import copy
import unittest

from chengkong.execution import simulate_context,replan_inflight,frozen_prefix,suffix_task,restore_full_task
from chengkong.planning import search_task
from chengkong.verification import verify_candidate
from chengkong.world import load_example,visible_world,generate_constraints


def fixture(at):
    return visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',at)


class ExecutionTests(unittest.TestCase):
    def test_inflight_maintenance_keeps_exact_executed_prefix_and_current_position(self):
        world=fixture(120);context=simulate_context(world);diagnostics=[]
        candidates=replan_inflight(world,generate_constraints(world),diagnostics)
        self.assertTrue(candidates,diagnostics)
        task=candidates[0]['tasks'][0]
        self.assertEqual(frozen_prefix(task,120),context['tasks']['task_1']['frozen_prefix'])
        self.assertEqual(frozen_prefix(task,120)[-1]['position_m'],[280.,400.,60.])
        self.assertEqual(task['trajectory'][0],world['tasks'][0]['trajectory'][0])
        self.assertTrue(verify_candidate(candidates[0],world,generate_constraints(world))['feasible'])
        task['trajectory'][1]['position_m'][0]+=1
        self.assertIn('executed_prefix_modified',{v['reason'] for v in verify_candidate(candidates[0],world,generate_constraints(world))['violations']})

    def test_later_historical_conflict_does_not_invalidate_future_candidate(self):
        world=fixture(210);candidates=replan_inflight(world,generate_constraints(world))
        self.assertTrue(candidates)
        report=verify_candidate(candidates[0],world,generate_constraints(world))
        self.assertTrue(report['feasible']);self.assertTrue(report['historical_conflicts'])
        self.assertEqual(candidates[0]['tasks'][0]['trajectory'],world['tasks'][0]['trajectory'])

    def test_context_energy_cannot_be_fabricated(self):
        world=fixture(120);candidates=replan_inflight(world,generate_constraints(world))
        candidates[0]['execution_context']['tasks']['task_1']['remaining_energy']+=100
        report=verify_candidate(candidates[0],world,generate_constraints(world))
        self.assertFalse(report['feasible']);self.assertEqual(report['unchecked'][0]['reason'],'invalid_or_stale_execution_context')

    def test_partial_service_keeps_remaining_dwell_and_energy(self):
        world=fixture(185);context=simulate_context(world);original=world['tasks'][0]
        suffix=suffix_task(original,context)
        self.assertEqual(suffix['mandatory_services'][0]['duration_s'],7)
        self.assertEqual(suffix['energy_assumptions']['capacity'],305)
        result=search_task(suffix,world,generate_constraints(world))
        self.assertEqual(result['status'],'found')
        full=restore_full_task(original,result['task'],context)
        self.assertEqual(full['mandatory_services'],original['mandatory_services'])
        self.assertEqual(full['trajectory'][-1]['time_s'],204)
        self.assertEqual(full['planning_energy_used'],114)

    def test_split_stationary_prefix_does_not_lose_service_credit(self):
        world=fixture(189);task=world['tasks'][0]
        task['trajectory'].insert(3,{'time_s':185,'position_m':[900,400,60]})
        task['trajectory'].insert(4,{'time_s':188,'position_m':[900,400,60]})
        suffix=suffix_task(task,simulate_context(world))
        self.assertEqual(suffix['mandatory_services'][0]['duration_s'],3)

    def test_current_spatial_contact_is_not_erased_as_history(self):
        from chengkong.analysis import check_task
        world=fixture(60)
        task=copy.deepcopy(world['tasks'][0]);task['protection_radius_m']=0
        task['trajectory']=[{'time_s':70,'position_m':[100,400,60]}, {'time_s':80,'position_m':[200,400,60]}]
        constraint={'id':'edge','geometry':{'type':'aabb','min_m':[120,390,50],'max_m':[140,410,70]},
            'interval_s':[0,900], **{k:world[k] for k in ['environment_version','scenario_id','rule_version','model_version']}}
        checked=check_task(task,[constraint],world,74)
        self.assertTrue(checked['conflicts'])
        self.assertEqual(checked['conflicts'][0]['interval_s'],[74,74])
        self.assertFalse(check_task(task,[constraint],world,74.1)['conflicts'])

    def test_resume_mid_ascent_stays_at_simulated_height(self):
        world=fixture(360);candidates=replan_inflight(world,generate_constraints(world))
        self.assertTrue(candidates)
        task=next(t for t in candidates[0]['tasks'] if t['id']=='task_2')
        self.assertEqual(frozen_prefix(task,360)[-1]['position_m'],[100.,400.,50.])


if __name__=='__main__':unittest.main()
