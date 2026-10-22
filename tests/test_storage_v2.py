"""SQLite transaction and optimistic concurrency checks with real candidates."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest

from chengkong.analysis import pad_reservations
from chengkong.pipeline import analyze_world
from chengkong.storage import PlanStore
from chengkong.world import load_example, visible_world, apply_event, advance_world

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/park_crane_synthetic.json'


class InjectedFailure(RuntimeError):
    pass


class StorageTransactionV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PlanStore(Path(self.temp.name) / 'plans.sqlite3')
        self.store.initialize(visible_world(load_example(EXAMPLE), 'end_unconfirmed', 60))
        self.world = self.store.current_world()

    def candidate(self):
        result = analyze_world(self.store.current_world(), planning_budget={'max_orders': 4, 'max_searches': 24, 'timeout_s': 3})
        candidates = [c for c in result['candidates'] if c['review']['feasible']]
        self.assertTrue(candidates, 'Synthetic baseline must yield an actually verified candidate')
        return candidates[0]

    def snapshot(self):
        return (self.store.current_world(), self.store.current_reservations(), self.store.history(), self.store.decisions())

    def test_explicit_adoption_atomically_updates_tasks_and_reservations(self):
        candidate = self.candidate()
        before = self.snapshot()
        self.assertEqual(self.store.adopt(candidate)['status'], 'explicit_adoption_required')
        self.assertEqual(self.snapshot(), before)
        result = self.store.adopt(candidate, explicit=True)
        self.assertTrue(result['adopted'])
        after = self.store.current_world()
        self.assertEqual(after['environment_version'], self.world['environment_version'])
        self.assertEqual(after['reservation_version'], self.world['reservation_version'] + 1)
        self.assertEqual(after['state_revision'], self.world['state_revision'] + 1)
        old = {t['id']: t for t in self.world['tasks']}
        proposed = {t['id']: t for t in candidate['tasks']}
        for task in after['tasks']:
            self.assertEqual(task['trajectory'], proposed[task['id']]['trajectory'])
            delta = int(task['trajectory'] != old[task['id']]['trajectory'])
            self.assertEqual(task['plan_version'], old[task['id']]['plan_version'] + delta)
        expected, unchecked = pad_reservations(after['tasks'], after)
        self.assertEqual(unchecked, [])
        self.assertEqual(self.store.current_reservations()['reservations'], expected)
        self.assertEqual(self.store.history()[-1]['after'], after)
        committed = self.snapshot()
        again = self.store.adopt(candidate, explicit=True)
        self.assertFalse(again['adopted'])
        self.assertEqual(again['reason'], 'reservation_version_changed_or_missing')
        self.assertEqual(self.snapshot(), committed)

    def test_fault_after_state_write_rolls_back_adoption_and_environment(self):
        candidate = self.candidate()
        before = self.snapshot()
        def fail(_db):
            raise InjectedFailure('intentional failure inside open transaction')
        self.store._after_state_write = fail
        with self.assertRaises(InjectedFailure):
            self.store.adopt(candidate, explicit=True)
        self.assertEqual(self.snapshot(), before)
        event = {'id': 'delay', 'kind': 'planned_end_changed', 'activity_id': 'lifting_1',
                 'new_planned_end_s': 600, 'effective_at_s': 60, 'observed_at_s': 60,
                 'source_kind': 'synthetic_record'}
        updated = apply_event(self.world, event)
        with self.assertRaises(InjectedFailure):
            self.store.replace_world(updated, expected_revision=self.world['state_revision'])
        self.assertEqual(self.snapshot(), before)

    def test_environment_and_reservation_versions_reject_stale_candidates(self):
        candidate = self.candidate()
        stale_reservation = deepcopy(candidate)
        stale_reservation['base_reservation_version'] = 0
        before = self.snapshot()
        result = self.store.adopt(stale_reservation, explicit=True)
        self.assertFalse(result['adopted'])
        self.assertEqual(result['reason'], 'reservation_version_changed_or_missing')
        self.assertEqual(self.snapshot(), before)
        changed = apply_event(self.world, {'id': 'update', 'kind': 'planned_end_changed', 'activity_id': 'lifting_1',
            'new_planned_end_s': 600, 'effective_at_s': 60, 'observed_at_s': 60, 'source_kind': 'synthetic_record'})
        self.store.replace_world(changed, expected_revision=self.world['state_revision'])
        new_snapshot = self.snapshot()
        result = self.store.adopt(candidate, explicit=True)
        self.assertFalse(result['adopted'])
        self.assertIn('base_environment_version', result['review']['version_mismatches'])
        self.assertEqual(self.snapshot(), new_snapshot)

    def test_state_revision_rejects_stale_writer_and_retains_adopted_tasks(self):
        peer = PlanStore(self.store.path)
        peer_read = peer.current_world()
        candidate = self.candidate()
        self.store.adopt(candidate, explicit=True)
        before = self.snapshot()
        stale_clock = advance_world(peer_read, 70)
        with self.assertRaisesRegex(ValueError, 'state_revision_changed'):
            peer.replace_world(stale_clock, expected_revision=peer_read['state_revision'])
        self.assertEqual(self.snapshot(), before)
        latest = peer.current_world()
        updated = advance_world(latest, 70)
        # Construction updates may carry old tasks; the store owns current tasks.
        updated['tasks'] = deepcopy(self.world['tasks'])
        peer.replace_world(updated, expected_revision=latest['state_revision'])
        self.assertEqual(peer.current_world()['tasks'], latest['tasks'])
        self.assertEqual(peer.current_reservations(), before[1])

    def test_saved_decisions_and_proposals_are_immutable(self):
        result = analyze_world(self.world, planning_budget={'max_orders': 4, 'max_searches': 24, 'timeout_s': 3})
        self.store.save_decision(result)
        decision = deepcopy(self.store.decisions()[0])
        candidate = result['candidates'][0]
        self.store.save_candidate(candidate)
        changed = deepcopy(candidate)
        changed['conditions'].append('different content under existing ID')
        with self.assertRaisesRegex(ValueError, 'candidate_id_reused_with_changed_content'):
            self.store.save_candidate(changed)
        self.assertEqual(self.store.get_candidate(candidate['id']), candidate)
        self.store.replace_world(advance_world(self.world, 80), expected_revision=self.world['state_revision'])
        self.assertEqual(self.store.decisions()[0], decision)
        self.assertEqual(decision['result']['world']['as_of_s'], 60)
        self.assertEqual(self.store.current_world()['as_of_s'], 80)

    def test_two_concurrent_writers_commit_exactly_one_revision(self):
        barrier = threading.Barrier(2)
        def writer():
            store = PlanStore(self.store.path)
            old = store.current_world()
            update = advance_world(old, 70)
            barrier.wait(timeout=5)
            try:
                store.replace_world(update, expected_revision=old['state_revision'])
                return 'committed'
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: writer(), range(2)))
        self.assertEqual(sorted(results), ['committed', 'state_revision_changed'])
        self.assertEqual(self.store.current_world()['state_revision'], 2)
        self.assertEqual(self.store.current_world()['as_of_s'], 70)
        self.assertEqual(len(self.store.history()), 2)


if __name__ == '__main__':
    unittest.main()
