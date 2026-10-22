"""Local SQLite plan adoption: explicit, version checked, atomic with reservations."""
import copy
import json
import sqlite3
from pathlib import Path

from .analysis import pad_reservations
from .verification import verify_candidate
from .world import generate_constraints
from .contracts import validate_world


class PlanStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.path=str(path)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS current_state (id INTEGER PRIMARY KEY CHECK(id=1), world TEXT NOT NULL, reservations TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def _connect(self):
        return sqlite3.connect(self.path,timeout=10)

    def initialize(self,world):
        validate_world(world)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM current_state').fetchone():
                raise ValueError('store_already_initialized')
            reservations,_=pad_reservations(world['tasks'],world)
            db.execute('INSERT INTO current_state VALUES(1,?,?)',(json.dumps(world),json.dumps(reservations)))
            db.execute('INSERT INTO history(kind,payload) VALUES(?,?)',('initialized',json.dumps(world)))

    def current_world(self):
        with self._connect() as db:
            row=db.execute('SELECT world FROM current_state WHERE id=1').fetchone()
            if not row:raise ValueError('store_not_initialized')
            return json.loads(row[0])

    def save_candidate(self,candidate):
        with self._connect() as db:
            db.execute('INSERT INTO proposals VALUES(?,?)',(candidate['id'],json.dumps(candidate)))

    def replace_world(self,world):
        """Publish a validated evidence update; retain current plans and history."""
        validate_world(world)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT world FROM current_state WHERE id=1').fetchone()
            if not row:raise ValueError('store_not_initialized')
            old=json.loads(row[0])
            if world['scenario_id']!=old['scenario_id'] or world['as_of_s']<old['as_of_s']:
                raise ValueError('world_update_cannot_switch_scenario_or_reverse_observation_time')
            if world['environment_version']<=old['environment_version']:
                raise ValueError('environment_version_must_increase')
            updated=copy.deepcopy(world);updated['tasks']=old['tasks'];updated['plan_review_status']='needs_recheck'
            reservations,_=pad_reservations(updated['tasks'],updated)
            db.execute('UPDATE current_state SET world=?, reservations=? WHERE id=1',(json.dumps(updated),json.dumps(reservations)))
            db.execute('INSERT INTO history(kind,payload) VALUES(?,?)',('environment_update',json.dumps({'before':old,'after':updated})))

    def adopt(self,candidate,explicit=False):
        if not explicit:return {'status':'explicit_adoption_required','adopted':False}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT world FROM current_state WHERE id=1').fetchone()
            if not row:raise ValueError('store_not_initialized')
            world=json.loads(row[0])
            report=verify_candidate(candidate,world,generate_constraints(world))
            if report['status']!='conditionally_feasible':
                return {'status':report['status'],'adopted':False,'review':report}
            updated=copy.deepcopy(world);original={t['id']:t for t in world['tasks']}
            updated['tasks']=copy.deepcopy(candidate['tasks'])
            for task in updated['tasks']:
                if task['trajectory']!=original[task['id']]['trajectory']:
                    task['plan_version']=original[task['id']]['plan_version']+1
            reservations,_=pad_reservations(updated['tasks'],updated)
            updated['plan_review_status']='conditionally_checked'
            record={'candidate_id':candidate['id'],'explicit':True,'before_plan_versions':{i:t['plan_version'] for i,t in original.items()},'after_plan_versions':{t['id']:t['plan_version'] for t in updated['tasks']},'environment_version':world['environment_version'],'conditions':report.get('conditions',[])}
            db.execute('UPDATE current_state SET world=?,reservations=? WHERE id=1',(json.dumps(updated),json.dumps(reservations)))
            db.execute('INSERT INTO history(kind,payload) VALUES(?,?)',('adopted',json.dumps(record)))
            return {'status':'adopted','adopted':True,'record':record}
