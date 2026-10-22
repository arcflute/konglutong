"""Versioned strategy switching and decision explanations from live computation."""
from pathlib import Path
import tempfile
import unittest
from chengkong.web import SimulationApp

ROOT=Path(__file__).resolve().parents[1]

class Round4PresentationTests(unittest.TestCase):
    def test_changed_budget_recommendation_does_not_reuse_immutable_candidate_id(self):
        from chengkong.world import load_example,visible_world
        from chengkong.pipeline import analyze_world
        from chengkong.storage import PlanStore
        w=visible_world(load_example(ROOT/'examples/round4/dynamic_window.json'),'dynamic_window',0)
        limited=analyze_world(w,planning_budget={'max_searches':1})
        complete=analyze_world(w)
        a=next(c for c in limited['candidates'] if c['kind']=='delay')
        b=next(c for c in complete['candidates'] if c['kind']=='delay')
        self.assertEqual(a['tasks'],b['tasks'])
        self.assertTrue(a['recommended']);self.assertFalse(b['recommended'])
        self.assertNotEqual(a['id'],b['id'])
        with tempfile.TemporaryDirectory() as temp:
            store=PlanStore(Path(temp)/'state.db');store.initialize(w)
            store.save_candidate(a);store.save_candidate(b)
            self.assertEqual(store.get_candidate(a['id']),a)
            self.assertEqual(store.get_candidate(b['id']),b)

    def test_policy_invalidation_windows_export_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            app=SimulationApp(ROOT/'examples/round4/dynamic_window.json',temp,project_root=ROOT)
            try:
                state=app.state('dynamic_window');r=state['result']
                self.assertEqual(r['world']['as_of_s'],0)
                self.assertEqual(r['world']['planning_policy']['mode'],'dynamic_windows')
                c=next(c for c in r['candidates'] if c['review']['feasible'] and c['kind']=='delay')
                explanation=next(x for x in r['maintenance_explanations']['candidates'] if x['candidate_id']==c['id'])
                self.assertTrue(explanation['actions'][0]['requirements_preserved'])
                self.assertTrue(any(a['planning_explanation'] for a in explanation['actions']))
                timeline=app.operation_timeline('dynamic_window',c['id'])
                self.assertTrue(any(x['kind']=='ground_wait' and x['plan']=='candidate' for x in timeline['tracks']))
                newer=app.policy('dynamic_window','conservative','keep_detour')
                self.assertGreater(newer['result']['world']['environment_version'],r['world']['environment_version'])
                self.assertFalse(app.adopt('dynamic_window',c['id'])['outcome']['adopted'])
                exported=app.export('dynamic_window')
                self.assertEqual(exported['planning_policy']['mode'],'conservative')
                self.assertIn('timings',exported)
            finally:app.close()
            app=SimulationApp(ROOT/'examples/round4/dynamic_window.json',temp,project_root=ROOT,initial_policy={'mode':'dynamic_windows'})
            try:
                restored=app.state('dynamic_window')
                self.assertEqual(app.active_scenario,'dynamic_window')
                self.assertEqual(restored['result']['world']['planning_policy']['mode'],'conservative')
                self.assertEqual(restored['recovery_status']['status'],'restored')
            finally:app.close()

    def test_unknown_failure_has_evidence_not_infeasibility_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            app=SimulationApp(ROOT/'examples/park_crane_synthetic.json',temp,project_root=ROOT)
            try:
                app.state('end_unconfirmed')
                state=app.advance('end_unconfirmed',600)
                r=state['result']
                self.assertFalse(r['candidates'])
                self.assertFalse(r['failure_diagnosis']['global_infeasibility_proven'])
                self.assertIn('task_requirements',r['failure_diagnosis'])
                self.assertEqual(r['learning_applicability']['default_strategy'],'rule')
                self.assertGreater(r['timings']['total_s'],0)
            finally:app.close()
