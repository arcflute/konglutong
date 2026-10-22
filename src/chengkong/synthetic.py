"""Seeded synthetic event families. Truth is never accepted by feature functions.

All model features are computed from decision-visible snapshots. Stored labels
are produced by geometry/solver calls or simulator-only end-time outcomes.
"""
from __future__ import annotations
from copy import deepcopy
import math
import random
import time

from .analysis import check_joint_tasks, check_task
from .geometry import geometry_bounds
from .world import generate_constraints

PAIR_FEATURES = ['bbox_clearance_m', 'temporal_overlap_s', 'route_length_m',
    'flight_duration_s', 'height_clearance_m', 'geometry_missing', 'end_unconfirmed',
    'source_age_s', 'planned_remaining_s', 'task_slack_s', 'protection_radius_m',
    'priority', 'constraint_is_activity']
NODE_FEATURES = ['min_bbox_clearance_m', 'max_temporal_overlap_s', 'geometry_missing',
    'end_unconfirmed', 'task_slack_s', 'priority', 'shared_resource_degree',
    'route_length_m', 'flight_duration_s', 'energy_margin', 'source_age_s']
ACTION_FEATURES = NODE_FEATURES + ['action_keep', 'action_delay', 'action_detour',
    'planned_remaining_s', 'constraint_count']
QUANTILE_FEATURES = ['planned_duration_s', 'elapsed_s', 'source_age_s',
    'notice_delay_s', 'radius_m', 'geometry_missing']
FEATURE_VERSION = 'visible-v2.1'


def _route_length(task):
    return sum(math.dist(a['position_m'], b['position_m'])
               for a,b in zip(task['trajectory'], task['trajectory'][1:]))


def pair_features(task, constraint, world):
    points = [p['position_m'] for p in task['trajectory']]
    a = [min(p[k] for p in points) for k in range(3)]
    b = [max(p[k] for p in points) for k in range(3)]
    missing = constraint.get('geometry') is None
    try:
        low,high = geometry_bounds(constraint['geometry'], task['protection_radius_m'])
        clearance = math.sqrt(sum(max(low[k]-b[k], a[k]-high[k], 0)**2 for k in range(3)))
        zclear = max(low[2]-b[2], a[2]-high[2])
    except (KeyError,TypeError,ValueError):
        missing,clearance,zclear = True,0.0,0.0
    t0,t1 = task['trajectory'][0]['time_s'],task['trajectory'][-1]['time_s']
    c0,c1 = constraint['interval_s']
    planned = constraint.get('planned_interval_s', [c0,c1])
    values = [clearance,max(0,min(t1,c1)-max(t0,c0)),_route_length(task),t1-t0,
        zclear,float(missing),float(constraint.get('completion_status')=='not_confirmed'),
        max(0,world['as_of_s']-constraint.get('observed_at_s',0)),
        planned[1]-world['as_of_s'],task['deadline_s']-t1,
        task['protection_radius_m'],task['priority'],float(constraint.get('kind')=='activity')]
    return dict(zip(PAIR_FEATURES,values))


def _relations(tasks):
    """Visible conservative task graph: temporal corridors and shared resources."""
    n=len(tasks); adjacency=[[float(i==j) for j in range(n)] for i in range(n)]
    for i,left in enumerate(tasks):
        for j in range(i+1,n):
            right=tasks[j]
            resource=(bool({left['launch_pad_id'],left['landing_pad_id']} &
                {right['launch_pad_id'],right['landing_pad_id']}) or
                left['aircraft_id']==right['aircraft_id'])
            overlap=max(left['trajectory'][0]['time_s'],right['trajectory'][0]['time_s']) < min(left['trajectory'][-1]['time_s'],right['trajectory'][-1]['time_s'])
            lp=[p['position_m'] for p in left['trajectory']];rp=[p['position_m'] for p in right['trajectory']]
            margin=left['protection_radius_m']+right['protection_radius_m']
            corridor=all(max(min(p[k] for p in lp),min(p[k] for p in rp)) <= min(max(p[k] for p in lp),max(p[k] for p in rp))+margin for k in range(3))
            adjacency[i][j]=adjacency[j][i]=float(resource or (overlap and corridor))
    return adjacency


