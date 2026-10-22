"""Finite-graph SIPP with continuous edge validation and explicit search limits.

Labels include service progress and synthetic energy. Waiting consumes a safe
node interval; airborne waiting is charged. Reservations are private to a
candidate. No optimality/completeness claim is made for this bounded graph.
"""
from __future__ import annotations

import copy
import heapq
import itertools
import math
import time

from .geometry import segment_occupancy, UnsupportedGeometry
from .analysis import check_task

EPS = 1e-6


def _distance(a, b):
    return math.dist(a, b)


def _merge(intervals):
    result = []
    for lo, hi in sorted(intervals):
        if result and lo <= result[-1][1] + EPS:
            result[-1][1] = max(result[-1][1], hi)
        else:
            result.append([lo, hi])
    return result


def _position(a, b, t):
    u = (t-a['time_s'])/(b['time_s']-a['time_s'])
    return [x+(y-x)*u for x,y in zip(a['position_m'],b['position_m'])]


def _reservation_collision(p, q, start, end, radius, reservations):
    for task in reservations:
        for a,b in zip(task['trajectory'],task['trajectory'][1:]):
            lo,hi=max(start,a['time_s']),min(end,b['time_s'])
            if hi <= lo:
                continue
            own_a={'position_m':p,'time_s':start}
            own_b={'position_m':q,'time_s':end}
            r0=[x-y for x,y in zip(_position(own_a,own_b,lo),_position(a,b,lo))]
            r1=[x-y for x,y in zip(_position(own_a,own_b,hi),_position(a,b,hi))]
            v=[y-x for x,y in zip(r0,r1)]
            vv=sum(x*x for x in v)
            u=max(0,min(1,-sum(x*y for x,y in zip(r0,v))/vv)) if vv else 0
            if sum((x+u*y)**2 for x,y in zip(r0,v)) <= (radius+task['protection_radius_m'])**2 + EPS:
                return True
    return False


def _pad_intervals(task):
    tr=task['trajectory']
    result=[]
    # Launch, including wait, and landing occupy pads for the complete vertical leg.
    for a,b in zip(tr,tr[1:]):
        if a['position_m'][:2]==tr[0]['position_m'][:2] and b['position_m'][:2]==tr[0]['position_m'][:2] and (a['position_m'][2]==0 or b['position_m'][2]==0):
            result.append((task['launch_pad_id'],a['time_s'],b['time_s']))
        if a['position_m'][:2]==tr[-1]['position_m'][:2] and b['position_m'][:2]==tr[-1]['position_m'][:2] and (a['position_m'][2]==0 or b['position_m'][2]==0):
            result.append((task['landing_pad_id'],a['time_s'],b['time_s']))
    return result


def _pad_collision(task,p,q,start,end,reservations,world):
    pad=None
    if p[2]==0: pad=task['launch_pad_id']
    if q[2]==0: pad=task['landing_pad_id']
    if pad is None: return False
    capacity=next((r['capacity'] for r in world['pads'] if r['id']==pad),1)
    rows=[(lo,hi) for other in reservations for other_pad,lo,hi in _pad_intervals(other) if other_pad==pad and start<hi and lo<end]
    times=sorted({start,end,*[max(start,lo) for lo,hi in rows],*[min(end,hi) for lo,hi in rows]})
    return any(sum(lo<=at<hi for lo,hi in rows)>=capacity for at in times[:-1])


def _geometry_bounds(g):
    if g['type']=='aabb': return g['min_m'],g['max_m']
    if g['type']=='vertical_cylinder':
        x,y=g['center_xy_m'];r=g['radius_m']
        return [x-r,y-r,g['z_min_m']],[x+r,y+r,g['z_max_m']]
    if g['type']=='capsule':
        r=g['radius_m'];a,b=g['start_m'],g['end_m']
        return [min(x,y)-r for x,y in zip(a,b)],[max(x,y)+r for x,y in zip(a,b)]
    raise UnsupportedGeometry(g['type'])


