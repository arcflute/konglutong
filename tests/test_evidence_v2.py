"""Real branch computations and evidence feedback with no invented answers."""
from copy import deepcopy
from pathlib import Path
import unittest

from chengkong.contracts import ContractError
from chengkong.evidence import generate_requests, query_evidence, apply_evidence_result
from chengkong.world import load_example, visible_world, advance_world, generate_constraints

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/park_crane_synthetic.json'


class EvidenceFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.raw = load_example(EXAMPLE)
        self.world = visible_world(self.raw, 'end_unconfirmed', 60)

    def test_conflict_sensitivity_and_decision_value_compare_actual_branches(self):
        sensitivity = generate_requests(self.world)[0]
        decision = generate_requests(self.world, source_layer='maintenance_decision')[0]
        self.assertEqual(sensitivity['trigger'], 'conflict_sensitivity_branch_change')
        self.assertEqual(decision['trigger'], 'maintenance_decision_branch_change')
        self.assertGreater(sensitivity['comparison']['classification_change_count'], 0)
        self.assertTrue(decision['comparison']['recommendation_changes'])
        self.assertIsNone(decision['comparison']['branch_probabilities'])
        self.assertFalse(decision['comparison']['hypothetical_boundary_is_evidence'])
        world = deepcopy(self.world)
        world['tasks'] = [world['tasks'][2]]
        self.assertEqual(generate_requests(world), [])
        self.assertEqual(generate_requests(world, source_layer='maintenance_decision'), [])

    def test_explicit_visible_source_updates_through_layer_one(self):
        request = generate_requests(self.world)[0]
        current = advance_world(self.world, 400)
        source = {'id': 'mock_site_log', 'kind': 'synthetic_evidence_source', 'records': [{
            'id': 'visible_completion', 'kind': 'activity_completion_confirmed', 'activity_id': 'lifting_1',
            'effective_at_s': 360, 'observed_at_s': 390, 'source_kind': 'synthetic_confirmation'}]}
        result = query_evidence(current, request, evidence_source=source)
        self.assertEqual(result['status'], 'resolved')
        self.assertFalse(result['world_updated'])
        self.assertEqual(current['activities'][0]['completion_status'], 'not_confirmed')
        updated = apply_evidence_result(current, result)
        self.assertEqual(updated['activities'][0]['completion_confirmed_at_s'], 360)
        self.assertGreater(updated['environment_version'], current['environment_version'])
        self.assertTrue(updated['facilities'][0]['installed'])
        self.assertEqual(updated['event_log'][-1]['event']['evidence_refs'], ['mock_site_log'])
        with self.assertRaises(ContractError):
            apply_evidence_result(updated, result)

    def test_candidate_branch_preference_does_not_override_authoritative_review(self):
        from chengkong.verification import verify_candidate
        world = deepcopy(self.world)
        world['tasks'] = [world['tasks'][1]]
        candidate = {'id': 'original_task2', 'tasks': deepcopy(world['tasks']),
                     'base_environment_version': world['environment_version'],
                     'base_plan_versions': {t['id']: t['plan_version'] for t in world['tasks']},
                     'scenario_id': world['scenario_id'], 'rule_version': world['rule_version'],
                     'model_version': world['model_version'], 'valid_interval_s': world['horizon_s'], 'changes': []}
        request = generate_requests(world, source_layer='maintenance_decision', candidates=[candidate])[0]
        preference = request['comparison']['preferred_candidate_by_branch']
        self.assertEqual(preference['unconfirmed_occupancy'], 'hold_no_verified_candidate')
        self.assertEqual(preference['hypothetical_completion'], 'original_task2')
        self.assertFalse(verify_candidate(candidate, world, generate_constraints(world))['feasible'])
        self.assertEqual(world['activities'][0]['completion_status'], 'not_confirmed')

    def test_future_source_timeout_and_empty_source_preserve_unknown(self):
        request = generate_requests(self.world)[0]
        source = {'id': 'mock', 'kind': 'synthetic_evidence_source', 'records': [{
            'id': 'future', 'kind': 'activity_completion_confirmed', 'activity_id': 'lifting_1',
            'effective_at_s': 360, 'observed_at_s': 360, 'source_kind': 'synthetic_confirmation'}]}
        future = query_evidence(self.world, request, evidence_source=source)
        self.assertEqual(future['status'], 'unknown')
        self.assertIsNone(future['answer'])
        self.assertEqual(future['evidence'], [])
        source['simulated_delay_s'] = 10
        timed_out = query_evidence(self.world, request, evidence_source=source, timeout_s=2)
        self.assertEqual(timed_out['status'], 'timeout')
        self.assertEqual(apply_evidence_result(self.world, timed_out), self.world)
        self.assertEqual(timed_out['query_cost'], 1)
        empty = query_evidence(self.world, request, evidence_source={'id': 'empty', 'kind': 'available_records', 'records': []})
        self.assertEqual(empty['status'], 'unknown')
        self.assertEqual(apply_evidence_result(self.world, empty), self.world)

    def test_conflicting_confirmations_are_not_arbitrarily_selected(self):
        request = generate_requests(self.world)[0]
        world = advance_world(self.world, 400)
        rows = [{'id': f'c{i}', 'kind': 'activity_completion_confirmed', 'activity_id': 'lifting_1',
                 'effective_at_s': t, 'observed_at_s': 390, 'source_kind': 'synthetic_confirmation'}
                for i, t in enumerate((350, 360))]
        result = query_evidence(world, request, evidence_source={'id': 'conflicting', 'kind': 'available_records', 'records': rows})
        self.assertEqual(result['status'], 'conflicting_evidence')
        self.assertEqual(apply_evidence_result(world, result), world)


if __name__ == '__main__':
    unittest.main()
