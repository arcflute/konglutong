"""Hand-computable crane cases; no solver-generated reference labels."""
from copy import deepcopy
from pathlib import Path
import math
import unittest

from chengkong.analysis import check_task
from chengkong.contracts import ContractError
from chengkong.geometry import segment_occupancy
from chengkong.incremental import DependencyEngine
from chengkong.motion import (validate_motion, motion_snapshot, motion_descriptor,
                              pose_at, geometry_at, occupancy_intervals)
from chengkong.planning import search_task
from chengkong.reference_geometry import check_reference_task
from chengkong.verification import verify_candidate
from chengkong.world import load_example,visible_world,generate_constraints,apply_event,advance_world

EXAMPLE=Path(__file__).resolve().parents[1]/'examples/park_crane_synthetic.json'


def model():
    return {'schema_version':'crane-motion-v1','kind':'tower_crane','activity_id':'lifting_1',
        'source_ids':['record_initial'],'base_m':[500,400,0], 'tower_height_m':60,'tower_radius_m':2,
        'boom_length_m':40,'boom_radius_m':.2,'trolley_radius_m':30,'rope_radius_m':.05,'load_radius_m':.5,
        'angle_range_deg':[-90,90],'load_z_range_m':[10,55], 'max_slew_deg_per_s':1.8,'max_hoist_mps':.5,
        'initial_pose':{'angle_deg':-90,'load_z_m':30},'parking_pose':{'angle_deg':90,'load_z_m':55},
        'program':[{'interval_s':[100,200],'start_pose':{'angle_deg':-90,'load_z_m':30},
                    'end_pose':{'angle_deg':90,'load_z_m':30}}],
        'outside_program_policy':'conservative_range','activity_policy':'moving_components_with_clearance',
        'activity_clearance_m':2,'max_time_step_s':1,'max_cell_inflation_m':.1}


def world_at(now=60, crane=None):
    raw=load_example(EXAMPLE);raw['observed_snapshot']['facilities'][0]['motion_model']=model() if crane is None else crane
    raw['observed_snapshot']['activities'][0]['planned_interval_s']=[100,360]
    return visible_world(raw,'end_unconfirmed',now)


def event(kind,at,**extra):
    return {'id':'test_'+kind+str(at),'kind':kind,'effective_at_s':at,'observed_at_s':at,
            'source_kind':'synthetic_hand_computable_case',**extra}


def task(point,start=100,end=200,radius=.1):
    return {'id':'hand_case','plan_version':1,'protection_radius_m':radius,
            'trajectory':[{'time_s':start,'position_m':point},{'time_s':end,'position_m':point}]}


def physical(world,component):
    return next(c for c in generate_constraints(world) if c['kind']=='physical' and c.get('motion',{}).get('component')==component)


