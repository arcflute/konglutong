"""Local, compare-and-swap SQLite state and explicit atomic proposal adoption."""
from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

from .analysis import check_joint_tasks
from .verification import verify_candidate
from .world import generate_constraints
from .contracts import validate_world


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def _reservation_snapshot(report):
    """Persist the same complete resource ledger used by the joint reviewer.

    Initial or event-invalidated plans may conflict; preserving their actual
    occupancy is necessary. Check status is recorded separately from the rows,
    and does not imply that an initial/current plan was approved for execution.
    """
    return {'schema_version':'reservation-snapshot-v2',
            'reservations':copy.deepcopy(report['reservations']),
            'joint_check_status':report['status'],'checks_completed':not report['unchecked'],
            'unchecked':copy.deepcopy(report['unchecked']),
            'scope':'pads, named ground waiting areas, and modeled same-aircraft turnaround/charging occupancy'}


class PlanStore:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.path=str(path)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS current_state (id INTEGER PRIMARY KEY CHECK(id=1), world TEXT NOT NULL, reservations TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, state_revision INTEGER NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS session_checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL, state_revision INTEGER NOT NULL, payload TEXT NOT NULL)')

    def _connect(self):
        connection=sqlite3.connect(self.path,timeout=10)
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    def _current(self,db):
        row=db.execute('SELECT world FROM current_state WHERE id=1').fetchone()
        if not row:raise ValueError('store_not_initialized')
        return json.loads(row[0])

    def initialize(self,world):
        validate_world(world)
        updated=copy.deepcopy(world)
        updated.setdefault('reservation_version',1)
        updated.setdefault('state_revision',1)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM current_state').fetchone():raise ValueError('store_already_initialized')
            reservations=_reservation_snapshot(check_joint_tasks(updated['tasks'],updated))
            db.execute('INSERT INTO current_state VALUES(1,?,?)',(_json(updated),_json(reservations)))
            db.execute('INSERT INTO history(kind,payload) VALUES(?,?)',('initialized',_json({'after':updated})))

    def current_world(self):
        with self._connect() as db:return self._current(db)

    def current_reservations(self):
        with self._connect() as db:
            world=self._current(db)
            stored=json.loads(db.execute('SELECT reservations FROM current_state WHERE id=1').fetchone()[0])
            if isinstance(stored,list):
                stored={'schema_version':'legacy-pad-only','reservations':stored,'checks_completed':False,
                        'joint_check_status':'not_checked','unchecked':[{'reason':'legacy_snapshot_omits_waiting_and_turnaround_occupancy'}]}
            return {'reservation_version':world.get('reservation_version',1),**stored}

    def save_candidate(self,candidate):
        payload=_json(candidate)
        with self._connect() as db:
            existing=db.execute('SELECT payload FROM proposals WHERE id=?',(candidate['id'],)).fetchone()
            if existing and existing[0]!=payload:raise ValueError('candidate_id_reused_with_changed_content')
            db.execute('INSERT OR IGNORE INTO proposals VALUES(?,?)',(candidate['id'],payload))

    def get_candidate(self,identifier):
        with self._connect() as db:
            row=db.execute('SELECT payload FROM proposals WHERE id=?',(identifier,)).fetchone()
            if row is None:raise ValueError('candidate_not_found')
            return json.loads(row[0])

    def save_decision(self,result):
        with self._connect() as db:
            db.execute('INSERT INTO decisions(state_revision,payload) VALUES(?,?)',
                       (result['world'].get('state_revision',1),_json(result)))

    def history(self):
        with self._connect() as db:
            return [{'id':row[0],'kind':row[1],**json.loads(row[2])}
                    for row in db.execute('SELECT id,kind,payload FROM history ORDER BY id')]

    def decisions(self):
        with self._connect() as db:
            return [{'id':row[0],'state_revision':row[1],'result':json.loads(row[2])}
                    for row in db.execute('SELECT id,state_revision,payload FROM decisions ORDER BY id')]

    def checkpoint(self,binding,payload,expected_revision):
        """Persist pending reviews and budgets together under the current revision.

        A world update may commit before a checkpoint if the process crashes.
        Recovery then invalidates the cached result; it never reuses a mismatched
        decision. Normal completed API calls checkpoint before returning.
        """
        from .recovery import assert_binding
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            world=self._current(db)
            if world.get('state_revision',1)!=expected_revision:raise ValueError('checkpoint_state_revision_changed')
            previous=db.execute('SELECT binding FROM session_checkpoint WHERE id=1').fetchone()
            if previous:assert_binding(json.loads(previous[0]),binding)
            result=payload.get('result')
            if result is not None and result.get('world')!=world:raise ValueError('checkpoint_result_world_mismatch')
            db.execute('INSERT OR REPLACE INTO session_checkpoint VALUES(1,?,?,?)',
                       (_json(binding),expected_revision,_json(payload)))
            self._after_checkpoint_write(db)

    def _after_checkpoint_write(self,db):
        """Fault-injection seam; not exposed as an API request option."""

    def load_checkpoint(self,binding):
        from .recovery import assert_binding
        with self._connect() as db:
            row=db.execute('SELECT binding,state_revision,payload FROM session_checkpoint WHERE id=1').fetchone()
            if row is None:return None
            assert_binding(json.loads(row[0]),binding)
            current=self._current(db)
            return {'state_revision':row[1],'current_revision':current.get('state_revision',1),'payload':json.loads(row[2])}

    def replace_world(self,world,expected_revision=None,*,task_additions=None):
        """Publish an event or clock update; never import another branch's plans."""
        validate_world(world)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=self._current(db)
            revision=old.get('state_revision',1)
            if expected_revision is not None and expected_revision!=revision:raise ValueError('state_revision_changed')
            if world['scenario_id']!=old['scenario_id'] or world['as_of_s']<old['as_of_s']:
                raise ValueError('world_update_cannot_switch_scenario_or_reverse_observation_time')
            if world['environment_version']<old['environment_version']:raise ValueError('environment_version_must_not_decrease')
            additions=list(task_additions or [])
            incoming={t['id']:t for t in world['tasks']}
            if additions:
                if len(set(additions))!=len(additions):raise ValueError('duplicate_imported_task_id')
                previous={t['id']:t for t in old['tasks']}
                incoming={t['id']:t for t in world['tasks']}
                if any(tid in previous or tid not in incoming for tid in additions):
                    raise ValueError('task_additions_must_be_new_declared_tasks')
                if set(incoming)!=set(previous)|set(additions) or any(incoming[tid]!=t for tid,t in previous.items()):
                    raise ValueError('import_cannot_modify_current_tasks')
                if any(incoming[tid]['trajectory'][0]['time_s']<world['as_of_s'] for tid in additions):
                    raise ValueError('cannot_import_already_started_task')
                if world['environment_version']<=old['environment_version']:
                    raise ValueError('task_import_requires_new_environment_version')
            if world['environment_version']==old['environment_version']:
                # A clock tick may change derived snapshots of scheduled events,
                # which must increment environment_version in the world engine.
                derived={'as_of_s','tasks','state_revision','reservation_version','information_gaps','plan_review_status'}
                changed=[key for key in set(old)|set(world) if key not in derived and old.get(key)!=world.get(key)]
                if changed:raise ValueError('environment_changed_without_version_increment:'+','.join(changed))
                if world['as_of_s']==old['as_of_s']:return copy.deepcopy(old)
            updated=copy.deepcopy(world)
            updated['tasks']=copy.deepcopy(old['tasks'])+[copy.deepcopy(incoming[tid]) for tid in additions]
            resource_changed=any(old.get(k)!=world.get(k) for k in ('pads','waiting_areas','aircraft_turnaround_model'))
            updated['reservation_version']=old.get('reservation_version',1)+int(bool(additions) or resource_changed)
            updated['state_revision']=revision+1
            updated['plan_review_status']='needs_recheck'
            reservations=_reservation_snapshot(check_joint_tasks(updated['tasks'],updated))
            db.execute('UPDATE current_state SET world=?,reservations=? WHERE id=1',(_json(updated),_json(reservations)))
            self._after_state_write(db)
            db.execute('INSERT INTO history(kind,payload) VALUES(?,?)',('environment_or_clock_update',_json({'before':old,'after':updated})))
            return updated

    def _after_state_write(self,db):
        """Transaction fault-injection seam for tests; production has no request flag."""

    def adopt(self,candidate,explicit=False):
        if explicit is not True:return {'status':'explicit_adoption_required','adopted':False}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            world=self._current(db)
            # This check is mandatory even when a legacy verifier is used.
            if candidate.get('base_reservation_version')!=world.get('reservation_version',1):
                return {'status':'needs_recheck','adopted':False,'reason':'reservation_version_changed_or_missing'}
            report=verify_candidate(candidate,world,generate_constraints(world))
            if report['status']!='conditionally_feasible':return {'status':report['status'],'adopted':False,'review':report}
            updated=copy.deepcopy(world)
            original={t['id']:t for t in world['tasks']}
            updated['tasks']=copy.deepcopy(candidate['tasks'])
            for task in updated['tasks']:
                if task['trajectory']!=original[task['id']]['trajectory']:
                    task['plan_version']=original[task['id']]['plan_version']+1
            updated['reservation_version']=world.get('reservation_version',1)+1
            updated['state_revision']=world.get('state_revision',1)+1
            joint=report.get('joint_report')
            if joint is None or 'reservations' not in joint or joint.get('unchecked'):
                return {'status':'not_checked','adopted':False,'reason':'complete_joint_reservation_report_missing_or_incomplete',
                        'unchecked':joint.get('unchecked',[]) if joint else []}
            # The mandatory verifier just built these rows inside this same
            # transaction. Plan version increments do not change their geometry.
            reservations=_reservation_snapshot(joint)
            updated['plan_review_status']='conditionally_checked'
            record={'candidate_id':candidate['id'],'explicit':True,
                    'before_plan_versions':{i:t['plan_version'] for i,t in original.items()},
                    'after_plan_versions':{t['id']:t['plan_version'] for t in updated['tasks']},
                    'environment_version':world['environment_version'],
                    'before_reservation_version':world.get('reservation_version',1),
                    'after_reservation_version':updated['reservation_version'],
                    'conditions':report.get('conditions',[]),'before':world,'after':updated}
            db.execute('UPDATE current_state SET world=?,reservations=? WHERE id=1',(_json(updated),_json(reservations)))
            self._after_state_write(db)
            db.execute('INSERT INTO history(kind,payload) VALUES(?,?)',('adopted',_json(record)))
            return {'status':'adopted','adopted':True,'record':record}
