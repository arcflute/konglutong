"""Cross-layer tests: imports, transactions, knowledge time, and actual ordering."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chengkong.web import SimulationApp
from chengkong.imports import StructuredImporter
from chengkong.pipeline import analyze_world
from chengkong.analysis import check_task
from chengkong.world import generate_constraints

ROOT=Path(__file__).resolve().parents[1]

class Round3IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=SimulationApp(ROOT/'examples/park_crane_synthetic.json',self.tmp.name,project_root=ROOT)
        self.scenario='end_unconfirmed'

    def test_import_review_accept_recompute_field_query_and_export(self):
        first=self.app.state(self.scenario);world=first['result']['world']
        review=self.app.import_material(self.scenario,self.app.template())
        self.assertEqual(review['outcome']['status'],'ready_for_review')
        self.assertEqual(review['state']['result']['world'],world)
        adopted=self.app.accept_import(self.scenario,review['outcome']['proposal_id'])
        self.assertTrue(adopted['outcome']['world_updated'])
        w=adopted['state']['result']['world']
        self.assertEqual(w['activities'][0]['planned_interval_s'][1],600)
        self.assertEqual(w['activities'][0]['completion_status'],'not_confirmed')
        self.assertEqual(w['tasks'],world['tasks'])
        self.assertTrue(adopted['state']['result']['candidates'])
        query=self.app.field_query(self.scenario,'lifting_1','planned_interval')
        self.assertEqual(query['outcome']['status'],'evidence_available')
        self.assertEqual(query['outcome']['answer'],[120,600])
        second=self.app.field_query(self.scenario,'lifting_1','planned_interval')
        self.assertEqual(second['outcome']['status'],'budget_exhausted')
        export=self.app.export(self.scenario)
        self.assertEqual(export['versions']['environment_version'],w['environment_version'])
        self.assertTrue(export['material_audit']);self.assertTrue(export['field_audit'])
        self.assertNotIn('scenario_definitions_for_simulator_only',json.dumps(export))
        kinds={row['kind'] for row in export['timeline']}
        self.assertTrue({'information_arrival','event_effective','planned_end'}<=kinds)
        self.assertNotIn('confirmed_completion',kinds)
        stale=self.app.adopt(self.scenario,first['result']['candidates'][0]['id'])
        self.assertFalse(stale['outcome']['adopted'])

    def test_unaccepted_material_field_evidence_requires_explicit_acceptance(self):
        first=self.app.state(self.scenario)['result']['world']
        reply=self.app.field_query(self.scenario,'lifting_1','planned_interval',content=self.app.template())
        self.assertEqual(reply['outcome']['status'],'evidence_available')
        self.assertEqual(reply['state']['result']['world'],first)
        self.assertEqual(reply['outcome']['answer'],[120,600])
        self.assertEqual(reply['import_review']['status'],'ready_for_review')
        applied=self.app.accept_import(self.scenario,reply['import_review']['proposal_id'])
        self.assertTrue(applied['outcome']['world_updated'])
        self.assertEqual(applied['state']['result']['world']['activities'][0]['planned_interval_s'],[120,600])

    def test_new_tasks_atomic_reservations_and_future_replay_persistence(self):
        store=self.app._session(self.scenario)['store'];old=store.current_world()
        source=json.loads((ROOT/'examples/imports/park_tasks_resources.json').read_text())
        service=StructuredImporter();proposal=service.prepare(old,source)
        self.assertEqual(proposal['status'],'ready_for_review',proposal)
        audit,new=service.accept(old,proposal['proposal_id'],True)
        ids=audit['task_additions'];self.assertTrue(ids)
        before=(store.current_world(),store.current_reservations(),store.history())
        def fail(_):raise RuntimeError('injected failure')
        with patch.object(store,'_after_state_write',fail):
            with self.assertRaisesRegex(RuntimeError,'injected'):
                store.replace_world(new,old['state_revision'],task_additions=ids)
        self.assertEqual((store.current_world(),store.current_reservations(),store.history()),before)
        changed=copy.deepcopy(new);changed['tasks'][0]['priority']+=1
        with self.assertRaisesRegex(ValueError,'cannot_modify_current_tasks'):
            store.replace_world(changed,old['state_revision'],task_additions=ids)
        committed=store.replace_world(new,old['state_revision'],task_additions=ids)
        self.assertEqual(len(committed['tasks']),len(old['tasks'])+len(ids))
        self.assertEqual(committed['reservation_version'],old['reservation_version']+1)
        after=self.app.advance(self.scenario,70)['result']['world']
        self.assertEqual(after['tasks'],committed['tasks'])
        self.assertEqual(after['pads'],committed['pads'])
        with self.assertRaisesRegex(ValueError,'state_revision_changed'):
            store.replace_world(new,old['state_revision'],task_additions=ids)

    def test_motion_preview_is_versioned_prediction_not_new_knowledge(self):
        app=SimulationApp(ROOT/'examples/motion_crane_synthetic.json',Path(self.tmp.name)/'motion',project_root=ROOT)
        before=app.state(self.scenario);view=app.view(self.scenario,150)
        self.assertEqual(view['temporal_status'],'prediction_from_current_known_rules')
        self.assertEqual(view['environment_version'],before['result']['world']['environment_version'])
        self.assertEqual({c['component'] for c in view['motion_snapshot']['facilities'][0]['components']},{'tower','boom','rope','load'})
        self.assertEqual(app.state(self.scenario)['result']['world'],before['result']['world'])
        app.advance(self.scenario,360)
        replay=app.replay(self.scenario,before['history'][0]['id'])
        self.assertEqual(replay['spatial_view']['known_as_of_s'],60)
        self.assertEqual(replay['result']['world'],before['result']['world'])
        self.assertNotIn('confirmed_completion',{row['kind'] for row in replay['timeline']})

    def test_budgeted_checks_execute_order_and_never_clear_unchecked(self):
        from chengkong.learning_round3_runtime import priority_checks
        world=self.app._session(self.scenario)['store'].current_world();constraints=generate_constraints(world)
        keys=[f'{t["id"]}:{c["id"]}' for t in world['tasks'] for c in constraints]
        class ReversePredictor:
            version='test-explicit-order'
            def sensitivity(self,*_):return {'pair_order':list(reversed(keys))}
        schedule=priority_checks(world,constraints,ReversePredictor(),max_pairs=1)
        self.assertEqual(schedule['executed'][0]['pair_id'],keys[-1])
        self.assertEqual(schedule['unchecked_pair_ids'],list(reversed(keys))[1:])
        self.assertTrue(all(r['status']=='not_checked' for r in schedule['reports'].values()))
        full=priority_checks(world,constraints,ReversePredictor())
        self.assertTrue(full['all_pairs_checked'])
        for task in world['tasks']:
            expected=check_task(task,constraints,world);actual=full['reports'][task['id']]
            self.assertEqual(expected['status'],actual['status'])
            self.assertEqual(sorted(expected['conflicts'],key=lambda c:c['constraint_id']),sorted(actual['conflicts'],key=lambda c:c['constraint_id']))
        result=analyze_world(world,checking_budget={'max_pairs':0},planning_budget={'max_orders':0,'max_searches':0})
        self.assertEqual(result['checking_schedule']['completed_pairs'],0)
        self.assertTrue(all(r['status']=='not_checked' for r in result['baseline_analysis'].values()))

if __name__=='__main__':unittest.main()