def build_route_graph(task,world,constraints,allow_detour=True):
    """Single cruise altitude, vertical pad edges, explicit service waypoints."""
    services=task['mandatory_services']
    altitudes={task['_cruise_altitude_m']} if '_cruise_altitude_m' in task else {p['position_m'][2] for p in task['trajectory'] if p['position_m'][2]>0}
    if len(altitudes)!=1 or any(s['position_m'][2] not in altitudes for s in services):
        return {'status':'unsupported_route_profile'}
    z=next(iter(altitudes)); pads={p['id']:p for p in world['pads']}
    start=tuple(task.get('_search_start_position_m',pads[task['launch_pad_id']]['position_m']));goal=tuple(pads[task['landing_pad_id']]['position_m'])
    nodes=[start,goal,(*start[:2],z),(*goal[:2],z)]
    nodes += [tuple(s['position_m']) for s in services]
    nodes += [tuple(p['position_m']) for p in task['trajectory'] if p['position_m'][2]>0]
    if allow_detour:
        margin=task['protection_radius_m']+10.0
        for c in constraints:
            lo,hi=_geometry_bounds(c['geometry'])
            if lo[2]-margin<=z<=hi[2]+margin:
                nodes += [(x,y,z) for x in [lo[0]-margin,hi[0]+margin] for y in [lo[1]-margin,hi[1]+margin]]
    nodes=list(dict.fromkeys(nodes))
    bounds=world['bounds_m'];r=task['protection_radius_m']
    nodes=[p for p in nodes if all(bounds['min'][i]+r<=p[i]<=bounds['max'][i]-r for i in [0,1]) and (p[2]==0 or bounds['min'][2]+r<=p[2]<=bounds['max'][2]-r)]
    if start not in nodes or goal not in nodes:return {'status':'unsupported_pad_bounds'}
    if len(nodes)>80:return {'status':'graph_budget_exhausted'}
    edges={i:[] for i in range(len(nodes))}
    for i,p in enumerate(nodes):
        for j,q in enumerate(nodes):
            if i==j:continue
            horizontal=p[2]==q[2] and p[2]>0
            vertical=p[:2]==q[:2] and (p==start or q==goal)
            if not (horizontal or vertical):continue
            # Direct mode preserves original horizontal route; graph has only original points.
            speed=task['nominal_horizontal_speed_mps'] if horizontal else task['nominal_vertical_speed_mps']
            edges[i].append((j,_distance(p,q)/speed))
    return {'status':'ready','nodes':nodes,'edges':edges,'start':nodes.index(start),'goal':nodes.index(goal)}


def _safe_intervals(p,task,world,constraints,reservations):
    start,end=world['horizon_s'];blocked=[];r=task['protection_radius_m']
    for c in constraints:
        if segment_occupancy(p,p,c['geometry'],r):blocked.append(c['interval_s'])
    # Dynamic reservations at this node; sphere intersection is an analytic quadratic.
    for other in reservations:
        rr=r+other['protection_radius_m']
        for a,b in zip(other['trajectory'],other['trajectory'][1:]):
            x=[v-w for v,w in zip(a['position_m'],p)]
            v=[v-w for v,w in zip(b['position_m'],a['position_m'])]
            aa=sum(t*t for t in v);bb=2*sum(s*t for s,t in zip(x,v));cc=sum(t*t for t in x)-rr*rr
            if aa==0:
                if cc<=0:blocked.append([a['time_s'],b['time_s']+EPS])
            else:
                disc=bb*bb-4*aa*cc
                if disc>=0:
                    lo=max(0,(-bb-math.sqrt(disc))/(2*aa));hi=min(1,(-bb+math.sqrt(disc))/(2*aa))
                    if lo<=hi:
                        d=b['time_s']-a['time_s'];blocked.append([a['time_s']+lo*d,a['time_s']+hi*d+EPS])
        if p[2]==0:
            pad=task['launch_pad_id'] if list(p)==task['trajectory'][0]['position_m'] else task['landing_pad_id']
            blocked.extend([[lo,hi] for pid,lo,hi in _pad_intervals(other) if pid==pad])
    result=[];cursor=start
    for lo,hi in _merge(blocked):
        lo=max(start,lo);hi=min(end,hi)
        if hi<start or lo>end:continue
        if lo>cursor:result.append([cursor,lo])
        cursor=max(cursor,hi)
    if cursor<end:result.append([cursor,end])
    return result