def graph_features(world,constraints):
    tasks=world['tasks'];adjacency=_relations(tasks); rows=[]
    for i,task in enumerate(tasks):
        pairs=[pair_features(task,c,world) for c in constraints]
        def aggregate(name,op,default=0):return op([p[name] for p in pairs],default=default)
        duration=task['trajectory'][-1]['time_s']-task['trajectory'][0]['time_s']
        e=task['energy_assumptions']
        values=[aggregate('bbox_clearance_m',min,1000),aggregate('temporal_overlap_s',max),
            aggregate('geometry_missing',max),aggregate('end_unconfirmed',max),task['deadline_s']-task['trajectory'][-1]['time_s'],
            task['priority'],sum(adjacency[i])-1,_route_length(task),duration,
            e['capacity']-e['required_landing_reserve']-duration*e['airborne_cost_per_s'],
            aggregate('source_age_s',max)]
        rows.append(dict(zip(NODE_FEATURES,values)))
    return {'task_ids':[t['id'] for t in tasks],'node_features':rows,'adjacency':adjacency}


def action_features(task,action,world,constraints,graph=None):
    graph=graph or graph_features(world,constraints)
    row=dict(graph['node_features'][graph['task_ids'].index(task['id'])])
    row.update(action_keep=float(action in ('keep','retain')),action_delay=float(action=='delay'),
        action_detour=float(action=='detour'),constraint_count=len(constraints),
        planned_remaining_s=max((c.get('planned_interval_s',c['interval_s'])[1]-world['as_of_s'] for c in constraints if c.get('kind')=='activity'),default=0))
    return row


def quantile_features(world):
    activity=world['activities'][0];g=activity.get('activity_envelope') or {}
    start,end=activity['planned_interval_s']
    return dict(zip(QUANTILE_FEATURES,[end-start,world['as_of_s']-start,
        world['as_of_s']-world['known_sources'][-1]['observed_at_s'],
        activity.get('observed_delay_notice_s',0),g.get('radius_m',0),float(not g)]))


