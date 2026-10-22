import copy
import unittest
from chengkong.world import load_example,visible_world,generate_constraints
from chengkong.analysis import check_task,check_joint_tasks


class AnalysisScopeTests(unittest.TestCase):
    def setUp(self):
        self.world=visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',60)

    def test_protection_margin_is_identified_separately(self):
        task=copy.deepcopy(self.world['tasks'][0])
        task['trajectory']=[{'time_s':130,'position_m':[400,462,60]},
                            {'time_s':150,'position_m':[600,462,60]}]
        result=check_task(task,generate_constraints(self.world),self.world)
        activity=[c for c in result['conflicts'] if c['kind']=='activity']
        self.assertTrue(activity)
        self.assertEqual(activity[0]['trigger'],'protection_margin_only')
        self.assertEqual(activity[0]['unprotected_overlap_intervals_s'],[])
        self.assertEqual(activity[0]['model_version'],'none')

    def test_sequential_aircraft_reuse_cannot_assume_battery_reset_or_reposition(self):
        tasks=copy.deepcopy(self.world['tasks'])
        tasks[1]['aircraft_id']=tasks[0]['aircraft_id']
        result=check_joint_tasks(tasks,self.world)
        self.assertEqual(result['status'],'not_checked')
        self.assertFalse(result['checks_completed'])
        self.assertTrue(any(u.get('kind')=='aircraft_turnaround_unmodeled' for u in result['unchecked']))


if __name__=='__main__':unittest.main()
