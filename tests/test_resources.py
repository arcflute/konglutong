import copy
import unittest

from chengkong.analysis import check_joint_tasks
from chengkong.resources import aircraft_continuity,capacity_conflicts,prepare_continuation
from chengkong.world import load_example,visible_world


def fixture():
    return visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',60)


class ResourceTests(unittest.TestCase):
    def test_same_aircraft_transfer_charge_and_energy_are_explicit(self):
        world=fixture();tasks=copy.deepcopy(world['tasks'][:2]);tasks[1]['aircraft_id']=tasks[0]['aircraft_id']
        world['aircraft_turnaround_model']={'ground_transfer_speed_mps':20.,'ground_transfer_energy_per_m':.01,'turnaround_s':10.,'recharge_rate_per_s':1.}
        result=aircraft_continuity(tasks,world)
        self.assertFalse(result['conflicts']);self.assertFalse(result['unchecked'])
        transition=result['transitions'][0]
        self.assertEqual(transition['distance_m'],800)
        self.assertEqual(transition['interval_s'],[204,254])
        self.assertEqual(transition['energy_at_next_start'],374)
        self.assertEqual(transition['energy_after_next_task'],260)
        self.assertEqual(result['reservations'][0]['interval_s'],[244,350])

    def test_same_aircraft_insufficient_transfer_time_and_energy(self):
        world=fixture();tasks=copy.deepcopy(world['tasks'][:2]);tasks[1]['aircraft_id']=tasks[0]['aircraft_id']
        world['aircraft_turnaround_model']={'ground_transfer_speed_mps':1.,'ground_transfer_energy_per_m':.4,'turnaround_s':10.,'recharge_rate_per_s':0.}
        result=aircraft_continuity(tasks,world)
        kinds={c['kind'] for c in result['conflicts']}
        self.assertIn('aircraft_transition_time',kinds);self.assertIn('aircraft_continuity_energy',kinds)

    def test_no_turnaround_configuration_never_resets_battery(self):
        world=fixture();tasks=copy.deepcopy(world['tasks'][:2]);tasks[1]['aircraft_id']=tasks[0]['aircraft_id']
        self.assertEqual(aircraft_continuity(tasks,world)['unchecked'][0]['kind'],'aircraft_turnaround_unmodeled')

    def test_waiting_area_capacity_half_open_and_finite(self):
        rows=[{'task_id':'a','resource_id':'w','interval_s':[1,5]},
              {'task_id':'b','resource_id':'w','interval_s':[3,6]},
              {'task_id':'c','resource_id':'w','interval_s':[5,8]}]
        conflicts,unchecked=capacity_conflicts(rows,[{'id':'w','capacity':1,'resource_kind':'waiting_area'}])
        self.assertFalse(unchecked);self.assertEqual([r['task_ids'] for r in conflicts],[['a','b'],['b','c']])

    def test_head_on_same_direction_and_crossing_are_continuous(self):
        world=fixture();base=copy.deepcopy(world['tasks'][:2])
        shapes=[([[10,20,60],[30,20,60]],[[30,20,60],[10,20,60]],'head_on'),
                ([[10,20,60],[30,20,60]],[[12,20,60],[32,20,60]],'same_direction'),
                ([[10,20,60],[30,20,60]],[[20,10,60],[20,30,60]],'crossing')]
        for left,right,expected in shapes:
            tasks=copy.deepcopy(base)
            for task,points in zip(tasks,[left,right]):
                task['trajectory']=[{'time_s':i*10,'position_m':p} for i,p in enumerate(points)]
            rows=[c for c in check_joint_tasks(tasks,world)['conflicts'] if c['kind']=='aircraft_separation']
            self.assertEqual(rows[0]['encounter_type'],expected)


if __name__=='__main__':unittest.main()