def make_event(seed,template_id,variant=0):
    """Each template owns layout, timing and delay-pattern family; variants stay together."""
    layout=random.Random(seed+10007*template_id)
    rng=random.Random(seed+10007*template_id+97*variant+1)
    cx=layout.uniform(210,390);cy=layout.uniform(160,400)
    length=layout.uniform(240,430);height=layout.choice([35,50,70])
    radius=layout.uniform(18,78)+rng.uniform(-4,4)
    n=layout.randint(2,5);shared=layout.random()<.55
    start=layout.choice([45,110,210,320]);duration=layout.uniform(40,180)
    notice=layout.choice([0,0,30,60]);planned_end=start+duration+notice
    missing=rng.random()<.13
    geometry={'type':'vertical_cylinder','center_xy_m':[cx,cy],'radius_m':radius,
        'z_min_m':0,'z_max_m':layout.choice([25,55,85])}
    world={'schema_version':'world-v1','scenario_id':'visible-synthetic-event',
        'as_of_s':30.0,'environment_version':1,'rule_version':'rules-v1','model_version':'none',
        'data_kind':'synthetic','is_real_case':False,'horizon_s':[0,800],
        'coordinate_system':{'type':'local_cartesian','unit':'m','height_datum':'local_flat_ground_z0'},
        'time_system':{'type':'relative_simulation_seconds','interval_convention':'[start,end)'},
        'bounds_m':{'min':[0,0,0],'max':[800,650,120]},
        'known_sources':[{'id':'record_initial','kind':'synthetic_record','observed_at_s':rng.uniform(0,25)}],
        'buildings':[],'pads':[], 'tasks':[], 'scene_relations':[], 'event_log':[],
        'facilities':[{'id':'crane_1','source_id':'record_initial','installed':True,'removal_confirmed':False,
            'physical_geometry':[{'type':'vertical_cylinder','center_xy_m':[cx,cy],'radius_m':3,'z_min_m':0,'z_max_m':20}]}],
        'activities':[{'id':'lifting_1','facility_id':'crane_1','source_id':'record_initial',
            'planned_interval_s':[start,planned_end],'completion_status':'not_confirmed',
            'completion_confirmed_at_s':None,'activity_envelope':None if missing else deepcopy(geometry),
            'observed_delay_notice_s':notice}], 'information_gaps':[]}
    for i in range(n):
        y=cy+(0 if shared else (i-(n-1)/2)*layout.uniform(25,70))+rng.uniform(-2,2)*(not shared)
        p0=[cx-length/2,y,0];p1=[cx+length/2,y,0]
        pad_number=0 if shared else i
        if not shared or i==0:
            for direction,p in [('west',p0),('east',p1)]:
                world['pads'].append({'id':f'pad_{direction}_{pad_number}','position_m':p,'capacity':1,'ground_wait_allowed':True})
        else:
            p0=deepcopy(world['pads'][0]['position_m']);p1=deepcopy(world['pads'][1]['position_m'])
        depart=layout.choice([40,90,180,300])+i*layout.choice([0,4,65])+rng.uniform(0,3)
        speed=layout.choice([8,10,12]);up=height/5;flight=length/speed;service=rng.choice([4,8,12])
        arrival=depart+2*up+flight+service
        slack=layout.choice([5,20,80,180]);deadline=min(790,arrival+slack)
        points=[(depart,p0),(depart+up,[p0[0],p0[1],height]),
            (depart+up+flight,[p1[0],p1[1],height]),
            (depart+up+flight+service,[p1[0],p1[1],height]),(arrival,p1)]
        world['tasks'].append({'id':f'task_{i}','aircraft_id':f'uav_{i}','plan_version':1,
            'priority':layout.choice([0,1,2]),'departure_time_s':depart,'deadline_s':deadline,
            'launch_pad_id':f'pad_west_{pad_number}','landing_pad_id':f'pad_east_{pad_number}',
            'mandatory_services':[{'id':f'service_{i}','position_m':[p1[0],p1[1],height],
                'duration_s':service,'position_tolerance_m':1,'window_s':[depart,deadline-up]}],
            'trajectory':[{'time_s':t,'position_m':p} for t,p in points],
            'nominal_horizontal_speed_mps':speed,'nominal_vertical_speed_mps':5,'protection_radius_m':3,
            'energy_assumptions':{'unit':'synthetic_energy_unit','capacity':layout.choice([70,100,180,300]),
                'airborne_cost_per_s':1,'ground_wait_cost_per_s':0,'required_landing_reserve':12}})
    # Full event truth stays out of world, features and runtime interfaces.
    delay=rng.gauss(12+.20*duration+.6*notice,12+.18*duration)
    if layout.random()<.25:delay+=rng.expovariate(1/70)
    truth={'actual_end_s':max(start+1,planned_end+delay),'actual_geometry':geometry,
        'delay_pattern':'synthetic_heteroskedastic_mixture','future_outcome_not_visible':True}
    return {'event_id':f'event-{template_id:03d}-{variant:02d}',
        'template_family':f'layout-event-template-{template_id:03d}',
        'world':world,'simulator_truth':truth}


