import copy
import unittest

from chengkong.analysis import check_joint_tasks
from chengkong.resources import aircraft_continuity,capacity_conflicts,prepare_continuation
from chengkong.world import load_example,visible_world,generate_constraints
from chengkong.verification import verify_candidate


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

    def _turnaround_world_and_candidate(self):
        world=fixture();world['activities'][0]['activity_envelope']['center_xy_m']=[700,100]
        world['tasks'][1]['aircraft_id']=world['tasks'][0]['aircraft_id']
        world['aircraft_turnaround_model']={'ground_transfer_speed_mps':20.,'ground_transfer_energy_per_m':.01,'turnaround_s':10.,'recharge_rate_per_s':1.}
        candidate={'id':'turnaround-check','candidate_schema_version':2,'tasks':copy.deepcopy(world['tasks']),
            'base_environment_version':world['environment_version'],'base_reservation_version':world.get('reservation_version',1),
            'base_plan_versions':{t['id']:t['plan_version'] for t in world['tasks']},
            'scenario_id':world['scenario_id'],'rule_version':world['rule_version'],'model_version':world['model_version'],
            'valid_interval_s':world['horizon_s']}
        return world,candidate

    def test_turnaround_cannot_wait_on_a_pad_that_forbids_waiting(self):
        world,candidate=self._turnaround_world_and_candidate()
        world['pads'][0]['ground_wait_allowed']=False
        report=verify_candidate(candidate,world,generate_constraints(world))
        self.assertFalse(report['feasible'])
        conflict=next(c for c in report['joint_report']['conflicts'] if c['kind']=='aircraft_turnaround_wait_location_unavailable')
        self.assertEqual(conflict['interval_s'],[244,350])

    def test_turnaround_stationary_aircraft_is_in_continuous_separation(self):
        world,candidate=self._turnaround_world_and_candidate()
        task=world['tasks'][2];task['departure_time_s']=250
        task['mandatory_services'][0]['position_m']=[100,150,5];task['mandatory_services'][0]['window_s']=[250,800]
        task['trajectory']=[{'time_s':250,'position_m':[100,650,0]},
            {'time_s':251,'position_m':[100,650,5]},{'time_s':301,'position_m':[100,150,5]},
            {'time_s':311,'position_m':[100,150,5]},{'time_s':312,'position_m':[100,150,0]}]
        world['pads'][3]['position_m']=[100,150,0]
        candidate['tasks']=copy.deepcopy(world['tasks'])
        report=verify_candidate(candidate,world,generate_constraints(world))
        self.assertFalse(report['feasible'])
        conflicts=[c for c in report['joint_report']['conflicts'] if c['kind']=='aircraft_separation']
        self.assertTrue(any(c['interval_s'][0]<=276<=c['interval_s'][1] and min(c['segment_indices'])<=-1000 for c in conflicts))
        self.assertEqual(report['joint_report']['additional_stationary_occupancies'][0]['position_m'],[100,400,0])

    def _turnaround_during_confirmed_activity(self,unknown_geometry=False):
        raw=load_example('examples/park_crane_synthetic.json')
        activity=raw['observed_snapshot']['activities'][0]
        activity['planned_interval_s']=[260,300]
        activity['activity_envelope']=None if unknown_geometry else {
            'type':'vertical_cylinder','center_xy_m':[100,400],'radius_m':10,'z_min_m':0,'z_max_m':10}
        raw['scenario_definitions_for_simulator_only'][0]['events'][0].update(observed_at_s=300,effective_at_s=300)
        world=visible_world(raw,'on_time_confirmed',320)
        world['tasks']=world['tasks'][:2];world['tasks'][1]['aircraft_id']=world['tasks'][0]['aircraft_id']
        world['aircraft_turnaround_model']={'ground_transfer_speed_mps':20.,'ground_transfer_energy_per_m':.01,'turnaround_s':10.,'recharge_rate_per_s':1.}
        candidate={'id':'turnaround-object-check','candidate_schema_version':2,'tasks':copy.deepcopy(world['tasks']),
            'base_environment_version':world['environment_version'],'base_reservation_version':world.get('reservation_version',1),
            'base_plan_versions':{t['id']:t['plan_version'] for t in world['tasks']},
            'scenario_id':world['scenario_id'],'rule_version':world['rule_version'],'model_version':world['model_version'],
            'valid_interval_s':world['horizon_s']}
        return world,candidate

    def test_turnaround_stationary_presence_checks_full_object_constraints_and_reference(self):
        world,candidate=self._turnaround_during_confirmed_activity()
        report=verify_candidate(candidate,world,generate_constraints(world))
        self.assertTrue(all(not row['conflicts'] for row in report['task_reports']))
        self.assertFalse(report['feasible'])
        conflicts=[c for c in report['joint_report']['conflicts'] if c['kind']=='aircraft_turnaround_object_occupancy']
        self.assertEqual(conflicts[0]['interval_s'],[260,300])
        self.assertEqual(conflicts[0]['task_ids'],['task_1','task_2'])
        self.assertTrue(report['joint_report']['stationary_reference_reports'][0]['conflicts'])

    def test_unknown_geometry_during_only_turnaround_still_prevents_feasibility(self):
        world,candidate=self._turnaround_during_confirmed_activity(unknown_geometry=True)
        report=verify_candidate(candidate,world,generate_constraints(world))
        self.assertTrue(all(row['checks_completed'] for row in report['task_reports']))
        self.assertFalse(report['feasible']);self.assertFalse(report['checks_completed'])
        self.assertTrue(report['joint_report']['stationary_object_reports'][0]['unchecked'])
        self.assertTrue(report['joint_report']['stationary_reference_reports'][0]['unchecked'])

    def test_inflight_turnaround_object_conflict_in_past_is_reported_as_history(self):
        from chengkong.execution import simulate_context
        world,candidate=self._turnaround_during_confirmed_activity()
        candidate['execution_context']=simulate_context(world)
        report=verify_candidate(candidate,world,generate_constraints(world))
        self.assertTrue(report['feasible'],report)
        self.assertTrue(any(c.get('kind')=='aircraft_turnaround_object_occupancy' for c in report['historical_conflicts']))
        self.assertFalse(report['joint_report']['conflicts'])
        self.assertFalse(report['joint_report']['stationary_reference_reports'][0]['conflicts'])
        self.assertTrue(report['joint_report']['complete_stationary_reference_reports'][0]['conflicts'])

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
