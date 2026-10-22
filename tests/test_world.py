"""Focused checks for source-only evidence, disabled learning and event atomicity."""
from copy import deepcopy
from pathlib import Path
import unittest

from chengkong.contracts import ContractError, UnsupportedData
from chengkong.world import load_example, visible_world, apply_event, generate_constraints
from chengkong.evidence import generate_requests, query_evidence
from chengkong.learning import (capability_statuses, UntrainedSensitivityModel,
                               UntrainedCalibrator, UnconfiguredMultimodalParser)

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'park_crane_synthetic.json'


class EvidenceAndAdapterTests(unittest.TestCase):
    def setUp(self):
        self.raw = load_example(EXAMPLE)
        self.world = visible_world(self.raw, 'on_time_confirmed', 60)

    def test_hidden_confirmation_is_not_a_query_answer(self):
        request = generate_requests(self.world)[0]
        result = query_evidence(self.world, request)
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['answer'])
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['query_cost'], 1)
        self.assertEqual(self.world['activities'][0]['completion_status'], 'not_confirmed')

    def test_query_budget_deadline_and_version(self):
        request = generate_requests(self.world, max_queries=1, deadline_s=100)[0]
        self.assertEqual(query_evidence(self.world, request, queries_used=1)['status'], 'budget_exhausted')
        self.assertEqual(query_evidence(self.world, request, now_s=100)['status'], 'deadline_exceeded')
        next_world = visible_world(self.raw, 'on_time_confirmed', 400)
        self.assertEqual(query_evidence(next_world, request)['status'], 'stale_request')
        self.assertEqual(generate_requests(next_world), [])

    def test_existing_confirmation_is_traceable(self):
        request = generate_requests(self.world)[0]
        next_world = visible_world(self.raw, 'on_time_confirmed', 400)
        request['environment_version'] = next_world['environment_version']
        result = query_evidence(next_world, request)
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['answer']['completion_confirmed_at_s'], 360)
        self.assertEqual(result['evidence'][0]['source_ids'], ['source:finish_confirmed'])
        self.assertFalse(result['world_updated'])

    def test_disabled_models_have_no_predicted_accuracy_or_fake_scores(self):
        statuses = capability_statuses()
        self.assertEqual(statuses['vlm_llm_parser']['status'], 'not_configured')
        self.assertTrue(all(not r['trained'] for r in statuses.values()))
        features = [{'pair_id': 'later', 'has_unknown_fields': False},
                    {'pair_id': 'unknown', 'has_unknown_fields': True}]
        result = UntrainedSensitivityModel().predict(features, {})
        self.assertEqual(result['status'], 'not_trained')
        self.assertEqual(result['fallback']['ordered_pair_ids'], ['unknown', 'later'])
        self.assertIsNone(result['fallback']['scores'])
        self.assertFalse(result['fallback']['allows_check_skipping'])
        self.assertIsNone(UntrainedCalibrator().calibrate({}, {})['coverage'])
        self.assertEqual(UnconfiguredMultimodalParser().parse([], {})['candidate_fields'], [])

    def test_bad_event_never_partially_mutates_world(self):
        world = visible_world(self.raw, 'end_unconfirmed', 400)
        original = deepcopy(world)
        event = {'id': 'bad_end', 'observed_at_s': 400, 'effective_at_s': 400,
                 'kind': 'planned_end_changed', 'activity_id': 'lifting_1',
                 'new_planned_end_s': 100, 'source_kind': 'synthetic_record'}
        with self.assertRaises(ContractError):
            apply_event(world, event)
        self.assertEqual(world, original)

    def test_removal_is_separate_and_keeps_past_occupancy(self):
        world = visible_world(self.raw, 'on_time_confirmed', 400)
        event = {'id': 'remove_crane', 'observed_at_s': 400, 'effective_at_s': 400,
                 'kind': 'facility_removal_confirmed', 'facility_id': 'crane_1',
                 'source_kind': 'synthetic_confirmation'}
        removed = apply_event(world, event)
        intervals = [c['interval_s'] for c in generate_constraints(removed) if c['object_id'] == 'crane_1']
        self.assertEqual(intervals, [[0, 400], [0, 400]])
        self.assertTrue(world['facilities'][0]['installed'])
        self.assertFalse(removed['facilities'][0]['installed'])

    def test_future_effective_event_is_explicitly_unsupported(self):
        event = {'id': 'early_prediction', 'observed_at_s': 60, 'effective_at_s': 400,
                 'kind': 'activity_completion_confirmed', 'activity_id': 'lifting_1',
                 'source_kind': 'synthetic_confirmation', 'facility_removed': False}
        with self.assertRaises(UnsupportedData):
            apply_event(self.world, event)


if __name__ == '__main__':
    unittest.main()
