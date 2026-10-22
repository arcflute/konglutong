"""Safety and mission checks use hand-altered complete plans, not search outputs."""
import copy
from pathlib import Path
import unittest

from chengkong.analysis import check_joint_tasks
from chengkong.verification import verify_candidate
from chengkong.world import generate_constraints, load_example, visible_world

EXAMPLE = Path(__file__).resolve().parents[1]/"examples/park_crane_synthetic.json"


class CandidateVerificationTests(unittest.TestCase):
    def setUp(self):
        self.world = visible_world(load_example(EXAMPLE),"end_unconfirmed",60)
        # Relocate the synthetic activity, leaving the original mission unchanged.
        self.world["activities"][0]["activity_envelope"]["center_xy_m"] = [700,100]
        self.constraints = generate_constraints(self.world)
        self.candidate = {"id":"manual","tasks":copy.deepcopy(self.world["tasks"]),
            "base_environment_version":self.world["environment_version"],
            "base_plan_versions":{t["id"]:t["plan_version"] for t in self.world["tasks"]},
            "scenario_id":self.world["scenario_id"],"rule_version":self.world["rule_version"],
            "model_version":self.world["model_version"],"valid_interval_s":self.world["horizon_s"]}

    def verify(self):
        return verify_candidate(self.candidate,self.world,self.constraints)

    def test_valid_complete_plan_and_hand_computed_energy(self):
        before = copy.deepcopy(self.world)
        report = self.verify()
        self.assertEqual(report["status"],"conditionally_feasible",report)
        self.assertEqual(report["task_metrics"][0]["energy_used"],114)
        self.assertEqual(self.world,before)

    def test_version_mismatch_needs_recheck(self):
        self.world["environment_version"] += 1
        self.assertEqual(self.verify()["status"],"needs_recheck")

    def test_service_definitions_cannot_be_deleted_or_changed(self):
        self.candidate["tasks"][0]["mandatory_services"] = []
        report = self.verify()
        self.assertFalse(report["feasible"])
        self.assertTrue(any(v["reason"] == "mission_requirement_modified" for v in report["violations"]))

    def test_missing_service_dwell_rejected(self):
        task = self.candidate["tasks"][0]
        task["trajectory"].pop(3)
        report = self.verify()
        self.assertTrue(any(v["reason"] == "mandatory_service_unsatisfied" for v in report["violations"]))

    def test_task_removal_and_protection_change_rejected(self):
        self.candidate["tasks"].pop()
        self.candidate["tasks"][0]["protection_radius_m"] = 0
        report = self.verify()
        reasons = {v["reason"] for v in report["violations"]}
        self.assertIn("joint_task_set_modified",reasons)
        self.assertIn("mission_requirement_modified",reasons)

    def test_ground_wait_is_reserved(self):
        task = self.candidate["tasks"][0]
        old_start = copy.deepcopy(task["trajectory"][0])
        for point in task["trajectory"]:
            point["time_s"] += 260
        task["trajectory"].insert(0,old_start)
        task["actual_departure_time_s"] = 350
        task["ground_wait_interval_s"] = [90,350]
        report = self.verify()
        self.assertEqual(report["status"],"rejected")
        self.assertTrue(any(c["kind"] == "pad_capacity" for c in report["joint_report"]["conflicts"]))

    def test_no_constraints_cannot_skip_world_objects(self):
        report = verify_candidate(self.candidate,self.world,[])
        self.assertFalse(report["feasible"])
        self.assertTrue(any(u["reason"] == "constraint_set_incomplete_or_modified" for u in report["unchecked"]))

    def test_recheck_all_objects_includes_new_unrelated_obstacle(self):
        self.world["buildings"].append({"id":"new_building","source_id":"record_initial",
            "geometry":{"type":"aabb","min_m":[590,640,50],"max_m":[610,660,70]}})
        self.constraints = generate_constraints(self.world)
        report = self.verify()
        self.assertFalse(report["feasible"])
        task3 = next(r for r in report["task_reports"] if r["task_id"] == "task_3")
        self.assertTrue(any(c["object_id"] == "new_building" for c in task3["conflicts"]))

    def test_history_cannot_be_rewritten(self):
        self.world["as_of_s"] = 100
        self.candidate["tasks"][0]["trajectory"][1]["time_s"] += 1
        self.constraints = generate_constraints(self.world)
        report = self.verify()
        self.assertTrue(any(v["reason"] == "cannot_rewrite_already_started_task" for v in report["violations"]))

    def test_energy_capacity_independently_checked(self):
        self.world["tasks"][0]["energy_assumptions"]["capacity"] = 120
        self.candidate["tasks"][0]["energy_assumptions"]["capacity"] = 120
        report = self.verify()
        self.assertTrue(any(v["reason"] == "energy_capacity" for v in report["violations"]))

    def test_crossing_aircraft_detected_between_waypoints(self):
        tasks = copy.deepcopy(self.world["tasks"][:2])
        tasks[0]["trajectory"] = [{"time_s":0,"position_m":[0,0,50]},{"time_s":10,"position_m":[10,0,50]}]
        tasks[1]["trajectory"] = [{"time_s":0,"position_m":[5,-5,50]},{"time_s":10,"position_m":[5,5,50]}]
        tasks[0]["protection_radius_m"] = tasks[1]["protection_radius_m"] = .5
        report = check_joint_tasks(tasks,self.world)
        conflicts = [c for c in report["conflicts"] if c["kind"] == "aircraft_separation"]
        self.assertEqual(len(conflicts),1)
        self.assertAlmostEqual(conflicts[0]["interval_s"][0],5-1/(2**.5))
        self.assertAlmostEqual(conflicts[0]["interval_s"][1],5+1/(2**.5))


if __name__ == "__main__":
    unittest.main()
