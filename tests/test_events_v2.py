"""Hand-specified valid/knowledge time and occupancy invariants."""
from copy import deepcopy
from pathlib import Path
import unittest

from chengkong.contracts import ContractError
from chengkong.world import load_example, visible_world, apply_event, advance_world, generate_constraints

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/park_crane_synthetic.json'


def event(identifier, kind, effective, observed=None, **payload):
    return dict(id=identifier, kind=kind, effective_at_s=effective,
                observed_at_s=effective if observed is None else observed,
                source_kind='synthetic_test_record', **payload)


class BitemporalEventsTests(unittest.TestCase):
    def setUp(self):
        self.raw = load_example(EXAMPLE)
        self.world = visible_world(self.raw, 'end_unconfirmed', 400)

    def test_geometry_change_preserves_old_and_new_intervals(self):
        new_geometry = [{'type': 'aabb', 'min_m': [1, 1, 0], 'max_m': [2, 2, 10]}]
        updated = apply_event(self.world, event('geometry', 'facility_geometry_changed', 300, 400,
                              facility_id='crane_1', physical_geometry=new_geometry))
        rows = [c for c in generate_constraints(updated) if c['object_id'] == 'crane_1']
        self.assertEqual([c['interval_s'] for c in rows], [[0, 300], [0, 300], [300, 900]])
        self.assertEqual(rows[-1]['geometry'], new_geometry[0])
        self.assertEqual(self.world['facilities'][0]['physical_geometry'], self.raw['observed_snapshot']['facilities'][0]['physical_geometry'])

    def test_late_geometry_rebuilds_valid_time_without_rewriting_decision(self):
        first_geometry = [{'type': 'aabb', 'min_m': [10, 10, 0], 'max_m': [20, 20, 10]}]
        later_geometry = [{'type': 'aabb', 'min_m': [30, 30, 0], 'max_m': [40, 40, 10]}]
        earlier = apply_event(self.world, event('newer_valid', 'facility_pose_changed', 300, 350,
                             facility_id='crane_1', physical_geometry=later_geometry, pose={'angle_deg': 90}))
        recorded_decision = deepcopy(earlier)
        late = apply_event(earlier, event('late_older', 'facility_geometry_changed', 200, 400,
                           facility_id='crane_1', physical_geometry=first_geometry))
        rows = [c for c in generate_constraints(late) if c['object_id'] == 'crane_1']
        self.assertEqual([c['interval_s'] for c in rows], [[0, 200], [0, 200], [200, 300], [300, 900]])
        self.assertEqual(late['facilities'][0]['physical_geometry'], later_geometry)
        self.assertEqual(earlier, recorded_decision)
        self.assertEqual(late['event_log'][0], recorded_decision['event_log'][0])
        self.assertTrue(late['event_log'][-1]['late_information'])

    def test_late_plan_update_before_completion_does_not_reopen_activity(self):
        completed = apply_event(self.world, event('complete', 'activity_completion_confirmed', 360, 370, activity_id='lifting_1'))
        late = apply_event(completed, event('late_plan', 'planned_end_changed', 300, 400,
                           activity_id='lifting_1', new_planned_end_s=600))
        self.assertEqual(late['activities'][0]['planned_interval_s'][1], 600)
        self.assertEqual(late['activities'][0]['completion_status'], 'confirmed')
        self.assertEqual(next(c for c in generate_constraints(late) if c['kind'] == 'activity')['interval_s'], [120, 360])

    def test_pause_retains_conservative_occupancy_then_resume(self):
        paused = apply_event(self.world, event('pause', 'activity_paused', 250, activity_id='lifting_1'))
        self.assertEqual(paused['activities'][0]['operational_status'], 'paused')
        rows = [c for c in generate_constraints(paused) if c['kind'] == 'activity']
        self.assertEqual([r['interval_s'] for r in rows], [[120, 250], [250, 900]])
        self.assertEqual(rows[0]['geometry'], rows[1]['geometry'])
        self.assertEqual(rows[1]['pause_occupancy_policy'], 'retain_conservative_envelope')
        resumed = apply_event(paused, event('resume', 'activity_resumed', 300, activity_id='lifting_1'))
        self.assertEqual(resumed['activities'][0]['operational_status'], 'active')
        self.assertEqual(resumed['activities'][0]['completion_status'], 'not_confirmed')

    def test_new_facility_and_activity_start_resolve_same_time_dependencies(self):
        facility = event('z_facility', 'facility_added', 200, facility={
            'id': 'mobile_1', 'type': 'test_box', 'physical_geometry': [
                {'type': 'aabb', 'min_m': [100, 100, 0], 'max_m': [110, 110, 10]}]})
        activity = event('a_activity', 'activity_started', 200, activity={
            'id': 'work_2', 'facility_id': 'mobile_1', 'planned_interval_s': [200, 300],
            'activity_envelope': None})
        initial = visible_world(self.raw, 'end_unconfirmed', 60)
        updated = advance_world(initial, 250, [activity, facility])
        rows = [c for c in generate_constraints(updated) if c['object_id'] in ('mobile_1', 'work_2')]
        self.assertEqual([c['interval_s'] for c in rows], [[200, 900], [200, 900]])
        self.assertEqual(rows[-1]['geometry_status'], 'unknown')
        self.assertEqual(len(updated['activities']), 2)

    def test_pending_events_cannot_shrink_present_occupancy(self):
        pending = apply_event(self.world, event('future_completion', 'activity_completion_confirmed', 500, 400, activity_id='lifting_1'))
        before = advance_world(pending, 499)
        after = advance_world(pending, 500)
        self.assertEqual(before['activities'][0]['completion_status'], 'not_confirmed')
        self.assertEqual(after['activities'][0]['completion_confirmed_at_s'], 500)
        self.assertEqual(after['pending_events'], [])
        self.assertGreater(after['environment_version'], before['environment_version'])
        self.assertEqual(pending['event_log'][0], after['event_log'][0])

    def test_invalid_future_event_rejected_at_receipt(self):
        before = deepcopy(self.world)
        with self.assertRaises(ContractError):
            apply_event(self.world, event('bad_future_plan', 'planned_end_changed', 500, 400,
                        activity_id='lifting_1', new_planned_end_s=100))
        with self.assertRaises(ContractError):
            apply_event(self.world, event('unknown_future_object', 'activity_paused', 500, 400,
                        activity_id='missing_activity'))
        self.assertEqual(self.world, before)

    def test_event_replay_preserves_adopted_task_and_deduplicates(self):
        self.world['tasks'][0]['plan_version'] += 1
        self.world['tasks'][0]['operator_note'] = 'adopted plan remains current'
        incoming = event('delay', 'planned_end_changed', 300, activity_id='lifting_1', new_planned_end_s=600)
        updated = apply_event(self.world, incoming)
        self.assertEqual(updated['tasks'], self.world['tasks'])
        self.assertEqual(apply_event(updated, incoming), updated)
        modified = deepcopy(incoming)
        modified['new_planned_end_s'] = 650
        with self.assertRaises(ContractError):
            apply_event(updated, modified)

    def test_failed_batch_does_not_leave_partial_update(self):
        before = deepcopy(self.world)
        with self.assertRaises(ContractError):
            advance_world(self.world, 450, [event('valid_plan', 'planned_end_changed', 400,
                          activity_id='lifting_1', new_planned_end_s=600),
                          event('invalid_remove', 'facility_removal_confirmed', 450, facility_id='crane_1')])
        self.assertEqual(self.world, before)


if __name__ == '__main__':
    unittest.main()
