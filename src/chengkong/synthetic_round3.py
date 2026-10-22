"""Round-three independent layout families; visible features never consume truth.

Motion envelope parameters are conservatively represented by cylinders in the
learning corpus. Dynamic crane checking is evaluated separately, not claimed as
an unseen capability of these learned scores.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
import math
import random
import signal
import time

from . import synthetic as old
from .pair_keys import pair_key
from .analysis import check_task,check_joint_tasks
from .geometry import geometry_bounds
from .world import generate_constraints

FEATURE_VERSION='visible-round3-v1'
PAIR_FEATURES=old.PAIR_FEATURES+['nearest_route_xy_m','object_radius_m','object_height_m','pair_start_gap_s','pair_end_gap_s']
NODE_FEATURES=old.NODE_FEATURES+['nearest_route_xy_m','shared_pad_degree','shared_aircraft_degree','corridor_degree','constraint_degree','task_count','temporal_density']
ACTION_FEATURES=NODE_FEATURES+['action_keep','action_delay','action_detour','planned_remaining_s','constraint_count','max_object_radius_m','min_task_end_gap_s','postponement_slack_s']
QUANTILE_FEATURES=old.QUANTILE_FEATURES


def point_segment_xy(point,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];d=dx*dx+dy*dy
    u=max(0,min(1,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/d)) if d else 0
    return math.hypot(a[0]+u*dx-point[0],a[1]+u*dy-point[1])


def pair_features(task,constraint,world):
    row=old.pair_features(task,constraint,world)
    try:
        lo,hi=geometry_bounds(constraint['geometry']);center=[(a+b)/2 for a,b in zip(lo,hi)]
        nearest=min(point_segment_xy(center,a['position_m'],b['position_m']) for a,b in zip(task['trajectory'],task['trajectory'][1:]))
        radius=max(hi[0]-lo[0],hi[1]-lo[1])/2;height=hi[2]-lo[2]
    except (KeyError,TypeError,ValueError):nearest=radius=height=0
    row.update(nearest_route_xy_m=nearest,object_radius_m=radius,object_height_m=height,
        pair_start_gap_s=task['trajectory'][-1]['time_s']-constraint['interval_s'][0],
        pair_end_gap_s=constraint['interval_s'][1]-task['trajectory'][0]['time_s'])
    return row


def graph_features(world,constraints):
    tasks=world['tasks'];base=old.graph_features(world,constraints);n=len(tasks)
    edge_types={name:[[0.0]*n for _ in range(n)] for name in ('shared_pad','shared_aircraft','spacetime_corridor','shared_constraint')}
    relevant=[]
    for task in tasks:
        relevant.append({c['id'] for c in constraints if (p:=pair_features(task,c,world))['bbox_clearance_m']==0 and p['temporal_overlap_s']>0})
    for i,t in enumerate(tasks):
        for j,u in enumerate(tasks):
            if i==j:continue
            edge_types['shared_pad'][i][j]=float(bool({t['launch_pad_id'],t['landing_pad_id']}&{u['launch_pad_id'],u['landing_pad_id']}))
            edge_types['shared_aircraft'][i][j]=float(t['aircraft_id']==u['aircraft_id'])
            overlap=max(t['trajectory'][0]['time_s'],u['trajectory'][0]['time_s'])<min(t['trajectory'][-1]['time_s'],u['trajectory'][-1]['time_s'])
            edge_types['spacetime_corridor'][i][j]=float(overlap and base['adjacency'][i][j]>0)
            edge_types['shared_constraint'][i][j]=float(bool(relevant[i]&relevant[j]))
    adjacency=[[float(i==j)+sum(edge_types[k][i][j]*w for k,w in [('shared_pad',1),('shared_aircraft',2),('spacetime_corridor',1),('shared_constraint',.5)]) for j in range(n)] for i in range(n)]
    for i,(task,row) in enumerate(zip(tasks,base['node_features'])):
        row.update(nearest_route_xy_m=min((pair_features(task,c,world)['nearest_route_xy_m'] for c in constraints),default=1000),
            shared_pad_degree=sum(edge_types['shared_pad'][i]),shared_aircraft_degree=sum(edge_types['shared_aircraft'][i]),
            corridor_degree=sum(edge_types['spacetime_corridor'][i]),constraint_degree=sum(edge_types['shared_constraint'][i]),
            task_count=n,temporal_density=sum(max(task['trajectory'][0]['time_s'],t['trajectory'][0]['time_s'])<min(task['trajectory'][-1]['time_s'],t['trajectory'][-1]['time_s']) for t in tasks)-1)
    base.update(adjacency=adjacency,edge_types=edge_types,
        edge_semantics='visible shared pad/aircraft, overlapping corridor, shared conservative constraint; diagonal self=1; symmetric degree normalization')
    return base


def action_features(task,action,world,constraints,graph=None):
    graph=graph or graph_features(world,constraints)
    row=dict(graph['node_features'][graph['task_ids'].index(task['id'])]);ps=[pair_features(task,c,world) for c in constraints]
    remaining=max((c.get('planned_interval_s',c['interval_s'])[1]-world['as_of_s'] for c in constraints if c['kind']=='activity'),default=0)
    row.update(action_keep=float(action in ('keep','retain')),action_delay=float(action=='delay'),action_detour=float(action=='detour'),
        planned_remaining_s=remaining,constraint_count=len(constraints),max_object_radius_m=max((p['object_radius_m'] for p in ps),default=0),
        min_task_end_gap_s=min((p['pair_end_gap_s'] for p in ps),default=0),postponement_slack_s=task['deadline_s']-task['trajectory'][-1]['time_s']-max(0,remaining))
    return row


def split_templates(count,seed):
    if count<100:raise ValueError('Round3 needs >=100 independent templates to retain >=20 calibration groups')
    order=list(range(count));random.Random(seed+5831).shuffle(order)
    sizes=[count-int(count*.15)-int(count*.2)-int(count*.15),int(count*.15),int(count*.2),int(count*.15)]
    result={};offset=0
    for name,size in zip(('train','validation','calibration','test'),sizes):
        for index in order[offset:offset+size]:result[f'round3-family-{index:04d}']=name
        offset+=size
    return result


def make_event(seed,template_id,variant=0):
    event=old.make_event(seed+4000001,template_id+1000,variant);world=event['world']
    layout=random.Random(seed+19997*template_id);rng=random.Random(seed+19997*template_id+variant*103+3)
    world['rule_version']='rules-v2';world['reservation_version']=1
    world['scenario_id']='synthetic-round3-visible';world['bounds_m']={'min':[-300,-300,0],'max':[1000,1000,150]}
    world['as_of_s']=30.0
    # Rotate full geometry and flight paths together; independent families differ in orientation, scale and resources.
    theta=layout.uniform(-math.pi,math.pi);cx,cy=world['facilities'][0]['physical_geometry'][0]['center_xy_m']
    def rotate(p):
        x,y=p[0]-cx,p[1]-cy
        return [cx+x*math.cos(theta)-y*math.sin(theta),cy+x*math.sin(theta)+y*math.cos(theta),p[2]]
    for pad in world['pads']:pad['position_m']=rotate(pad['position_m'])
    for task in world['tasks']:
        for point in task['trajectory']:point['position_m']=rotate(point['position_m'])
        for service in task['mandatory_services']:service['position_m']=rotate(service['position_m'])
    # 1-3 sites, not duplicate snapshots of one fixed site. Truth is separated per activity.
    truth_by_activity={'lifting_1':{'actual_end_s':event['simulator_truth']['actual_end_s'],'actual_geometry':event['simulator_truth']['actual_geometry']}}
    for site in range(1,layout.randint(1,3)):
        center=[cx+layout.uniform(-130,130),cy+layout.uniform(-130,130)]
        fid=f'crane_{site+1}';aid=f'lifting_{site+1}'
        g={'type':'vertical_cylinder','center_xy_m':center,'radius_m':layout.uniform(12,65),'z_min_m':0,'z_max_m':layout.choice([20,45,80])}
        start=layout.uniform(20,280);end=start+layout.uniform(50,220)
        world['facilities'].append({'id':fid,'source_id':'record_initial','installed':True,'removal_confirmed':False,
            'physical_geometry':[dict(g,radius_m=3,z_max_m=layout.uniform(15,32))]})
        world['activities'].append({'id':aid,'facility_id':fid,'source_id':'record_initial','planned_interval_s':[start,end],
            'completion_status':'not_confirmed','completion_confirmed_at_s':None,'activity_envelope':None if rng.random()<.12 else g})
        truth_by_activity[aid]={'actual_geometry':g,'actual_end_s':end+rng.gauss(20,25)}
    pattern=layout.choice(['normal','late_record','missing_field','conflicting_records','pad_competition','inflight_change','wide_motion_envelope'])
    # Metadata identifies coverage only; neither IDs nor mode are in model feature names.
    if pattern=='missing_field':world['activities'][rng.randrange(len(world['activities']))]['activity_envelope']=None
    if pattern=='late_record':world['known_sources'][0]['observed_at_s']=29;world['known_sources'][0]['effective_at_s']=0
    if pattern=='conflicting_records':
        world['information_gaps'].append({'object_id':'lifting_1','field':'planned_interval_s','status':'conflict','values':[world['activities'][0]['planned_interval_s'],[45,400]],'source_ids':['record_initial','record_conflict']})
        world['known_sources'].append({'id':'record_conflict','kind':'synthetic_record','observed_at_s':25})
    if pattern=='inflight_change':world['as_of_s']=max(30,min(t['departure_time_s'] for t in world['tasks'])+5)
    if pattern=='wide_motion_envelope':
        for a in world['activities']:
            if a['activity_envelope']:a['activity_envelope']['radius_m']*=1.4
    # Conditional heteroskedastic end errors; no activity hidden field enters features.
    first=world['activities'][0];q=old.quantile_features(world);scale=8+.12*q['planned_duration_s']+.08*q['notice_delay_s']
    error=15+.22*q['planned_duration_s']+.4*q['notice_delay_s']+rng.gauss(0,scale)
    if layout.random()<.15:error+=rng.expovariate(1/55)
    truth_by_activity['lifting_1']['actual_end_s']=max(first['planned_interval_s'][0]+1,first['planned_interval_s'][1]+error)
    return {'event_id':f'r3-event-{template_id:04d}-{variant:02d}','template_family':f'round3-family-{template_id:04d}',
        'parent_template':f'round3-family-{template_id:04d}','variant':variant,'sequence_id':f'r3-sequence-{template_id:04d}-{variant:02d}',
        'coverage_mode':pattern,'world':world,'simulator_truth':{'activities':truth_by_activity,'future_outcome_not_visible':True}}


def solve_action(world,constraints,task,action,max_expansions=256,timeout_s=3):
    from .planning import search_task
    from .verification import _task_requirements
    tick=time.perf_counter();reservations=[t for t in world['tasks'] if t['id']!=task['id']]
    if task['departure_time_s'] < world['as_of_s'] and action != 'keep':
        return {'action':action,'feasible':False,'actual_cost':None,'solve_status':'unsupported_inflight_local_action',
            'failure_reasons':['Use frozen-prefix joint inflight planner; local static action must not rewrite execution'],
            'solve_seconds':time.perf_counter()-tick,'metrics':{}}

    class Timeout(Exception):pass
    def alarm(*_):raise Timeout()
    oldhandler=signal.signal(signal.SIGALRM,alarm);signal.setitimer(signal.ITIMER_REAL,timeout_s)
    try:
        result={'status':'found','task':deepcopy(task)} if action=='keep' else search_task(task,world,constraints,reservations,allow_detour=action=='detour',max_expansions=max_expansions)
        candidate=result.get('task');feasible=False;cost=None;metrics={};failure=[]
        if candidate:
            report=check_task(candidate,constraints,world);violations,unchecked,met=_task_requirements(candidate,task,world)
            joint=check_joint_tasks([candidate,*reservations],world)
            related=[x for x in joint['conflicts']+joint['unchecked'] if task['id'] in x.get('task_ids',[]) or x.get('task_id')==task['id']]
            feasible=report['status']=='clear' and not violations and not unchecked and not related
            failure=[x.get('reason',x.get('kind','unknown')) for x in violations+unchecked+related]+[x.get('kind','geometry_conflict') for x in report['conflicts']]
            length=old._route_length(candidate);delay=candidate['trajectory'][-1]['time_s']-task['trajectory'][-1]['time_s']
            metrics={'changed_tasks':int(candidate['trajectory']!=task['trajectory']),'delay_s':delay,'distance_m':length,
                'extra_distance_m':max(0,length-old._route_length(task)),'energy_used':met.get('energy_used',0)}
            if feasible:cost=delay+.02*metrics['extra_distance_m']+.02*metrics['energy_used']+.5*metrics['changed_tasks']
        return {'action':action,'feasible':feasible,'actual_cost':cost,'solve_status':result['status'],'failure_reasons':failure,
            'solve_seconds':time.perf_counter()-tick,'metrics':metrics}
    except Timeout:return {'action':action,'feasible':False,'actual_cost':None,'solve_status':'timeout','failure_reasons':['label_timeout'],'solve_seconds':time.perf_counter()-tick,'metrics':{}}
    finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,oldhandler)


def label_event(event,max_expansions=256,timeout_s=3):
    tick=time.perf_counter();world=event['world'];constraints=generate_constraints(world);graph=graph_features(world,constraints);pairs=[];direct={}
    for task in world['tasks']:
        direct[task['id']]=False
        for c in constraints:
            variants=[];actual=check_task(task,[c],world);direct[task['id']]|=bool(actual['conflicts'])
            if c['kind']=='activity' and c['object_id'] in event['simulator_truth']['activities']:
                truth=event['simulator_truth']['activities'][c['object_id']];activity=next(a for a in world['activities'] if a['id']==c['object_id'])
                for radius in (.8,1,1.2):
                    for end in (activity['planned_interval_s'][1],truth['actual_end_s'],world['horizon_s'][1]):
                        cc=deepcopy(c);cc['geometry']=deepcopy(truth['actual_geometry']);cc['geometry']['radius_m']*=radius
                        cc['interval_s'][1]=max(cc['interval_s'][0]+.01,min(world['horizon_s'][1],end))
                        variants.append(int(bool(check_task(task,[cc],world)['conflicts'])))
            else:variants=[int(bool(actual['conflicts']))]
            pairs.append({'pair_id':pair_key(task['id'],c['id']),'features':pair_features(task,c,world),'target':sum(variants)/len(variants),
                'observed_conflict':bool(actual['conflicts']),'observed_unchecked':bool(actual['unchecked']),
                'label_perturbation_count':len(variants)})
    joint=check_joint_tasks(world['tasks'],world)
    for conflict in joint['conflicts']:
        for tid in conflict.get('task_ids',[]):
            if tid in direct:direct[tid]=True
    graph['targets']=[float(direct[t]) for t in graph['task_ids']]
    graph['label_semantics']='Direct observed geometric hard conflict OR membership in actual joint resource/separation conflict; no artificial neighbor averaging'
    actions=[]
    for task in world['tasks']:
        group=[]
        for action in ('keep','delay','detour'):
            row=solve_action(world,constraints,task,action,max_expansions,timeout_s)
            row.update(query_id=f'{event["event_id"]}:{task["id"]}',features=action_features(task,action,world,constraints,graph));group.append(row)
        costs=sorted({round(a['actual_cost'],6) for a in group if a['feasible']})
        for row in group:row['relevance']=len(costs)-costs.index(round(row['actual_cost'],6)) if row['feasible'] else 0
        actions.extend(group)
    first=world['activities'][0]
    return {'event_id':event['event_id'],'template_family':event['template_family'],'parent_template':event['parent_template'],
        'variant':event['variant'],'sequence_id':event['sequence_id'],'coverage_mode':event['coverage_mode'],
        'pairs':pairs,'graph':graph,'actions':actions,'quantile':{'features':old.quantile_features(world),
            'target_end_error_s':event['simulator_truth']['activities'][first['id']]['actual_end_s']-first['planned_interval_s'][1]},
        'label_seconds':time.perf_counter()-tick,'label_scope':'Actual current hard checks and 9 perturbations; bounded local repair; no global infeasibility proof'}
