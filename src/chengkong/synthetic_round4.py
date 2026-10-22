"""Frozen independent scene/sequence identities, future records released at receipt.

No simulator outcome field is passed into planning/model feature functions.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,random,time
from pathlib import Path

PATTERNS=['moving_window','persistent_rule','uncertainty_arrival','completion_evidence','shared_resource','same_aircraft_inflight']

def sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def freeze(config):
    root=Path(config['data_dir']);root.mkdir(parents=True,exist_ok=True)
    file=root/'protocol-frozen.json'
    # Assign every pattern exactly once in each set. Each new parent has its own
    # independently drawn layout and motion parameters; no base-layout children
    # cross splits. Pattern names are coverage descriptors, not online features.
    records=[];idx=0
    if config.get('protocol_version')=='topology-v2':
        families=[('folded_services','folded route with two distinct mandatory service nodes', ['moving_window','uncertainty_arrival']),
                  ('dual_crane_cross','two moving cranes, four pads and crossing mission corridors',['moving_window','completion_evidence']),
                  ('aircraft_loop','three-pad triangular loop with three serial missions by one aircraft',['same_aircraft_inflight','uncertainty_arrival'])]
        for family,definition,patterns in families:
            for variant,pattern in enumerate(patterns):
                records.append({'template_id':f'round4-{family}-{variant:02d}','sequence_id':f'round4-sequence-{family}-{variant:02d}',
                    'parent_template':'round4-topology-'+family,'topology_key':family,'topology_definition':definition,
                    'variant':variant,'split':'test','coverage_pattern':pattern,'seed':config['seed']+idx*17011+500003})
                idx+=1
    for split,n in ({} if config.get('protocol_version')=='topology-v2' else config['split_templates']).items():
        for j in range(n):
            records.append({'template_id':f'round4-layout-{idx:03d}','sequence_id':f'round4-sequence-{idx:03d}',
                'parent_template':f'round4-layout-{idx:03d}','split':split,'coverage_pattern':PATTERNS[j%len(PATTERNS)],
                'seed':config['seed']+idx*17011,'decision_schedule':'initial plus three arrival groups; saved by generator'})
            idx+=1
    protocol={'schema_version':'round4-frozen-protocol-v1','created_at_unix_s':time.time(),'config':config,
        'templates':records,'final_test_ids':[r['template_id'] for r in records if r['split']=='test'],
        'group_unit':'Hand-defined parent topology and complete event sequence; every parameter derivative and snapshot stays in the same split.',
        'model_selection':'No new model/config selection; frozen round3 weights only as explicit OOD stress comparison.',
        'calibration':'none_not_fitted; zero calibration families in topology-v2; old coverage is inapplicable' if config.get('protocol_version')=='topology-v2' else 'Reserved diagnostic split only; no new calibration fitted',
        'generator_rule':'topology-v2: three explicit task/resource/construction graphs, each with two parameter variants, all test-only; old single topology protocol retained as observed diagnostic.' if config.get('protocol_version')=='topology-v2' else 'single dynamic-window topology parameter diagnostic; no independent-template claim',
        'strong_rule_status':'keep then detour then delay fixed before generation/test; round3 evidence remains posthoc'}
    protocol['identity_sha256']=sha({k:v for k,v in protocol.items() if k not in ('created_at_unix_s','identity_sha256')})
    if file.exists():
        old=json.loads(file.read_text())
        if old['identity_sha256']!=protocol['identity_sha256']:raise ValueError('Frozen protocol changed; use independent directory')
        return old
    file.write_text(json.dumps(protocol,indent=2));return protocol


def make_sequence(record):
    """Independently drawn layout/task/motion with four receipt-time decisions."""
    from .world import load_example,visible_world
    from .contracts import validate_example
    raw=load_example('examples/round4/dynamic_window.json');rng=random.Random(record['seed'])
    snap=raw['observed_snapshot'];m=snap['facilities'][0]['motion_model'];task=snap['tasks'][0]
    cx=rng.uniform(43,57);cy=rng.uniform(38,50);radius=rng.uniform(8,12);y=cy+radius
    start=rng.uniform(7,16);end=rng.uniform(84,94);service_x=end-rng.uniform(7,12)
    z=rng.uniform(9,12);tower=rng.uniform(20,24);speed=rng.uniform(8,12)
    hold=rng.uniform(18,26);rotate=rng.uniform(18,24);dwell=rng.uniform(2,4)
    m.update(base_m=[cx,cy,0],tower_height_m=tower,trolley_radius_m=radius,boom_length_m=radius+rng.uniform(3,5),
        max_slew_deg_per_s=90/rotate,max_hoist_mps=1,load_z_range_m=[4,tower-2])
    m['program'][0]['interval_s']=[0,hold];m['program'][1]['interval_s']=[hold,hold+rotate]
    m['uncertainty_bounds']={'position_m':rng.uniform(.03,.12),'clock_s':rng.uniform(.1,.4),'source_ids':['record_initial']}
    snap['facilities'][0]['physical_geometry'][0].update(center_xy_m=[cx,cy],z_max_m=tower)
    snap['activities'][0]['activity_envelope'].update(min_m=[cx-18,cy-18,0],max_m=[cx+18,cy+18,tower+2])
    snap['activities'][0]['planned_interval_s']=[0,45]
    snap['pads'][0]['position_m']=[start,y,0];snap['pads'][1]['position_m']=[end,y,0]
    task.update(nominal_horizontal_speed_mps=speed,departure_time_s=2,deadline_s=95)
    service=task['mandatory_services'][0];service.update(position_m=[service_x,y,z],duration_s=dwell,window_s=[2,82])
    t=2;tr=[{'time_s':t,'position_m':[start,y,0]}];t+=z/2;tr.append({'time_s':t,'position_m':[start,y,z]})
    t+=(service_x-start)/speed;tr.append({'time_s':t,'position_m':[service_x,y,z]});t+=dwell;tr.append({'time_s':t,'position_m':[service_x,y,z]})
    t+=(end-service_x)/speed;tr.append({'time_s':t,'position_m':[end,y,z]});t+=z/2;tr.append({'time_s':t,'position_m':[end,y,0]});task['trajectory']=tr
    task['energy_assumptions']['capacity']=t-2+rng.uniform(12,24)
    pattern=record['coverage_pattern']
    if pattern in ('persistent_rule','completion_evidence'):m['activity_policy']='recorded_envelope'
    if pattern=='completion_evidence':
        # A future task crosses after the recorded planned end. Hypothetical
        # completion changes the activity prohibition but never removes the
        # physical crane. The later confirmation is a real simulated record.
        snap['activities'][0]['planned_interval_s']=[0,10]
        task['departure_time_s']+=33
        for point in task['trajectory']:point['time_s']+=33
        task['mandatory_services'][0]['window_s']=[35,82]
    # Every independently drawn layout includes a second, distant static site;
    # its input remains fully checked even when screened clear.
    snap['facilities'].append({'id':'site_2','source_id':'record_initial','installed':True,'removal_confirmed':False,
        'physical_geometry':[{'type':'vertical_cylinder','center_xy_m':[rng.uniform(15,25),rng.uniform(15,20)],'radius_m':2,'z_min_m':0,'z_max_m':12}]})
    if pattern in ('shared_resource','same_aircraft_inflight'):
        second=deepcopy(task);second['id']='task_followup';second['mandatory_services'][0]['id']='service_followup';second['priority']=2
        if pattern=='shared_resource':
            shift=1.5;second['aircraft_id']='uav_2'
            snap['waiting_areas']=[{'id':'wait_launch','position_m':[start,y,0],'capacity':1}]
        else:
            shift=t+7;second['aircraft_id']=task['aircraft_id'];second['launch_pad_id']='pad_finish';second['landing_pad_id']='pad_start'
            for point in second['trajectory']:point['position_m'][0]=start+end-point['position_m'][0]
            second['mandatory_services'][0]['position_m'][0]=start+end-service_x
            second['energy_assumptions']['capacity']=task['energy_assumptions']['capacity']=110
            snap['aircraft_turnaround_model']={'ground_transfer_speed_mps':20.,'ground_transfer_energy_per_m':.01,'turnaround_s':5.,'recharge_rate_per_s':.5}
        for point in second['trajectory']:point['time_s']+=shift
        second['departure_time_s']+=shift;second['mandatory_services'][0]['window_s']=[second['departure_time_s'],95]
        second['deadline_s']=110;snap['tasks'].append(second)
    def ev(identifier,kind,effective,arrival,**payload):
        return {'id':record['sequence_id']+':'+identifier,'kind':kind,'effective_at_s':effective,'observed_at_s':arrival,'source_kind':'synthetic_record',**payload}
    if pattern in ('moving_window','same_aircraft_inflight'):
        events=[ev('pause','activity_paused',8,9,activity_id='lifting_1'),ev('resume','activity_resumed',14,14,activity_id='lifting_1'),
                ev('completion','activity_completion_confirmed',60,66,activity_id='lifting_1')];times=[0,9,14,66]
    elif pattern=='uncertainty_arrival':
        changed=deepcopy(m);changed['uncertainty_bounds']['clock_s']=4;changed['uncertainty_bounds']['position_m']=.6
        events=[ev('motion_uncertainty','facility_motion_changed',7,8,facility_id='crane_1',motion_model=changed),
                ev('plan_extension','planned_end_changed',12,15,activity_id='lifting_1',new_planned_end_s=70),
                ev('completion','activity_completion_confirmed',70,74,activity_id='lifting_1')];times=[0,8,15,74]
    else:
        events=[ev('plan_extension','planned_end_changed',6,8,activity_id='lifting_1',new_planned_end_s=65),
                ev('late_pause','activity_paused',12,18,activity_id='lifting_1'),
                ev('completion','activity_completion_confirmed',32,38,activity_id='lifting_1')];times=[0,8,18,38]
    raw['description']='Round4 explicitly synthetic independent local layout and complete arrival sequence; no real device record.'
    raw['scenario_definitions_for_simulator_only']=[{'id':record['sequence_id'],'probability':None,'events':events}]
    if record.get('topology_key'):apply_topology(raw,record)
    raw['planned_check_points']=times;validate_example(raw)
    return {'schema_version':'round4-sequence-v1',**record,'input':raw,'decision_times_s':times,'input_sha256':sha(raw),
        'data_kind':'synthetic','future_records_scope':'Only evaluator holds input simulator event list; world/model functions receive already released records.',
        'execution_scope':'Counterfactual software incumbent trajectory replay; historical violations separately disqualify executed-task credit; no real flight.'}


def generate(config):
    protocol=freeze(config);root=Path(config['data_dir']);out=[];started=time.perf_counter()
    for record in protocol['templates']:
        item=make_sequence(record);file=root/(record['template_id']+'.json')
        if file.exists() and json.loads(file.read_text())!=item:raise ValueError('Frozen sequence input changed: '+str(file))
        if not file.exists():file.write_text(json.dumps(item,indent=2))
        out.append({'template_id':record['template_id'],'split':record['split'],'sha256':sha(item),'path':str(file)})
    manifest={'protocol_identity_sha256':protocol['identity_sha256'],'sequences':out,'generation_seconds':time.perf_counter()-started,
        'independent_parent_topologies':len({r['parent_template'] for r in protocol['templates']}),'parameter_sequences':len(out),'event_decisions':len(out)*4,'future_truth_in_online_world':False}
    path=root/'data-manifest.json'
    if not path.exists():path.write_text(json.dumps(manifest,indent=2))
    return manifest


def route_for(task,start,end,services,release):
    z=services[0]['position_m'][2];horizontal=task['nominal_horizontal_speed_mps'];vertical=task['nominal_vertical_speed_mps']
    t=release;tr=[{'time_s':t,'position_m':list(start)}];t+=z/vertical;position=[*start[:2],z];tr.append({'time_s':t,'position_m':position})
    import math
    for service in services:
        t+=math.dist(position,service['position_m'])/horizontal;position=list(service['position_m']);tr.append({'time_s':t,'position_m':position})
        t+=service['duration_s'];tr.append({'time_s':t,'position_m':list(position)})
    target=[*end[:2],z];t+=math.dist(position,target)/horizontal;tr.append({'time_s':t,'position_m':target});t+=z/vertical;tr.append({'time_s':t,'position_m':list(end)})
    task.update(trajectory=tr,mandatory_services=services,departure_time_s=release)
    return t


def apply_topology(raw,record):
    """Three different parent graphs, not relabeled rotations of one route."""
    snap=raw['observed_snapshot'];base=deepcopy(snap['tasks'][0]);pads=snap['pads'];m=snap['facilities'][0]['motion_model'];cx,cy,_=m['base_m'];z=base['mandatory_services'][0]['position_m'][2]
    key=record['topology_key'];snap['tasks']=[base];snap.pop('waiting_areas',None)
    if key=='folded_services':
        service=deepcopy(base['mandatory_services'][0]);service['id']='fold_service';service['position_m']=[cx-18,cy-10,z];service['window_s']=[base['departure_time_s'],82]
        services=[service,*base['mandatory_services']]
        finish=route_for(base,pads[0]['position_m'],pads[1]['position_m'],services,base['departure_time_s'])
        base['energy_assumptions']['capacity']=finish-base['departure_time_s']+15
    elif key=='dual_crane_cross':
        second=deepcopy(snap['facilities'][0]);second['id']='crane_cross';second['motion_model']['activity_id']='lifting_cross'
        mx,my=cx+22,cy+10;second['motion_model']['base_m']=[mx,my,0];second['physical_geometry'][0]['center_xy_m']=[mx,my]
        snap['facilities'].append(second);activity=deepcopy(snap['activities'][0]);activity.update(id='lifting_cross',facility_id='crane_cross')
        activity['activity_envelope'].update(min_m=[mx-18,my-18,0],max_m=[mx+18,my+18,m['tower_height_m']+2]);snap['activities'].append(activity)
        pads.extend([{'id':'pad_south','position_m':[mx,8,0],'capacity':1,'ground_wait_allowed':True},
                     {'id':'pad_north','position_m':[mx,94,0],'capacity':1,'ground_wait_allowed':True}])
        task=deepcopy(base);task.update(id='task_cross',aircraft_id='uav_cross',launch_pad_id='pad_south',landing_pad_id='pad_north',priority=2)
        service=deepcopy(base['mandatory_services'][0]);service.update(id='service_cross',position_m=[mx,84,z],window_s=[3,82])
        finish=route_for(task,pads[-2]['position_m'],pads[-1]['position_m'],[service],3);task['energy_assumptions']['capacity']=finish-3+18;snap['tasks'].append(task)
        # Second-site events are independent known records released with each
        # arrival, not an extra hidden source provided to the solver.
        for scenario in raw['scenario_definitions_for_simulator_only']:
            scenario['events'] += [dict(deepcopy(e),id=e['id']+':cross',activity_id='lifting_cross') for e in list(scenario['events']) if 'activity_id' in e]
    elif key=='aircraft_loop':
        pads.append({'id':'pad_north_loop','position_m':[cx,93,0],'capacity':1,'ground_wait_allowed':True})
        snap['aircraft_turnaround_model']={'ground_transfer_speed_mps':20.,'ground_transfer_energy_per_m':.01,'turnaround_s':5.,'recharge_rate_per_s':.5}
        base['energy_assumptions']['capacity']=150
        previous=base
        for index,(a,b) in enumerate(((pads[1],pads[2]),(pads[2],pads[0])),start=1):
            task=deepcopy(base);release=previous['trajectory'][-1]['time_s']+8
            task.update(id=f'task_loop_{index}',launch_pad_id=a['id'],landing_pad_id=b['id'],priority=index+1,deadline_s=118)
            service=deepcopy(base['mandatory_services'][0]);service.update(id=f'service_loop_{index}',position_m=[a['position_m'][0]+.8*(b['position_m'][0]-a['position_m'][0]),a['position_m'][1]+.8*(b['position_m'][1]-a['position_m'][1]),z],window_s=[release,112])
            route_for(task,a['position_m'],b['position_m'],[service],release);snap['tasks'].append(task);previous=task
    else:raise ValueError('Unsupported topology family')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--config',default='configs/evaluation-round4.json');a=p.parse_args()
    c=json.loads(Path(a.config).read_text());print(json.dumps(generate(c)))
