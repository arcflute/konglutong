"""Hand-checkable round4 dynamic SIPP cases; no future simulation truth used."""
from copy import deepcopy
from pathlib import Path
import unittest

from chengkong.analysis import check_task,check_joint_tasks
from chengkong.contracts import ContractError
from chengkong.motion import geometry_at,validate_motion
from chengkong.planning import search_task,_safe_intervals
from chengkong.reference_geometry import check_reference_task
from chengkong.temporal_windows import TemporalWindows
from chengkong.verification import verify_candidate
from chengkong.world import load_example,visible_world,generate_constraints,apply_event

FIXTURE=Path(__file__).resolve().parents[1]/'examples/round4/dynamic_window.json'


def raw():return load_example(FIXTURE)


def world(data=None):return visible_world(raw() if data is None else data,'dynamic_window',0)


def candidate(w,task):
    return {'id':'manual_dynamic_candidate','candidate_schema_version':2,'tasks':[task],
        'base_environment_version':w['environment_version'],'base_reservation_version':w.get('reservation_version',1),
        'base_plan_versions':{t['id']:t['plan_version'] for t in w['tasks']},
        'rule_version':w['rule_version'],'model_version':w['model_version'],'scenario_id':w['scenario_id'],
        'valid_interval_s':w['horizon_s']}


