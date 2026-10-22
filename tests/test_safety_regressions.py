"""Independent contract and evidence-visibility regression checks.

These expectations come from the startup scenario's declared semantics, rather
than from planner output. All data in this module is explicitly synthetic.
"""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from chengkong.contracts import (
    ContractError, UnsupportedData, parse_interval, parse_quantity, validate_example,
)
from chengkong.world import apply_event, generate_constraints, load_example, visible_world


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "park_crane_synthetic.json"


class ContractRegressionTests(unittest.TestCase):
    def setUp(self):
        self.raw = load_example(EXAMPLE)

    def test_units_are_explicit_and_not_silently_converted(self):
        self.assertEqual(parse_quantity({"value": 12, "unit": "m"}, "m"), 12)
        self.assertEqual(parse_interval([120, 360]), [120, 360])
        with self.assertRaises(UnsupportedData):
            parse_quantity({"value": 12, "unit": "ft"}, "m")
        for interval in ([360, 120], [120, 120], [0, "360"], [0, float("inf")]):
            with self.subTest(interval=interval), self.assertRaises(ContractError):
                parse_interval(interval)

    def test_unsupported_coordinate_and_time_systems_reject(self):
        for section, key, value in (
            ("coordinate_system", "unit", "ft"),
            ("coordinate_system", "height_datum", "mean_sea_level"),
            ("time_system", "type", "unix_seconds"),
            ("time_system", "interval_convention", "[start,end]"),
        ):
            raw = deepcopy(self.raw)
            raw[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(UnsupportedData):
                validate_example(raw)

    def test_nonfinite_values_and_reversed_trajectory_times_reject(self):
        for value in (float("nan"), float("inf"), True):
            raw = deepcopy(self.raw)
            raw["observed_snapshot"]["tasks"][0]["protection_radius_m"] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_example(raw)
        raw = deepcopy(self.raw)
        raw["observed_snapshot"]["tasks"][0]["trajectory"][1]["time_s"] = 89
        with self.assertRaises(ContractError):
            validate_example(raw)

    def test_json_nan_is_rejected_at_import(self):
        raw = deepcopy(self.raw)
        raw["observed_snapshot"]["bounds_m"]["max"][0] = float("nan")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nonfinite.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ContractError):
                load_example(path)

    def test_unknown_geometry_is_retained_for_incomplete_checking(self):
        raw = deepcopy(self.raw)
        raw["observed_snapshot"]["buildings"][0]["geometry"] = {"type": "mesh"}
        world = visible_world(raw, "end_unconfirmed", 60)
        constraint = next(c for c in generate_constraints(world) if c["object_id"] == "building_1")
        self.assertEqual(constraint["geometry_status"], "unsupported")
        self.assertEqual(constraint["geometry"], {"type": "mesh"})


class VisibilityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.raw = load_example(EXAMPLE)

    def test_future_branch_events_cannot_change_current_world(self):
        changed = deepcopy(self.raw)
        event = changed["scenario_definitions_for_simulator_only"][0]["events"][0]
        event.update(observed_at_s=700, effective_at_s=650, source_kind="synthetic_hidden_marker")
        expected = visible_world(self.raw, "on_time_confirmed", 60)
        actual = visible_world(changed, "on_time_confirmed", 60)
        self.assertEqual(actual, expected)
        self.assertNotIn("synthetic_hidden_marker", json.dumps(actual))
        self.assertNotIn("scenario_definitions_for_simulator_only", actual)

    def test_all_three_branches_are_identical_before_event_release(self):
        worlds = []
        for scenario in self.raw["scenario_definitions_for_simulator_only"]:
            world = visible_world(self.raw, scenario["id"], 60)
            world.pop("scenario_id")
            worlds.append(world)
        self.assertEqual(worlds[0], worlds[1])
        self.assertEqual(worlds[1], worlds[2])

    def test_event_is_released_by_observation_time_not_effective_time(self):
        raw = deepcopy(self.raw)
        event = raw["scenario_definitions_for_simulator_only"][0]["events"][0]
        event.update(effective_at_s=360, observed_at_s=400)
        before = visible_world(raw, "on_time_confirmed", 399)
        after = visible_world(raw, "on_time_confirmed", 400)
        self.assertEqual(before["activities"][0]["completion_status"], "not_confirmed")
        self.assertEqual(after["activities"][0]["completion_confirmed_at_s"], 360)
        self.assertEqual(after["environment_version"], before["environment_version"] + 1)

    def test_future_evidence_cannot_be_applied_to_visible_world(self):
        world = visible_world(self.raw, "on_time_confirmed", 60)
        event = self.raw["scenario_definitions_for_simulator_only"][0]["events"][0]
        with self.assertRaises(ContractError):
            apply_event(world, event)
        self.assertEqual(world["environment_version"], 1)
        self.assertEqual(world["activities"][0]["completion_status"], "not_confirmed")

    def test_replaying_same_event_is_idempotent_and_conflicting_payload_rejects(self):
        world = visible_world(self.raw, "on_time_confirmed", 400)
        event = self.raw["scenario_definitions_for_simulator_only"][0]["events"][0]
        self.assertEqual(apply_event(world, event), world)
        changed = deepcopy(event)
        changed["effective_at_s"] = 350
        with self.assertRaises(ContractError):
            apply_event(world, changed)

    def test_planned_end_and_horizon_never_become_completion(self):
        for time in (360, 600, 900):
            with self.subTest(as_of_s=time):
                world = visible_world(self.raw, "end_unconfirmed", time)
                activity = next(c for c in generate_constraints(world) if c["kind"] == "activity")
                self.assertEqual(world["activities"][0]["completion_status"], "not_confirmed")
                self.assertEqual(activity["interval_s"], [120, 900])
                self.assertFalse(activity["end_is_confirmed"])
                self.assertEqual(activity["beyond_horizon_status"], "unknown")

    def test_delay_changes_plan_but_does_not_confirm_finish(self):
        world = visible_world(self.raw, "delayed_confirmed", 400)
        activity = next(c for c in generate_constraints(world) if c["kind"] == "activity")
        self.assertEqual(world["activities"][0]["planned_interval_s"], [120, 600])
        self.assertEqual(activity["interval_s"], [120, 900])
        self.assertFalse(activity["end_is_confirmed"])
        self.assertEqual(world["event_log"][0]["old_environment_version"], 1)
        self.assertEqual(world["event_log"][0]["new_environment_version"], 2)

    def test_completion_leaves_facility_geometry_and_history_intact(self):
        for scenario, time, end in (("on_time_confirmed", 400, 360), ("delayed_confirmed", 600, 600)):
            with self.subTest(scenario=scenario):
                world = visible_world(self.raw, scenario, time)
                constraints = generate_constraints(world)
                activity = next(c for c in constraints if c["kind"] == "activity")
                facility = [c for c in constraints if c["kind"] == "physical" and c["object_id"] == "crane_1"]
                self.assertEqual(activity["interval_s"], [120, end])
                self.assertTrue(activity["end_is_confirmed"])
                self.assertEqual(len(facility), 2)
                self.assertTrue(all(c["interval_s"] == [0, 900] for c in facility))
                self.assertTrue(world["facilities"][0]["installed"])


if __name__ == "__main__":
    unittest.main()