class MotionTests(unittest.TestCase):
    def test_horizontal_sweep_crosses_between_clear_endpoints(self):
        world=world_at();c=physical(world,'boom');p=[520,400,60]
        for at in (100,200):
            self.assertEqual(segment_occupancy(p,p,geometry_at(c['motion'],at),.1),[])
        # At t=150 the arm lies along +x and includes (520,400,60).
        report=check_task(task(p),[c],world)
        self.assertEqual(report['status'],'conflict')
        self.assertTrue(any(a['interval_s'][0]<=150<=a['interval_s'][1] for a in report['conflicts']))
        self.assertTrue(check_reference_task(task(p),[c])['conflicts'])
        self.assertLessEqual(report['conflicts'][0]['dynamic_check']['max_inflation_m'],.1+1e-10)

    def test_same_envelope_but_wrong_time_and_wrong_side_are_clear(self):
        world=world_at();c=physical(world,'boom')
        # Full-range broad box contains the west point; the -90..90 degree
        # physical boom always lies to the east of x=500, so 20m west is clear.
        p=[480,400,60]
        self.assertTrue(segment_occupancy(p,p,c['geometry'],.1))
        self.assertEqual(check_task(task(p),[c],world)['status'],'clear')
        self.assertFalse(check_reference_task(task(p),[c])['conflicts'])
        self.assertEqual(check_task(task([520,400,60],100,105),[c],world)['status'],'clear')

    def test_hoist_crosses_between_samples_with_hand_computed_height(self):
        m=model();m['initial_pose']={'angle_deg':0,'load_z_m':20}
        m['program']=[{'interval_s':[100,140],'start_pose':deepcopy(m['initial_pose']),
                       'end_pose':{'angle_deg':0,'load_z_m':40}}]
        world=world_at(crane=m);c=physical(world,'load');p=[530,400,30]
        # Half of 40s at 0.5m/s gives z=30 at t=120.
        self.assertEqual(pose_at(c['motion'],120)['load_z_m'],30)
        report=check_task(task(p,100,140),[c],world)
        self.assertTrue(any(r['interval_s'][0]<=120<=r['interval_s'][1] for r in report['conflicts']))
        self.assertTrue(check_reference_task(task(p,100,140),[c])['conflicts'])

    def test_fast_rotation_is_not_endpoint_sampling(self):
        m=model();m['max_slew_deg_per_s']=180;m['program'][0]['interval_s']=[100,101]
        m['max_time_step_s']=5;m['max_cell_inflation_m']=.2
        world=world_at(crane=m);c=physical(world,'boom')
        rows,detail=occupancy_intervals([520,400,60],[520,400,60],100,101,c,.01)
        self.assertTrue(any(lo<=100.5<=hi for lo,hi in rows))
        self.assertGreater(detail['cells_checked'],100)
        self.assertLessEqual(detail['max_inflation_m'],.2+1e-10)

    def test_pause_hold_resume_phase_and_unconfirmed_end(self):
        paused=apply_event(world_at(400),event('activity_paused',150,activity_id='lifting_1'))
        c=physical(paused,'boom')
        self.assertEqual(pose_at(c['motion'],399)['angle_deg'],0)
        self.assertEqual(check_task(task([520,400,60],380,390),[c],paused)['status'],'conflict')
        self.assertEqual(paused['activities'][0]['completion_status'],'not_confirmed')
        resumed=apply_event(paused,event('activity_resumed',180,activity_id='lifting_1'))
        self.assertAlmostEqual(pose_at(physical(resumed,'boom')['motion'],200)['angle_deg'],36)

    def test_same_time_pause_resume_order_matches_world_replay(self):
        world=world_at(400)
        pause={**event('activity_paused',150,activity_id='lifting_1'),'id':'z_pause'}
        resume={**event('activity_resumed',150,activity_id='lifting_1'),'id':'a_resume'}
        # Receipt order differs from effective/observed/ID replay order.
        updated=apply_event(apply_event(world,pause),resume)
        self.assertEqual(updated['activities'][0]['operational_status'],'paused')
        self.assertAlmostEqual(motion_snapshot(updated,190)['facilities'][0]['pose']['angle_deg'],0)

    def test_completion_moves_to_park_continuously_and_tower_remains(self):
        complete=apply_event(world_at(300),event('activity_completion_confirmed',160,activity_id='lifting_1'))
        c=physical(complete,'boom')
        self.assertAlmostEqual(pose_at(c['motion'],160)['angle_deg'],18)
        self.assertAlmostEqual(pose_at(c['motion'],170)['angle_deg'],36)
        self.assertAlmostEqual(pose_at(c['motion'],210)['angle_deg'],90)
        self.assertTrue(all(c['interval_s'][1]<=160 for c in generate_constraints(complete) if c['kind']=='activity'))
        self.assertEqual(physical(complete,'tower')['interval_s'],[0,900])
        self.assertEqual(motion_snapshot(complete,200)['facilities'][0]['activity_clearance_m'],0)
        self.assertGreater(motion_snapshot(complete,150)['facilities'][0]['activity_clearance_m'],0)
        removed=apply_event(complete,event('facility_removal_confirmed',250,facility_id='crane_1'))
        self.assertEqual(physical(removed,'tower')['interval_s'],[0,250])
        self.assertEqual(motion_snapshot(removed,260)['facilities'],[])

    def test_unknown_parking_and_after_program_keep_range_occupied(self):
        m=model();m['parking_pose']=None
        complete=apply_event(world_at(300,m),event('activity_completion_confirmed',160,activity_id='lifting_1'))
        c=physical(complete,'boom')
        self.assertIsNone(pose_at(c['motion'],250))
        self.assertEqual(check_task(task([480,400,60],250,260),[c],complete)['status'],'conflict')
        unconfirmed=world_at(400)
        self.assertIsNone(motion_snapshot(unconfirmed)['facilities'][0]['pose'])

    def test_parking_target_revision_starts_at_update_not_old_completion(self):
        complete=apply_event(world_at(400),event('activity_completion_confirmed',160,activity_id='lifting_1'))
        m=model();m['parking_pose']={'angle_deg':-90,'load_z_m':55}
        updated=apply_event(complete,event('facility_motion_changed',300,facility_id='crane_1',motion_model=m))
        # Old target reached 90 degrees before update. New target cannot cause
        # an instantaneous 180-degree jump by reusing completion time 160.
        self.assertAlmostEqual(motion_snapshot(updated,300)['facilities'][0]['pose']['angle_deg'],90)
        self.assertAlmostEqual(motion_snapshot(updated,310)['facilities'][0]['pose']['angle_deg'],72)
        self.assertAlmostEqual(motion_snapshot(updated,400)['facilities'][0]['pose']['angle_deg'],-90)

    def test_missing_or_unsupported_model_not_silently_static(self):
        for m in (None,dict(model(),boom_length_m=None),dict(model(),kind='articulated_crane')):
            world=world_at(crane=model())
            updated=apply_event(world,event('facility_motion_changed',60,facility_id='crane_1',motion_model=m))
            c=[r for r in generate_constraints(updated) if r['object_id']=='crane_1' and r['interval_s'][0]>=60]
            report=check_task(task([1,1,1]),c,updated)
            self.assertEqual(report['status'],'not_checked')
            self.assertTrue(report['unchecked'])
            result=search_task(updated['tasks'][0],updated,generate_constraints(updated))
            self.assertEqual(result['status'],'unsupported_or_incomplete_search_input')

    def test_speed_and_discontinuous_program_rejected_atomically(self):
        world=world_at();before=deepcopy(world)
        for m in (dict(model(),max_slew_deg_per_s=.1),dict(model(),initial_pose={'angle_deg':0,'load_z_m':30})):
            with self.assertRaises(ContractError):
                apply_event(world,event('facility_motion_changed',60,facility_id='crane_1',motion_model=m))
        self.assertEqual(world,before)

    def test_late_motion_update_preserves_valid_intervals_and_old_snapshot(self):
        world=world_at(400);old=deepcopy(world);new=model();new['boom_length_m']=50
        updated=apply_event(world,{**event('facility_motion_changed',200,facility_id='crane_1',motion_model=new),'observed_at_s':400})
        rows=[c for c in generate_constraints(updated) if c['kind']=='physical' and c.get('motion',{}).get('component')=='boom']
        self.assertEqual([r['interval_s'] for r in rows],[[0,200],[200,900]])
        self.assertEqual([r['motion']['model']['boom_length_m'] for r in rows],[40,50])
        self.assertEqual(world,old)
        self.assertEqual(motion_snapshot(updated,150)['facilities'][0]['components'][1]['geometry']['radius_m'],.2)
        self.assertEqual(motion_snapshot(updated,450)['facilities'][0]['data_kind'],'parameterized_rule_prediction')

    def test_incremental_matches_full_after_pause_and_candidates_recheck_motion(self):
        world=world_at(400);engine=DependencyEngine();engine.analyze(world)
        updated=apply_event(world,event('activity_paused',150,activity_id='lifting_1'))
        inc=engine.update(updated);constraints=generate_constraints(updated)
        self.assertEqual(inc['baseline_analysis'],{t['id']:check_task(t,constraints,updated) for t in updated['tasks']})
        candidate={'id':'manual_motion','tasks':deepcopy(updated['tasks']),
            'base_environment_version':updated['environment_version'],
            'base_plan_versions':{t['id']:t['plan_version'] for t in updated['tasks']},
            'scenario_id':updated['scenario_id'],'rule_version':updated['rule_version'],
            'model_version':updated['model_version'],'valid_interval_s':updated['horizon_s']}
        review=verify_candidate(candidate,updated,constraints)
        self.assertEqual(review['status'],'rejected')
        self.assertIn('motion_check_scope',review)
        self.assertTrue(any(c.get('dynamic_check') for r in review['task_reports'] for c in r['conflicts']))
        # Supplying only static constraints cannot bypass regenerated motion.
        altered=verify_candidate(candidate,updated,[c for c in constraints if 'motion' not in c])
        self.assertTrue(any(r['reason']=='constraint_set_incomplete_or_modified' for r in altered['unchecked']))


if __name__=='__main__':
    unittest.main()
