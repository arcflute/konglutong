"""End-to-end computation and explicit local adoption, using synthetic inputs."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from chengkong.pipeline import run_analysis
from chengkong.planning import search_task
from chengkong.analysis import check_task
from chengkong.storage import PlanStore
from chengkong.verification import verify_candidate
from chengkong.world import apply_event, generate_constraints, load_example, visible_world


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "park_crane_synthetic.json"


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = load_example(EXAMPLE)
        cls.result = run_analysis(cls.raw)

    def test_actual_end_to_end_has_reviewed_candidate_and_serializable_output(self):
        result = self.result
        self.assertEqual(result["schema_version"], "chengkong-result-v2")
        self.assertEqual(result["data_kind"], "synthetic")
        self.assertFalse(result["is_real_case"])
        self.assertFalse(result["current_plan_mutated"])
        self.assertEqual(set(result["baseline_analysis"]), {"task_1", "task_2", "task_3"})
        self.assertTrue(result["constraints"])
        feasible = [c for c in result["candidates"] if c["status"] == "conditionally_feasible"]
        self.assertTrue(feasible, "the supplied scenario must produce a genuinely checked proposal")
        for candidate in feasible:
            self.assertEqual({t["id"] for t in candidate["tasks"]}, {"task_1", "task_2", "task_3"})
            review = verify_candidate(candidate, result["world"], result["constraints"])
            self.assertTrue(review["checks_completed"])
            self.assertTrue(review["feasible"])
            self.assertEqual(review["joint_report"]["status"], "clear")
        json.dumps(result, allow_nan=False)

    def test_continuous_conflicts_match_independently_hand_computed_intervals(self):
        # x(t)=100+10*(t-102); the protected cylinder spans x=[437,563].
        # Task 2 repeats this horizontal leg 260 s later. Both endpoints alone
        # are outside the cylinder, so this also detects waypoint-only checking.
        for task_id, expected in (("task_1", [135.7, 148.3]), ("task_2", [395.7, 408.3])):
            with self.subTest(task=task_id):
                report = self.result["baseline_analysis"][task_id]
                conflicts = [c for c in report["conflicts"] if c["object_id"] == "lifting_1"]
                self.assertEqual(len(conflicts), 1)
                self.assertEqual(conflicts[0]["segment_index"], 1)
                for actual, hand_computed in zip(conflicts[0]["interval_s"], expected):
                    self.assertAlmostEqual(actual, hand_computed, places=7)
                self.assertTrue(report["checks_completed"])
        self.assertEqual(self.result["baseline_analysis"]["task_3"]["status"], "clear")

    def test_generation_is_deterministic_and_keeps_input_and_unaffected_plan(self):
        original = deepcopy(self.raw)
        result = run_analysis(self.raw)
        self.assertEqual(self.raw, original)
        self.assertEqual(result, self.result)
        self.assertEqual(result["world"]["tasks"], original["observed_snapshot"]["tasks"])
        old_tasks = {t["id"]: t for t in original["observed_snapshot"]["tasks"]}
        for candidate in result["candidates"]:
            for task in candidate["tasks"]:
                self.assertEqual(task["mandatory_services"], old_tasks[task["id"]]["mandatory_services"])
                self.assertEqual(task["plan_version"], old_tasks[task["id"]]["plan_version"])
                if task["id"] == "task_3":
                    self.assertEqual(task, old_tasks["task_3"])

    def test_released_confirmation_changes_analysis_but_never_rewrites_started_tasks(self):
        normal = run_analysis(self.raw, "on_time_confirmed", 400)
        delayed = run_analysis(self.raw, "delayed_confirmed", 400)
        unknown = run_analysis(self.raw, "end_unconfirmed", 400)
        self.assertEqual(normal["baseline_analysis"]["task_2"]["status"], "clear")
        self.assertEqual(delayed["baseline_analysis"]["task_2"]["status"], "conflict")
        self.assertEqual(unknown["baseline_analysis"]["task_2"]["status"], "conflict")
        for result in (normal, delayed, unknown):
            self.assertEqual(result["candidates"], [])
            self.assertEqual(result["planning_status"], "needs_operational_review_started_task")
            self.assertFalse(result["current_plan_mutated"])

    def test_unknown_or_unsupported_geometry_never_becomes_clear(self):
        for geometry in (None, {"type": "unimplemented_mesh"}):
            with self.subTest(geometry=geometry):
                raw = deepcopy(self.raw)
                raw["observed_snapshot"]["buildings"][0]["geometry"] = geometry
                result = run_analysis(raw)
                self.assertEqual(result["baseline_analysis"]["task_3"]["status"], "not_checked")
                self.assertFalse(result["baseline_analysis"]["task_3"]["checks_completed"])
                self.assertFalse(any(c["status"] == "conditionally_feasible" for c in result["candidates"]))

    def test_unknown_completion_produces_source_limited_requests_and_untrained_statuses(self):
        requests = self.result["evidence_requests"]
        self.assertTrue(requests)
        request = next(r for r in requests if r["object_id"] == "lifting_1")
        self.assertEqual(set(request["related_task_ids"]), {"task_1", "task_2"})
        self.assertEqual(request["field"], "completion_status")
        statuses = json.dumps(self.result["learning_status"])
        self.assertIn("not_configured", statuses)
        self.assertIn("not_trained", statuses)
        self.assertNotIn('"trained": true', statuses)
        result = next(r for r in self.result["evidence_results"] if r["request_id"] == request["id"])
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["evidence"], [])
        self.assertFalse(result["world_updated"])

    def test_old_candidate_cannot_rewrite_a_task_after_it_has_started(self):
        candidate = self.result["candidates"][0]
        later = visible_world(self.raw, "end_unconfirmed", 100)
        self.assertEqual(later["environment_version"], candidate["base_environment_version"])
        result = verify_candidate(candidate, later, generate_constraints(later))
        self.assertFalse(result["feasible"])
        self.assertIn(result["status"], ("rejected", "needs_recheck", "not_checked"))


class AdoptionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.raw = load_example(EXAMPLE)
        self.result = run_analysis(self.raw)
        self.candidate = next(c for c in self.result["candidates"] if c["status"] == "conditionally_feasible")
        self.store = PlanStore(Path(self.directory.name) / "plans.sqlite3")
        self.store.initialize(self.result["world"])

    def test_storing_and_reviewing_candidate_does_not_adopt_it(self):
        before = self.store.current_world()
        self.store.save_candidate(self.candidate)
        outcome = self.store.adopt(self.candidate)
        self.assertFalse(outcome["adopted"])
        self.assertEqual(outcome["status"], "explicit_adoption_required")
        self.assertEqual(self.store.current_world(), before)

    def test_explicit_adoption_increments_changed_plans_and_rejects_repeat(self):
        outcome = self.store.adopt(self.candidate, explicit=True)
        self.assertTrue(outcome["adopted"])
        self.assertEqual(outcome["status"], "adopted")
        current = self.store.current_world()
        versions = {t["id"]: t["plan_version"] for t in current["tasks"]}
        self.assertEqual(versions, {"task_1": 2, "task_2": 2, "task_3": 1})
        repeated = self.store.adopt(self.candidate, explicit=True)
        self.assertFalse(repeated["adopted"])
        self.assertEqual(repeated["status"], "needs_recheck")
        self.assertEqual(self.store.current_world(), current)

    def test_evidence_update_preserves_current_tasks_and_invalidates_old_proposal(self):
        # A newly supplied confirmation updates the same decision branch; a
        # branch label switch is not an admissible live-store evidence update.
        new_world = visible_world(self.raw, "end_unconfirmed", 400)
        event = self.raw["scenario_definitions_for_simulator_only"][0]["events"][0]
        new_world = apply_event(new_world, event)
        old_tasks = deepcopy(self.store.current_world()["tasks"])
        self.store.replace_world(new_world)
        updated = self.store.current_world()
        self.assertEqual(updated["tasks"], old_tasks)
        self.assertEqual(updated["plan_review_status"], "needs_recheck")
        outcome = self.store.adopt(self.candidate, explicit=True)
        self.assertFalse(outcome["adopted"])
        self.assertEqual(outcome["status"], "needs_recheck")
        self.assertEqual(self.store.current_world(), updated)

    def test_tampered_service_list_cannot_make_adoption_feasible(self):
        tampered = deepcopy(self.candidate)
        tampered["tasks"][0]["mandatory_services"] = []
        before = self.store.current_world()
        outcome = self.store.adopt(tampered, explicit=True)
        self.assertFalse(outcome["adopted"])
        self.assertEqual(outcome["status"], "rejected")
        self.assertEqual(self.store.current_world(), before)

    def test_a_different_simulator_branch_cannot_replace_live_store_state(self):
        before = self.store.current_world()
        other_branch = visible_world(self.raw, "on_time_confirmed", 400)
        with self.assertRaises(ValueError):
            self.store.replace_world(other_branch)
        self.assertEqual(self.store.current_world(), before)


class SippGroundWaitIntegrationTests(unittest.TestCase):
    def setUp(self):
        raw = load_example(EXAMPLE)
        self.world = visible_world(raw, "end_unconfirmed", 60)
        self.constraints = generate_constraints(self.world)
        self.task = self.world["tasks"][0]
        self.reservations = self.world["tasks"][1:]

    def test_ground_wait_is_in_trajectory_and_cost_before_safe_detour(self):
        result = search_task(self.task, self.world, self.constraints,
                             self.reservations, start_after_s=200)
        self.assertEqual(result["status"], "found")
        task = result["task"]
        self.assertEqual(task["ground_wait_interval_s"], [90, 200])
        self.assertEqual(task["trajectory"][:2], [
            {"time_s": 90, "position_m": [100, 400, 0]},
            {"time_s": 200, "position_m": [100, 400, 0]},
        ])
        self.assertEqual(task["actual_departure_time_s"], 200)
        self.assertEqual(check_task(task, self.constraints, self.world)["status"], "clear")
        self.assertAlmostEqual(task["planning_energy_used"], task["trajectory"][-1]["time_s"] - 200)

    def test_ground_wait_cannot_cross_another_tasks_reserved_launch_interval(self):
        # Task 2 occupies this capacity-one pad from t=350 to t=362.
        for after in (350, 400):
            with self.subTest(start_after_s=after):
                result = search_task(self.task, self.world, self.constraints,
                                     self.reservations, start_after_s=after)
                self.assertEqual(result["status"], "ground_wait_interval_unavailable")

    def test_search_budget_exhaustion_is_not_reported_as_infeasibility(self):
        result = search_task(self.task, self.world, self.constraints,
                             self.reservations, max_expansions=0)
        self.assertEqual(result["status"], "search_budget_exhausted")
        self.assertNotIn("task", result)


if __name__ == "__main__":
    unittest.main()