def search_task(task,world,constraints,reservations=(),allow_detour=True,max_expansions=10000,start_after_s=None,timeout_s=None):
    try:
        for constraint in constraints:
            _geometry_bounds(constraint['geometry'])
        return _search_task(task,world,constraints,reservations,allow_detour,max_expansions,start_after_s, None if timeout_s is None else time.monotonic()+max(0.,timeout_s))
    except (UnsupportedGeometry,TypeError,KeyError,ValueError) as exc:
        return {'status':'unsupported_or_incomplete_search_input','reason':str(exc)}


def _search_task(task,world,constraints,reservations,allow_detour,max_expansions,start_after_s,deadline):
    graph=build_route_graph(task,world,constraints,allow_detour)
    if graph['status']!='ready':return {'status':graph['status']}
    nodes=graph['nodes'];safe=[_safe_intervals(p,task,world,constraints,reservations) for p in nodes]
    start_time=task['departure_time_s'];end_limit=min(task['deadline_s'],world['horizon_s'][1])
    if start_time<world['as_of_s']:return {'status':'task_already_started'}
    si=next((i for i,(lo,hi) in enumerate(safe[graph['start']]) if lo<=start_time<hi),None)
    if si is None:return {'status':'initial_pad_unavailable'}
    search_start=max(start_time,start_after_s if start_after_s is not None else start_time)
    if search_start>=safe[graph['start']][si][1] or search_start>end_limit:
        return {'status':'ground_wait_interval_unavailable'}
    counter=itertools.count();queue=[];labels={};expansions=0
    energy=task['energy_assumptions'];capacity=energy['capacity']-energy['required_landing_reserve'];services=task['mandatory_services']
    initial_path=[{'time_s':start_time,'position_m':list(nodes[graph['start']])}]
    if search_start>start_time:
        initial_path.append({'time_s':search_start,'position_m':list(nodes[graph['start']])})
    initial_used=(search_start-start_time)*energy['ground_wait_cost_per_s']
    if search_start>start_time and nodes[graph['start']][2]==0 and not next((p.get('ground_wait_allowed',False) for p in world['pads'] if p['id']==task['launch_pad_id']),False):return {'status':'ground_wait_location_unavailable'}
    if initial_used>capacity:return {'status':'ground_wait_energy_exceeded'}
    heapq.heappush(queue,(search_start,next(counter),graph['start'],si,0,initial_used,initial_path))
    while queue:
        if deadline is not None and time.monotonic()>=deadline:return {'status':'search_timeout','expansions':expansions}
        at,_,node,interval,progress,used,path=heapq.heappop(queue)
        expansions+=1
        if expansions>max_expansions:return {'status':'search_budget_exhausted','expansions':expansions}
        p=nodes[node];safe_end=safe[node][interval][1]
        # Services must be performed in the original order and with full dwell.
        if progress<len(services) and _distance(p,services[progress]['position_m'])<=EPS:
            service=services[progress];begin=max(at,service['window_s'][0]);finish=begin+service['duration_s']
            if finish>service['window_s'][1] or finish>=safe_end or finish>end_limit:continue
            used+=(finish-at)*energy['airborne_cost_per_s']
            if used>capacity+EPS:continue
            path=copy.deepcopy(path)
            if finish>at:path.append({'time_s':finish,'position_m':list(p)})
            at=finish;progress+=1
        if node==graph['goal'] and progress==len(services):
            result=copy.deepcopy(task);result['trajectory']=path
            result.pop('ground_wait_interval_s',None)
            actual=start_time
            for a,b in zip(path,path[1:]):
                if b['position_m'][2]>0:actual=a['time_s'];break
            result['actual_departure_time_s']=actual
            if actual>start_time:result['ground_wait_interval_s']=[start_time,actual]
            result['planning_energy_used']=used
            return {'status':'found','task':result,'expansions':expansions,'node_count':len(nodes),'algorithm':'bounded_sipp_energy_labels_v1'}
        for target,duration in graph['edges'][node]:
            q=nodes[target]
            if target==graph['goal'] and progress<len(services):continue
            forbidden=[]
            for c in constraints:
                for u0,u1 in segment_occupancy(p,q,c['geometry'],task['protection_radius_m']):
                    forbidden.append((c['interval_s'][0]-u1*duration-EPS,c['interval_s'][1]-u0*duration))
            forbidden=_merge(forbidden)
            for target_interval,(lo,hi) in enumerate(safe[target]):
                depart=max(at,lo-duration);tries=0
                while depart+duration<min(hi,end_limit+EPS) and depart<safe_end:
                    if deadline is not None and time.monotonic()>=deadline:return {'status':'search_timeout','expansions':expansions}
                    jump=next((b for a,b in forbidden if a<=depart<b),None)
                    if jump is not None:depart=jump;continue
                    # Private trajectory reservations: bounded 1s retry after a continuous collision.
                    if _reservation_collision(p,q,depart,depart+duration,task['protection_radius_m'],reservations) or _pad_collision(task,p,q,depart,depart+duration,reservations,world):
                        depart+=1.0;tries+=1
                        if tries>1000:break
                        continue
                    break
                arrival=depart+duration
                if depart>=safe_end or arrival>=hi or arrival>end_limit or tries>1000:continue
                if depart>at+EPS and p[2]==0 and not next((pad.get('ground_wait_allowed',False) for pad in world['pads'] if list(p)==pad['position_m']),False):continue
                wait_cost=energy['ground_wait_cost_per_s'] if p[2]==0 else energy['airborne_cost_per_s']
                next_used=used+(depart-at)*wait_cost+duration*energy['airborne_cost_per_s']
                if next_used>capacity+EPS:continue
                key=(target,target_interval,progress);old=labels.setdefault(key,[])
                hover=energy['ground_wait_cost_per_s'] if q[2]==0 else energy['airborne_cost_per_s']
                if any(t<=arrival and e+(arrival-t)*hover<=next_used+EPS for t,e in old):continue
                old[:]=[(t,e) for t,e in old if not (arrival<=t and next_used+(t-arrival)*hover<=e+EPS)]
                old.append((arrival,next_used));new_path=copy.deepcopy(path)
                if depart>at+EPS:new_path.append({'time_s':depart,'position_m':list(p)})
                new_path.append({'time_s':arrival,'position_m':list(q)})
                heapq.heappush(queue,(arrival,next(counter),target,target_interval,progress,next_used,new_path))
    return {'status':'no_solution_in_bounded_graph','expansions':expansions,'node_count':len(nodes)}


def _ground_departures(task,constraints):
    options={float(task['departure_time_s'])}
    for a,b in zip(task['trajectory'],task['trajectory'][1:]):
        duration=b['time_s']-a['time_s']
        for c in constraints:
            for u0,_ in segment_occupancy(a['position_m'],b['position_m'],c['geometry'],task['protection_radius_m']):
                departure=c['interval_s'][1]-u0*duration-(a['time_s']-task['departure_time_s'])+EPS
                if task['departure_time_s']<departure<task['deadline_s']:
                    options.add(departure)
    return sorted(options)[:32]


def generate_candidates(world,constraints,diagnostics=None,budget=None,execution_context=None,planning_hints=None,ranking_hook=None):
    """Bounded cooperative search; all proposals still require independent review."""
    from .cooperation import cooperative_candidates
    return cooperative_candidates(world,constraints,diagnostics,budget,execution_context,planning_hints,ranking_hook)
