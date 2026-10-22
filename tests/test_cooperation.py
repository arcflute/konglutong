import copy
import unittest

from chengkong.planning import generate_candidates
from chengkong.verification import verify_candidate
from chengkong.world import load_example,visible_world,generate_constraints


def fixture():
    return visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',60)


def opposing_fixture():
    world=fixture();world['activities'][0]['activity_envelope']['center_xy_m']=[700,100]
    world['buildings'].append({'id':'detour_anchor','source_id':'record_initial',
        'geometry':{'type':'aabb','min_m':[490,480,40],'max_m':[510,500,55]}})
    world['tasks']=world['tasks'][:2];a,b=world['tasks']
    b['departure_time_s']=90;b['deadline_s']=204;b['priority']=2
    for p in b['trajectory']:
        p['position_m'][0]=1000-p['position_m'][0];p['time_s']-=260
    b['launch_pad_id'],b['landing_pad_id']=b['landing_pad_id'],b['launch_pad_id']
    b['mandatory_services'][0]['position_m'][0]=100;b['mandatory_services'][0]['window_s']=[90,204]
    return world


class CooperationTests(unittest.TestCase):
    def test_detour_expands_repair_to_initially_clear_third_task(self):
        world=fixture();task=world['tasks'][2]
        # Analytically align task 3 with the detour corner reached by task 1.
        for p in task['trajectory']:
            p['position_m'][1]=474.5;p['time_s']+=.7132780088469
        task['departure_time_s']+=.7132780088469;task['mandatory_services'][0]['position_m'][1]=474.5
        for p in world['pads'][2:]:p['position_m'][1]=474.5
        diagnostics=[];constraints=generate_constraints(world)
        candidates=generate_candidates(world,constraints,diagnostics)
        self.assertTrue(candidates)
        expansions=[d for d in diagnostics if d.get('status')=='repair_set_expanded']
        self.assertIn('task_3',expansions[0]['added_task_ids'])
        self.assertEqual(candidates[0]['repair_task_ids'],['task_1','task_2','task_3'])
        self.assertTrue(verify_candidate(candidates[0],world,constraints)['feasible'])

    def test_later_arrival_expands_to_shared_pad_task_and_reorders(self):
        world=fixture();task=world['tasks'][2]
        task['departure_time_s']=205;task['launch_pad_id']='pad_east_main';task['landing_pad_id']='pad_east_north'
        task['trajectory']=[{'time_s':205,'position_m':[900,400,0]},
            {'time_s':217,'position_m':[900,400,60]},{'time_s':242,'position_m':[900,650,60]},
            {'time_s':252,'position_m':[900,650,60]},{'time_s':264,'position_m':[900,650,0]}]
        task['mandatory_services'][0]['position_m']=[900,650,60]
        task['mandatory_services'][0]['window_s']=[205,800]
        diagnostics=[];candidates=generate_candidates(world,generate_constraints(world),diagnostics)
        self.assertTrue(candidates)
        expansion=next(d for d in diagnostics if d.get('status')=='repair_set_expanded')
        self.assertIn('task_3',expansion['added_task_ids'])
        self.assertTrue(any(c['kind']=='pad_capacity' for c in expansion['conflicts']))
        self.assertTrue(candidates[0]['review']['feasible'])
        self.assertLess(candidates[0]['search_priority_order'].index('task_3'),candidates[0]['search_priority_order'].index('task_1'))

    def test_order_backtracking_succeeds_after_first_priority_order_fails(self):
        world=opposing_fixture();diagnostics=[]
        candidates=generate_candidates(world,generate_constraints(world),diagnostics)
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]['search_priority_order'],['task_2','task_1'])
        self.assertTrue(any(d.get('status')=='priority_order_backtracked' and d.get('priority_order')==['task_1','task_2'] for d in diagnostics))
        self.assertEqual(candidates[0]['tasks'][1]['mandatory_services'],world['tasks'][1]['mandatory_services'])

    def test_exhausted_budget_and_timeout_never_claim_infeasibility(self):
        for budget,status in [({'max_searches':0},'search_budget_exhausted'),({'timeout_s':0},'cooperative_timeout')]:
            world=opposing_fixture();diagnostics=[]
            self.assertEqual(generate_candidates(world,generate_constraints(world),diagnostics,budget),[])
            self.assertTrue(any(d.get('status')==status or d.get('failure_class')==status for d in diagnostics),diagnostics)
            self.assertFalse(any(d.get('global_infeasibility_proven') for d in diagnostics))

    def test_same_aircraft_continuation_inserts_real_transfer_time(self):
        world=fixture();world['tasks']=world['tasks'][:2]
        world['tasks'][1]['aircraft_id']=world['tasks'][0]['aircraft_id']
        world['aircraft_turnaround_model']={'ground_transfer_speed_mps':4.,'ground_transfer_energy_per_m':.01,'turnaround_s':10.,'recharge_rate_per_s':0.}
        candidates=generate_candidates(world,generate_constraints(world))
        self.assertTrue(candidates)
        first,second=candidates[0]['tasks']
        self.assertAlmostEqual(second['departure_time_s'],first['trajectory'][-1]['time_s']+210)
        self.assertEqual(second['ground_entry']['mode'],'configured_ground_carrier')
        self.assertTrue(candidates[0]['review']['joint_report']['aircraft_transitions'])
        self.assertTrue(candidates[0]['review']['feasible'])

    def test_reservation_version_and_actual_cost_are_carried(self):
        world=fixture();world['reservation_version']=4
        c=generate_candidates(world,generate_constraints(world))[0]
        self.assertEqual(c['base_reservation_version'],4)
        self.assertEqual(c['stability_cost']['values'][0],2)
        self.assertGreater(c['stability_cost']['values'][2],0)
        world['reservation_version']=5
        self.assertIn('base_reservation_version',verify_candidate(c,world,generate_constraints(world))['version_mismatches'])

    def test_ranking_hook_gets_only_unsolved_proposals(self):
        world=fixture();calls=[]
        def rank(proposals):
            calls.append(proposals)
            self.assertEqual(set(proposals[0]),{'id','action','task_ids'})
            return {'proposal_order':[p['id'] for p in reversed(proposals)],'status':'test_simulated_ranking'}
        candidates=generate_candidates(world,generate_constraints(world),ranking_hook=rank)
        self.assertTrue(calls);self.assertTrue(candidates[0]['review']['feasible'])
        self.assertEqual(candidates[0]['ranking_source'],'configured_learning_proposal_order')


if __name__=='__main__':unittest.main()
