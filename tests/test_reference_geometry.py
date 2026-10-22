import unittest
from chengkong.reference_geometry import segment_intersects,check_reference_task
from chengkong.geometry import segment_occupancy


class ReferenceTests(unittest.TestCase):
    def test_hand_calculated_box_corner_and_tangency(self):
        box={'type':'aabb','min_m':[0,0,0],'max_m':[2,2,2]}
        self.assertTrue(segment_intersects([-2,1,1],[4,1,1],box))
        self.assertFalse(segment_intersects([-2,-1,-1],[4,-1,-1],box,1))
        self.assertTrue(segment_intersects([-2,-1,1],[4,-1,1],box,1))
        self.assertFalse(segment_intersects([-2,1,1],[0,1,1],box,end_exclusive=True))
        self.assertTrue(segment_intersects([-2,1,1],[0,1,1],box,end_exclusive=False))

    def test_capsule_and_cylinder_against_separate_analytic_solver(self):
        shapes=[{'type':'capsule','start_m':[0,0,0],'end_m':[0,0,10],'radius_m':2},
            {'type':'vertical_cylinder','center_xy_m':[0,0],'radius_m':2,'z_min_m':0,'z_max_m':10}]
        for shape in shapes:
            for y in [0.,2.,2.5,3.,4.]:
                a=[-5,y,5];b=[5,y,5]
                self.assertEqual(segment_intersects(a,b,shape,.5),bool(segment_occupancy(a,b,shape,.5)),(shape,y))

    def test_reference_obeys_time_overlap(self):
        task={'id':'t','protection_radius_m':0,'trajectory':[{'time_s':0,'position_m':[-2,1,1]},{'time_s':10,'position_m':[8,1,1]}]}
        c={'id':'c','geometry':{'type':'aabb','min_m':[0,0,0],'max_m':[2,2,2]},'interval_s':[4,10]}
        # Exiting box at t=4 still closed contact at activity start.
        self.assertTrue(check_reference_task(task,[c])['conflicts'])
        c['interval_s']=[0,2]
        self.assertFalse(check_reference_task(task,[c])['conflicts'])


if __name__=='__main__':unittest.main()