class DynamicPlanningTests(unittest.TestCase):
    def test_allowed_window_ground_wait_preserves_service_and_passes_independent_review(self):
        w=world();c=generate_constraints(w);original=deepcopy(w['tasks'][0])
        conservative=deepcopy(w);conservative['planning_policy']['mode']='conservative'
        self.assertEqual(search_task(original,conservative,c,allow_detour=False)['status'],'no_solution_in_bounded_graph')
        result=search_task(original,w,c,allow_detour=False)
        self.assertEqual(result['status'],'found');plan=result['task']
        self.assertGreater(plan['actual_departure_time_s'],2)
        self.assertEqual(plan['mandatory_services'],original['mandatory_services'])
        self.assertEqual(plan['energy_assumptions'],original['energy_assumptions'])
        self.assertTrue(verify_candidate(candidate(w,plan),w,c)['feasible'])
        self.assertEqual(check_reference_task(plan,c)['conflicts'],[])
        self.assertEqual(result['planning_explanation']['waits'][0]['resource_id'],'pad_start')
        self.assertEqual(w['tasks'][0],original)

    def test_deadline_rejects_wait_but_detour_keeps_requirements(self):
        data=raw();t=data['observed_snapshot']['tasks'][0]
        t['deadline_s']=30;t['mandatory_services'][0]['window_s'][1]=25
        w=world(data);c=generate_constraints(w)
        self.assertEqual(search_task(t,w,c,allow_detour=False)['status'],'no_solution_in_bounded_graph')
        result=search_task(t,w,c,allow_detour=True,timeout_s=10)
        self.assertEqual(result['status'],'found')
        self.assertLessEqual(result['task']['trajectory'][-1]['time_s'],30)
        self.assertTrue(any(p['position_m'][1]!=60 for p in result['task']['trajectory']))
        self.assertTrue(verify_candidate(candidate(w,result['task']),w,c)['feasible'])

    def test_continuous_prohibition_not_released_by_geometric_gap(self):
        data=raw();data['observed_snapshot']['facilities'][0]['motion_model']['activity_policy']='recorded_envelope'
        w=world(data);c=generate_constraints(w);result=search_task(w['tasks'][0],w,c,allow_detour=False)
        self.assertEqual(result['status'],'no_solution_in_bounded_graph')
        self.assertTrue(any(row['permission_semantics']=='continuous_prohibition_not_released_by_geometric_gap'
                            for row in result['temporal_windows']['constraints']))
        data=raw();m=data['observed_snapshot']['facilities'][0]['motion_model']
        m['program']=[{'interval_s':[0,120],'start_pose':m['initial_pose'],'end_pose':m['initial_pose']}]
        w=world(data)
        self.assertEqual(search_task(w['tasks'][0],w,generate_constraints(w),allow_detour=False)['status'],'no_solution_in_bounded_graph')

    def test_declared_phase_error_invalidates_old_candidate_and_changes_checks(self):
        w=world();c=generate_constraints(w);plan=search_task(w['tasks'][0],w,c,allow_detour=False)['task']
        old=candidate(w,plan);m=deepcopy(w['facilities'][0]['motion_model']);m['uncertainty_bounds']['clock_s']=3
        updated=apply_event(w,{'id':'clock_uncertainty','kind':'facility_motion_changed','facility_id':'crane_1',
            'motion_model':m,'effective_at_s':0,'observed_at_s':0,'source_kind':'synthetic_declared_error_bound'})
        changed=generate_constraints(updated)
        self.assertFalse(verify_candidate(old,updated,changed)['feasible'])
        self.assertEqual(check_task(plan,changed,updated)['status'],'conflict')
        self.assertTrue(check_reference_task(plan,changed)['conflicts'])
        self.assertNotEqual(TemporalWindows(w['tasks'][0],w,c).key,TemporalWindows(updated['tasks'][0],updated,changed).key)

    def test_shared_waiting_pad_cannot_silently_double_book(self):
        w=world();c=generate_constraints(w);a=search_task(w['tasks'][0],w,c,allow_detour=False)['task']
        b=deepcopy(w['tasks'][0]);b['id']='competing_task';b['aircraft_id']='different_aircraft'
        # Both aircraft are explicitly on the same capacity-one pad at release.
        # No off-pad waiting position or instantaneous entry may be invented.
        result=search_task(b,w,c,reservations=[a],allow_detour=False)
        self.assertEqual(result['status'],'initial_pad_unavailable')
        self.assertTrue(check_joint_tasks([a,b],w)['conflicts'])

    def test_clear_nodes_do_not_allow_colliding_complete_edge(self):
        w=world();c=generate_constraints(w);task=w['tasks'][0];windows=TemporalWindows(task,w,c)
        p,q=[10,60,10],[80,60,10]
        self.assertFalse(windows.blocked_node(p));self.assertFalse(windows.blocked_node(q))
        # At t=11 a 10m/s flight departed x=10 at t=7 reaches x=50.
        # Before t=20 the rope is exactly at x=50,y=60 from z=5 to20.
        forbidden=windows.forbidden_departures(p,q,7)
        self.assertTrue(any(a<=7<b for a,b in forbidden))
        proxy={'id':'whole_edge','plan_version':1,'protection_radius_m':.3,
            'trajectory':[{'time_s':7,'position_m':p},{'time_s':14,'position_m':q}]}
        self.assertEqual(check_task(proxy,c,w)['status'],'conflict')
        self.assertTrue(check_reference_task(proxy,c)['conflicts'])

    def test_unknown_error_bounds_fall_back_and_budget_never_releases_partial_windows(self):
        data=raw();del data['observed_snapshot']['facilities'][0]['motion_model']['uncertainty_bounds']
        w=world(data);c=generate_constraints(w);result=search_task(w['tasks'][0],w,c,allow_detour=False)
        self.assertEqual(result['status'],'no_solution_in_bounded_graph')
        self.assertTrue(all(x['status']=='conservative_fallback_missing_uncertainty_bounds' for x in result['temporal_windows']['constraints']))
        w=world();w['planning_policy']['max_cells']=1
        result=search_task(w['tasks'][0],w,generate_constraints(w))
        self.assertEqual(result['status'],'search_budget_exhausted')
        self.assertEqual(result['budget_dimension'],'temporal_window_cells')
        self.assertNotIn('task',result)

    def test_cache_fingerprint_changes_for_each_input_version(self):
        w=world();c=generate_constraints(w);t=w['tasks'][0];first=TemporalWindows(t,w,c)
        self.assertTrue(TemporalWindows(t,w,c).cache_hit)
        for key in ('environment_version','reservation_version','rule_version','model_version'):
            edited=deepcopy(w);edited[key]='different'
            self.assertNotEqual(first.key,TemporalWindows(t,edited,c).key)
        result=search_task(t,w,c,allow_detour=False)
        self.assertEqual(result,search_task(t,w,c,allow_detour=False))

    def test_pause_freezes_occupancy_and_completion_keeps_physical_structure(self):
        w=world();pause={'id':'pause','kind':'activity_paused','activity_id':'lifting_1','effective_at_s':0,
                         'observed_at_s':0,'source_kind':'synthetic_hand_case'}
        paused=apply_event(w,pause)
        self.assertEqual(search_task(paused['tasks'][0],paused,generate_constraints(paused),allow_detour=False)['status'],'no_solution_in_bounded_graph')
        complete=apply_event(w,{**pause,'id':'complete','kind':'activity_completion_confirmed'})
        c=generate_constraints(complete)
        self.assertFalse(any(x['kind']=='activity' for x in c))
        self.assertEqual(len([x for x in c if x['kind']=='physical']),4)
        result=search_task(complete['tasks'][0],complete,c,allow_detour=False)
        self.assertEqual(result['status'],'found')
        self.assertTrue(verify_candidate(candidate(complete,result['task']),complete,c)['feasible'])

    def test_pad_capacity_and_physical_separation_are_distinct(self):
        w=world();p=w['pads'][0]['position_m'];task=w['tasks'][0]
        other=deepcopy(task);other['id']='landing'
        other['launch_pad_id']='pad_finish';other['landing_pad_id']='pad_start'
        other['trajectory']=[{'time_s':5,'position_m':[10,60,10]},{'time_s':10,'position_m':[10,60,0]}]
        one=_safe_intervals(p,task,w,[],[other]);w['pads'][0]['capacity']=2
        two=_safe_intervals(p,task,w,[],[other])
        self.assertFalse(any(a<=6<b for a,b in one))
        self.assertTrue(any(a<=6<b for a,b in two))
        self.assertFalse(any(a<=9.99<b for a,b in two))

    def test_hoist_window_enters_search_and_inflight_prefix_is_frozen(self):
        data=raw();m=data['observed_snapshot']['facilities'][0]['motion_model']
        m['program'][1]={'interval_s':[20,33],'start_pose':m['initial_pose'],
                         'end_pose':{'angle_deg':90.,'load_z_m':18.}}
        w=world(data);c=generate_constraints(w)
        result=search_task(w['tasks'][0],w,c,allow_detour=False)
        self.assertEqual(result['status'],'found')
        self.assertGreater(result['task']['actual_departure_time_s'],2)
        self.assertTrue(verify_candidate(candidate(w,result['task']),w,c)['feasible'])
        from chengkong.execution import replan_inflight,simulate_context,frozen_prefix
        w=visible_world(raw(),'dynamic_window',8);context=simulate_context(w)
        candidates=replan_inflight(w,generate_constraints(w),budget={'timeout_s':15})
        self.assertTrue(candidates)
        for proposal in candidates:
            self.assertTrue(proposal['review']['feasible'])
            self.assertEqual(frozen_prefix(proposal['tasks'][0],8),context['tasks']['task_window']['frozen_prefix'])

    def test_uncertainty_requires_nonnegative_explicit_sourced_bounds(self):
        model=raw()['observed_snapshot']['facilities'][0]['motion_model']
        for bounds in ({'position_m':-1,'clock_s':0,'source_ids':['record_initial']},
                       {'position_m':0,'clock_s':0,'source_ids':[]}):
            with self.assertRaises(ContractError):validate_motion(dict(model,uncertainty_bounds=bounds))


if __name__=='__main__':unittest.main()