def split_templates(template_count,seed):
    if template_count<20:raise ValueError('At least 20 independent template families required')
    order=list(range(template_count));random.Random(seed+431).shuffle(order)
    nval=max(3,template_count//6);ncal=max(4,template_count//5);ntest=max(4,template_count//5)
    edges=[template_count-nval-ncal-ntest,template_count-ncal-ntest,template_count-ntest,template_count]
    result={};last=0
    for split,end in zip(['train','validation','calibration','test'],edges):
        for i in order[last:end]:result[f'layout-event-template-{i:03d}']=split
        last=end
    return result


def label_event(event,max_expansions=256,solve_timeout_s=5):
    """Solver outcomes are targets only. No model receives this function's labels."""
    from .planning import search_task
    from .verification import _task_requirements
    import signal
    world=event['world'];constraints=generate_constraints(world);graph=graph_features(world,constraints)
    pair_rows=[];node_direct=[];started=time.monotonic()
    truth=event['simulator_truth']
    for task in world['tasks']:
        task_labels=[]
        for c in constraints:
            perturbations=[]
            if c['kind']=='activity':
                # Parameter perturbation / simulated end alternatives are labels, not inputs.
                for radius_factor in (.75,1.0,1.25):
                    for end in (world['activities'][0]['planned_interval_s'][1],truth['actual_end_s'],world['horizon_s'][1]):
                        cc=deepcopy(c);cc['geometry']=deepcopy(truth['actual_geometry']);cc['geometry']['radius_m']*=radius_factor
                        cc['interval_s'][1]=max(cc['interval_s'][0]+.01,min(800,end))
                        perturbations.append(int(bool(check_task(task,[cc],world)['conflicts'])))
            else:perturbations=[int(bool(check_task(task,[c],world)['conflicts']))]
            # Proportion of perturbed scenarios conflicting; neither accident probability nor safety score.
            target=sum(perturbations)/len(perturbations)
            task_labels.append(target)
            pair_rows.append({'pair_id':f'{task["id"]}:{c["id"]}',
                'features':pair_features(task,c,world),'target':target,
                'label_conflict_count':sum(perturbations),'label_perturbation_count':len(perturbations)})
        node_direct.append(max(task_labels,default=0))
    joint=check_joint_tasks(world['tasks'],world)
    for conflict in joint['conflicts']:
        for tid in conflict.get('task_ids',[]):
            node_direct[graph['task_ids'].index(tid)]=max(.8,node_direct[graph['task_ids'].index(tid)])
    adjacency=graph['adjacency']
    graph['targets']=[.65*node_direct[i]+.35*sum(a*v for a,v in zip(adjacency[i],node_direct))/sum(adjacency[i]) for i in range(len(node_direct))]
    action_rows=[]
    class LabelTimeout(Exception):pass
    def alarm(_signum,_frame):raise LabelTimeout()
    for task in world['tasks']:
        # Local repair labels: other trajectories are fixed reservations; unrelated
        # violations do not make this task's successful repair label negative.
        reservations=[t for t in world['tasks'] if t['id']!=task['id']]
        for action in ['keep','delay','detour']:
            tick=time.monotonic();result={'status':'pending'};modified=None
            handler=signal.signal(signal.SIGALRM,alarm);signal.setitimer(signal.ITIMER_REAL,solve_timeout_s)
            try:
                if action=='keep':result={'status':'found','task':deepcopy(task)}
                else:result=search_task(task,world,constraints,reservations,
                    allow_detour=action=='detour',max_expansions=max_expansions)
                modified=result.get('task')
            except LabelTimeout:result={'status':'timeout'}
            finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,handler)
            feasible=False;cost=None;failure=[]
            if modified:
                report=check_task(modified,constraints,world)
                violations,unchecked,metrics=_task_requirements(modified,task,world)
                joint_check=check_joint_tasks([modified,*reservations],world)
                target_joint=[c for c in joint_check['conflicts'] if task['id'] in c.get('task_ids',[])]
                target_unchecked=[u for u in joint_check['unchecked'] if task['id']==u.get('task_id') or task['id'] in u.get('task_ids',[])]
                feasible=(report['status']=='clear' and not violations and not unchecked and not target_joint and not target_unchecked)
                failure=[v.get('reason',v.get('kind')) for v in violations+unchecked+target_joint+target_unchecked]
                if feasible:
                    cost=(modified['trajectory'][-1]['time_s']-task['trajectory'][-1]['time_s']+
                        .02*max(0,_route_length(modified)-_route_length(task))+
                        .02*metrics['energy_used']+.5*(modified['trajectory']!=task['trajectory']))
            action_rows.append({'query_id':f'{event["event_id"]}:{task["id"]}','action':action,
                'features':action_features(task,action,world,constraints,graph),
                'feasible':feasible,'actual_cost':cost,'solve_status':result['status'],
                'failure_reasons':failure,'solve_seconds':time.monotonic()-tick})
    for task in world['tasks']:
        rows=[a for a in action_rows if a['query_id'].endswith(':'+task['id'])]
        good=sorted([a for a in rows if a['feasible']],key=lambda a:(a['actual_cost'],a['action']))
        for row in rows:row['relevance']=len(good)-good.index(row) if row in good else 0
    return {'event_id':event['event_id'],'template_family':event['template_family'],
        'pairs':pair_rows,'graph':graph,'actions':action_rows,
        'quantile':{'features':quantile_features(world),
            'target_end_error_s':truth['actual_end_s']-world['activities'][0]['planned_interval_s'][1]},
        'label_seconds':time.monotonic()-started,
        'label_scope':'bounded local repair against fixed other tasks; no global optimum proof'}
