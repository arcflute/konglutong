import copy
import unittest
from chengkong.world import load_example,visible_world,generate_constraints
from chengkong.planning import search_task,generate_candidates
from chengkong.verification import verify_candidate
from chengkong.analysis import check_task,check_joint_tasks


class SIPPTests(unittest.TestCase):
    def setUp(self):
        self.world=visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',60)
        self.constraints=generate_constraints(self.world)

    def test_ground_delay_uses_safe_interval_and_keeps_service(self):
        # Explicit finite-reservation unit fixture, NOT future evidence given to online world.
        constraints=copy.deepcopy(self.constraints)
        for c in constraints:
            if c['kind']=='activity':c['interval_s'][1]=200
        task=self.world['tasks'][0]
        departure=200-(440-3-100)/10-12+1e-6
        result=search_task(task,self.world,constraints,start_after_s=departure,allow_detour=False)
        self.assertEqual(result['status'],'found')
        planned=result['task']
        self.assertAlmostEqual(planned['actual_departure_time_s'],154.300001)
        self.assertEqual(planned['ground_wait_interval_s'],[90,departure])
        self.assertAlmostEqual(planned['planning_energy_used'],114)
        self.assertEqual(check_task(planned,constraints,self.world)['status'],'clear')
        self.assertEqual(planned['mandatory_services'],task['mandatory_services'])

    def test_ground_wait_reserves_shared_pad(self):
        task=self.world['tasks'][0]
        other=copy.deepcopy(self.world['tasks'][1])
        for p in other['trajectory']:p['time_s']-=250
        other['departure_time_s']=100
        result=search_task(task,self.world,self.constraints,[other],start_after_s=160)
        self.assertEqual(result['status'],'ground_wait_interval_unavailable')

    def test_search_budget_not_reported_as_infeasible(self):
        result=search_task(self.world['tasks'][0],self.world,self.constraints,max_expansions=0)
        self.assertEqual(result['status'],'search_budget_exhausted')

    def test_energy_limit_is_not_ignored(self):
        task=copy.deepcopy(self.world['tasks'][0]);task['energy_assumptions']['capacity']=25
        result=search_task(task,self.world,self.constraints)
        self.assertEqual(result['status'],'no_solution_in_bounded_graph')

    def test_diagnostics_preserve_failed_delay_and_successful_detour(self):
        diagnostic=[];candidate=generate_candidates(self.world,self.constraints,diagnostic)
        self.assertEqual(candidate[0]['kind'],'detour')
        self.assertTrue(any(d['mode']=='delay' and any(a['status']=='no_solution_in_bounded_graph' for a in d['attempts']) for d in diagnostic))
        self.assertTrue(verify_candidate(candidate[0],self.world,self.constraints)['feasible'])

    def test_unsupported_altitude_profile_does_not_fake_plan(self):
        task=copy.deepcopy(self.world['tasks'][0]);task['trajectory'][1]['position_m'][2]=50
        self.assertEqual(search_task(task,self.world,self.constraints)['status'],'unsupported_route_profile')

    def test_unknown_future_geometry_cannot_crash_replanning_into_its_time(self):
        self.world['activities'][0]['planned_interval_s']=[700,800]
        self.world['activities'][0]['activity_envelope']=None
        self.world['buildings'][0]['geometry']={'type':'aabb','min_m':[400,390,50],'max_m':[450,410,70]}
        constraints=generate_constraints(self.world)
        self.assertTrue(check_task(self.world['tasks'][0],constraints,self.world)['checks_completed'])
        diagnostics=[]
        self.assertEqual(generate_candidates(self.world,constraints,diagnostics),[])
        self.assertEqual(diagnostics[0]['status'],'unsupported_or_incomplete_search_input')
        self.assertEqual(search_task(self.world['tasks'][0],self.world,constraints)['status'],'unsupported_or_incomplete_search_input')


if __name__=='__main__':unittest.main()
