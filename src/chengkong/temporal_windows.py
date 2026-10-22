"""Conservative time tubes for bounded SIPP, independent of departure sampling.

For every obstacle cell [a,b], its midpoint geometry enlarged by V*(b-a)/2
contains every moving solid in that cell. If route fraction [u0,u1] intersects
that tube, every departure in [a-u1*T, b-u0*T] is forbidden. Taking complements
therefore certifies an entire moving edge, not just its endpoints. This is a
sufficient condition and may discard feasible correlated space-time passages.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import math
import threading
import time

from .geometry import segment_occupancy, geometry_bounds
from .motion import component_speed, geometry_at, motion_breakpoints, pose_at

EPS = 1e-6
_CACHE = OrderedDict()
_LOCK = threading.RLock()


class WindowBudgetExceeded(RuntimeError):
    pass


def policy(world):
    config = dict(world.get('planning_policy') or {})
    config.setdefault('mode', 'conservative')
    config.setdefault('max_time_step_s', 1.)
    config.setdefault('max_cell_inflation_m', .25)
    config.setdefault('max_cells', 30000)
    if config['mode'] not in ('conservative', 'dynamic_windows'):
        raise ValueError('unsupported planning_policy.mode')
    for key, upper in (('max_time_step_s', 5.), ('max_cell_inflation_m', 5.)):
        if not isinstance(config[key], (int,float)) or isinstance(config[key],bool) or not math.isfinite(config[key]) or not 0 < config[key] <= upper:
            raise ValueError('invalid temporal window '+key)
    if not isinstance(config['max_cells'],int) or isinstance(config['max_cells'],bool) or not 1 <= config['max_cells'] <= 200000:
        raise ValueError('temporal max_cells must be an integer in [1,200000]')
    return config


def _merge(rows):
    merged = []
    for a,b in sorted(rows):
        if merged and a <= merged[-1][1]+EPS:
            merged[-1][1] = max(b,merged[-1][1])
        else:
            merged.append([a,b])
    return merged


def _deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('temporal window construction exceeded search timeout')


def cache_fingerprint(task,world,constraints,reservations):
    payload = {'schema':'temporal-tubes-v1','policy':policy(world), 'task':task,
               'constraints':constraints, 'reservations':reservations,
               'versions':{k:world.get(k) for k in ('environment_version','rule_version','model_version',
                            'reservation_version','scenario_id','as_of_s','horizon_s')},
               'resources': [world.get('pads',[]),world.get('waiting_areas',[])]}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class TemporalWindows:
    def __init__(self,task,world,constraints,reservations=(),deadline=None):
        self.config=policy(world); self.radius=task['protection_radius_m']
        self.key=cache_fingerprint(task,world,constraints,reservations)
        self.deadline=deadline; self.edge_cache={}; self.node_cache={}
        self.edge_queries=0; self.node_queries=0; self.cache_hit=False
        start=time.monotonic()
        with _LOCK:
            cached=_CACHE.get(self.key)
            if cached is not None:
                self.rows,self.details=cached; _CACHE.move_to_end(self.key);self.cache_hit=True
        if not self.cache_hit:
            self.rows=[];self.details=[];raw_cells=0
            for c in constraints:
                _deadline(deadline)
                lo=max(c['interval_s'][0],world['horizon_s'][0])
                hi=min(c['interval_s'][1],world['horizon_s'][1],task['deadline_s'])
                if lo>=hi:continue
                descriptor=c.get('motion'); status='static_continuous_prohibition'
                if descriptor:
                    if descriptor.get('motion_status')!='supported':
                        raise ValueError('unsupported or unknown motion: '+str(c['id']))
                    status=('dynamic_bounded_motion' if descriptor['model'].get('uncertainty_bounds') is not None
                            else 'conservative_fallback_missing_uncertainty_bounds')
                cells=[];max_error=0.;unknown=0;review_guard=0.
                if status!='dynamic_bounded_motion':
                    if raw_cells>=self.config['max_cells']:
                        raise WindowBudgetExceeded('temporal window cell budget exhausted; no partial intervals released')
                    cells=[(lo,hi,c['geometry'],0.)]
                else:
                    model=descriptor['model'];speed=component_speed(descriptor)
                    # The primary reviewer may cut cells at different segment
                    # boundaries. Two of its maximal midpoint errors bound the
                    # extra displacement of that enclosure from the true solid.
                    review_guard=2*model.get('max_cell_inflation_m',.5) if speed else 0.
                    dt=min(self.config['max_time_step_s'],model.get('max_time_step_s',1.))
                    inflation=min(self.config['max_cell_inflation_m'],model.get('max_cell_inflation_m',.5))
                    if speed:dt=min(dt,2*inflation/speed)
                    else:dt=hi-lo
                    cuts=motion_breakpoints(descriptor,lo,hi)
                    for left,right in zip(cuts,cuts[1:]):
                        n=max(1,math.ceil((right-left)/dt))
                        if raw_cells+len(cells)+n>self.config['max_cells']:
                            raise WindowBudgetExceeded('temporal window cell budget exhausted; no partial intervals released')
                        for i in range(n):
                            if i%64==0:_deadline(deadline)
                            a=left+(right-left)*i/n;b=left+(right-left)*(i+1)/n;mid=(a+b)/2
                            unknown_pose=pose_at(descriptor,mid) is None
                            error=0. if unknown_pose else speed*(b-a)/2
                            cells.append((a,b,geometry_at(descriptor,mid),error+review_guard))
                            max_error=max(max_error,error);unknown+=unknown_pose
                raw_cells+=len(cells)
                compact=[]
                for a,b,g,error in cells:
                    if compact and compact[-1][2]==g and abs(compact[-1][3]-error)<=1e-10 and abs(compact[-1][1]-a)<=EPS:
                        old=compact[-1];compact[-1]=(old[0],b,g,max(error,old[3]))
                    else:compact.append((a,b,g,error))
                self.rows.extend((a,b,g,error,c['id'],*geometry_bounds(g,self.radius+error)) for a,b,g,error in compact)
                self.details.append({'constraint_id':c['id'],'object_id':c['object_id'],'kind':c['kind'],
                    'status':status,'cells':len(cells),'interval_s':[lo,hi],'max_cell_inflation_m':max_error,
                    'unknown_pose_envelope_cells':unknown,'independent_partition_review_guard_m':review_guard,'source_ids':c.get('source_ids',[]),
                    'uncertainty_bounds':descriptor['model'].get('uncertainty_bounds') if descriptor else None,
                    'permission_semantics':('time_varying_physical_or_explicit_moving_protection' if descriptor else
                        'continuous_prohibition_not_released_by_geometric_gap')})
            with _LOCK:
                _CACHE[self.key]=(self.rows,self.details)
                while len(_CACHE)>16:_CACHE.popitem(last=False)
        self.build_seconds=time.monotonic()-start

    def blocked_node(self,p):
        key=tuple(p);self.node_queries+=1
        if key not in self.node_cache:
            blocks=[]
            for i,(a,b,g,error,cid,lo,hi) in enumerate(self.rows):
                if i%256==0:_deadline(self.deadline)
                if any(x < lower-EPS or x > upper+EPS for x,lower,upper in zip(p,lo,hi)):continue
                if segment_occupancy(p,p,g,self.radius+error):blocks.append((a-EPS,b+EPS))
            self.node_cache[key]=_merge(blocks)
        return self.node_cache[key]

    def forbidden_departures(self,p,q,duration):
        key=(tuple(p),tuple(q),duration);self.edge_queries+=1
        if key not in self.edge_cache:
            blocks=[]
            for i,(a,b,g,error,cid,lo,hi) in enumerate(self.rows):
                if i%256==0:_deadline(self.deadline)
                if any(max(x,y) < lower-EPS or min(x,y) > upper+EPS for x,y,lower,upper in zip(p,q,lo,hi)):continue
                for u0,u1 in segment_occupancy(p,q,g,self.radius+error):
                    blocks.append((a-u1*duration-EPS,b-u0*duration+EPS))
            self.edge_cache[key]=_merge(blocks)
        return self.edge_cache[key]

    def explain_path(self,task,world):
        passages=[];waits=[]
        for a,b in zip(task['trajectory'],task['trajectory'][1:]):
            p,q=a['position_m'],b['position_m'];start,end=a['time_s'],b['time_s']
            if p==q:
                pad=next((x['id'] for x in world['pads'] if x['position_m']==p),None)
                waits.append({'position_m':p,'interval_s':[start,end],
                    'resource_id':pad,'kind':'ground_pad_wait' if p[2]==0 else 'airborne_wait_or_required_service',
                    'energy_used':(end-start)*task['energy_assumptions']['ground_wait_cost_per_s' if p[2]==0 else 'airborne_cost_per_s']})
                continue
            duration=end-start;low=world['horizon_s'][0];high=min(task['deadline_s'],world['horizon_s'][1])-duration
            for left,right in self.forbidden_departures(p,q,duration):
                if right<=start:low=max(low,right)
                elif left>start:high=min(high,left);break
            passages.append({'start_m':p,'end_m':q,'interval_s':[start,end],
                'allowed_departure_interval_s':[low,high],
                'scope':'whole linear segment against every supplied object constraint; reservations additionally checked',
                'reservation_window_not_implied':True})
        return {'mode':'dynamic_windows','passages':passages,'waits':waits,
            'physical_and_protection_constraints':[d for d in self.details if d['status']=='dynamic_bounded_motion'],
            'continuous_prohibitions':[d['constraint_id'] for d in self.details if d['status']=='static_continuous_prohibition'],
            'fallback_constraints':[d['constraint_id'] for d in self.details if d['status'].startswith('conservative_fallback')],
            'service_requirements_preserved':task['mandatory_services'],
            'unconfirmed_completion_releases_nothing':True}

    def describe(self):
        return {'mode':'dynamic_windows','method':'continuous_edge_vs_velocity_bounded_time_tubes',
            'cache_fingerprint':self.key,'cache_implemented':True,'cache_binding':
            'environment/rule/model/scenario/knowledge time/horizon/task/constraint/reservation/resource/policy fingerprints',
            'cells':len(self.rows),'node_queries':self.node_queries,
            'edge_queries':self.edge_queries,'constraints':self.details,'numerical_tolerance_s':EPS,
            'max_time_step_s':self.config['max_time_step_s'],'max_cell_inflation_m':self.config['max_cell_inflation_m'],
            'guarantee':'conservative sufficient windows on a finite graph; no completeness or optimality claim',
            'boundary_policy':'closed spatial contact and uncertain temporal boundaries remain blocked'}
