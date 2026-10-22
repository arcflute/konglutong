"""Hand-computable continuous geometry and half-open time reference cases."""
import unittest

from chengkong.analysis import check_task
from chengkong.geometry import UnsupportedGeometry, segment_occupancy


class ContinuousGeometryTests(unittest.TestCase):
    def assertInterval(self, actual, expected):
        self.assertEqual(len(actual),1)
        self.assertAlmostEqual(actual[0][0],expected[0],places=8)
        self.assertAlmostEqual(actual[0][1],expected[1],places=8)

    def test_aabb_middle_crossing_with_clear_waypoints(self):
        box = {"type":"aabb","min_m":[0,-1,-1],"max_m":[1,1,1]}
        self.assertInterval(segment_occupancy([-2,0,0],[2,0,0],box,0),(.5,.75))

    def test_spherical_aabb_protection_does_not_use_box_corner(self):
        box = {"type":"aabb","min_m":[0,0,0],"max_m":[1,1,1]}
        self.assertEqual(segment_occupancy([-1,-1,.5],[-1,-1,.5],box,1),[])
        self.assertInterval(segment_occupancy([-1,0,.5],[-1,0,.5],box,1),(0,1))

    def test_cylinder_analytic_crossing(self):
        cylinder = {"type":"vertical_cylinder","center_xy_m":[0,0],"radius_m":2,"z_min_m":-1,"z_max_m":1}
        self.assertInterval(segment_occupancy([-10,0,0],[10,0,0],cylinder,1),(.35,.65))

    def test_capsule_body_and_round_end(self):
        capsule = {"type":"capsule","start_m":[0,0,0],"end_m":[0,4,0],"radius_m":1}
        self.assertInterval(segment_occupancy([-5,2,0],[5,2,0],capsule),(.4,.6))
        self.assertInterval(segment_occupancy([-5,-1,0],[5,-1,0],capsule),(.5,.5))
        self.assertEqual(segment_occupancy([-5,-2,0],[5,-2,0],capsule),[])

    def test_capsule_zero_axis_is_sphere(self):
        sphere = {"type":"capsule","start_m":[0,0,0],"end_m":[0,0,0],"radius_m":2}
        self.assertInterval(segment_occupancy([-10,0,0],[10,0,0],sphere),(.4,.6))

    def test_unknown_and_unsupported_geometry_raise(self):
        for geometry in (None,{"type":"mesh"}):
            with self.assertRaises(UnsupportedGeometry):
                segment_occupancy([0,0,0],[1,0,0],geometry)

    def test_time_end_excluded_start_included_contact(self):
        world = {"environment_version":1,"scenario_id":"manual","rule_version":"rules-v1","model_version":"none"}
        task = {"id":"task","protection_radius_m":0,"trajectory":[
            {"time_s":0,"position_m":[-5,0,0]},{"time_s":10,"position_m":[5,0,0]}]}
        constraint = {"id":"wall","object_id":"wall","kind":"physical",
            "geometry":{"type":"aabb","min_m":[0,-1,-1],"max_m":[0,1,1]},
            "interval_s":[0,5],**world}
        self.assertEqual(check_task(task,[constraint],world)["status"],"clear")
        constraint["interval_s"] = [5,10]
        report = check_task(task,[constraint],world)
        self.assertEqual(report["status"],"conflict")
        self.assertEqual(report["conflicts"][0]["interval_s"],[5,5])

    def test_missing_geometry_never_clear(self):
        world = {"environment_version":1,"scenario_id":"manual","rule_version":"rules-v1","model_version":"none"}
        task = {"id":"task","protection_radius_m":0,"trajectory":[
            {"time_s":0,"position_m":[0,0,0]},{"time_s":10,"position_m":[1,0,0]}]}
        result = check_task(task,[{"id":"unknown","geometry":None,"interval_s":[0,10],**world}],world)
        self.assertEqual(result["status"],"not_checked")
        self.assertFalse(result["checks_completed"])

    def test_beyond_horizon_remains_unchecked(self):
        world = {"horizon_s":[0,5]}
        task = {"id":"task","protection_radius_m":0,"trajectory":[
            {"time_s":0,"position_m":[0,0,0]},{"time_s":10,"position_m":[1,0,0]}]}
        report = check_task(task,[],world)
        self.assertEqual(report["status"],"not_checked")
        self.assertEqual(report["unchecked"][0]["reason"],"trajectory_outside_checked_horizon")


if __name__ == "__main__":
    unittest.main()
