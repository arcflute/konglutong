"""Cached pair aggregation is compared with uncached full calculation."""
from copy import deepcopy
from pathlib import Path
import unittest

from chengkong.analysis import check_task, check_joint_tasks
from chengkong.incremental import DependencyEngine
from chengkong.world import load_example, visible_world, generate_constraints, apply_event, advance_world

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/park_crane_synthetic.json'


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.world = visible_world(load_example(EXAMPLE), 'end_unconfirmed', 400)
        self.engine = DependencyEngine()

    def assertFullEqual(self, world, result):
        constraints = generate_constraints(world)
        self.assertEqual(result['baseline_analysis'], {t['id']: check_task(t, constraints, world) for t in world['tasks']})
        self.assertEqual(result['joint_analysis'], check_joint_tasks(world['tasks'], world))

    def test_same_version_hits_cache_and_equals_full(self):
        first = self.engine.analyze(self.world)
        second = self.engine.analyze(self.world)
        self.assertFullEqual(self.world, second)
        self.assertGreater(first['cache']['pairs_recomputed'], 0)
        self.assertEqual(second['cache']['pairs_recomputed'], 0)
        self.assertEqual(second['cache']['pairs_reused'], first['cache']['pairs_recomputed'])

    def test_changed_geometry_rechecks_new_associations_and_reuses_others(self):
        self.engine.analyze(self.world)
        t = self.world['tasks'][2]
        p = t['trajectory'][1]['position_m']
        world = apply_event(self.world, {'id': 'move', 'kind': 'facility_geometry_changed', 'facility_id': 'crane_1',
                  'observed_at_s': 400, 'effective_at_s': 100, 'source_kind': 'synthetic_record',
                  'physical_geometry': [{'type': 'aabb', 'min_m': [x - 10 for x in p], 'max_m': [x + 10 for x in p]}]})
        candidate = {'id': 'old', 'base_environment_version': self.world['environment_version'],
                     'base_plan_versions': {t['id']: t['plan_version'] for t in world['tasks']}, 'tasks': world['tasks']}
        result = self.engine.update(world, candidates=[candidate])
        self.assertFullEqual(world, result)
        self.assertGreater(result['cache']['pairs_reused'], 0)
        self.assertGreater(result['cache']['pairs_recomputed'], 0)
        self.assertEqual(result['invalidated_candidate_ids'], ['old'])
        self.assertTrue(any(c['object_id'] == 'crane_1' for c in result['baseline_analysis'][t['id']]['conflicts']))

    def test_unknown_geometry_task_change_and_clock_match_full(self):
        self.engine.analyze(self.world)
        world = deepcopy(self.world)
        world['buildings'][0]['geometry'] = None
        world['environment_version'] += 1
        world['tasks'][0]['protection_radius_m'] += 1
        world['tasks'][0]['plan_version'] += 1
        result = self.engine.analyze(world)
        self.assertFullEqual(world, result)
        self.assertTrue(any(r['unchecked'] for r in result['baseline_analysis'].values()))
        advanced = advance_world(world, 450)
        cached = self.engine.analyze(advanced)
        self.assertFullEqual(advanced, cached)
        self.assertEqual(cached['cache']['pairs_recomputed'], 0)


if __name__ == '__main__':
    unittest.main()
