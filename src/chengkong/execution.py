"""Software-only execution snapshots and suffix replanning; no flight control."""
import copy
import math

from .resources import task_energy,aircraft_continuity

EPS=1e-7


def frozen_prefix(task,at_s):
    points=task['trajectory']
    prefix=[copy.deepcopy(p) for p in points if p['time_s']<=at_s]
    if not prefix:return []
    if prefix[-1]['time_s']<at_s<points[-1]['time_s']:
        b=next(p for p in points if p['time_s']>at_s);a=prefix[-1]
        u=(at_s-a['time_s'])/(b['time_s']-a['time_s'])
        prefix.append({'time_s':at_s,'position_m':[x+u*(y-x) for x,y in zip(a['position_m'],b['position_m'])]})
    return prefix


def simulate_context(world,time_s=None):
    at=world['as_of_s'] if time_s is None else float(time_s)
    rows={}
    continuity=aircraft_continuity(world['tasks'],world)
    initial_energy={r['task_ids'][1]:r['energy_at_next_start'] for r in continuity['transitions']}
    for task in world['tasks']:
        prefix=frozen_prefix(task,at)
        status='not_started' if not prefix else 'completed' if at>=task['trajectory'][-1]['time_s'] else 'in_progress'
        used=task_energy(dict(task,trajectory=prefix)) if len(prefix)>1 else 0.
        rows[task['id']]={'status':status,'frozen_prefix':prefix,
            'position_m':prefix[-1]['position_m'] if prefix else None,
            'energy_used':used,'remaining_energy':initial_energy.get(task['id'],task['energy_assumptions']['capacity'])-used}
    return {'kind':'software_simulation_only','at_s':at,'environment_version':world['environment_version'],
        'base_plan_versions':{t['id']:t['plan_version'] for t in world['tasks']},'tasks':rows}


def validate_context(context,world):
    """Never trust caller-supplied position, prefix or optimistic remaining energy."""
    expected=simulate_context(world)
    return context==expected


def suffix_task(task,context):
    state=context['tasks'][task['id']];prefix=state['frozen_prefix'];at=context['at_s']
    result=copy.deepcopy(task)
    if state['status']!='in_progress':return result
    result['trajectory']=[copy.deepcopy(prefix[-1])]+[copy.deepcopy(p) for p in task['trajectory'] if p['time_s']>at]
    result['departure_time_s']=at
    result['_search_start_position_m']=list(state['position_m'])
    # Original route profile, not interpolated ascent height, determines cruise.
    result['_cruise_altitude_m']=max(p['position_m'][2] for p in task['trajectory'])
    result['energy_assumptions']['capacity']=state['remaining_energy']
    services=[];cursor=task['departure_time_s']
    for service in task['mandatory_services']:
        intervals=[]
        for a,b in zip(prefix,prefix[1:]):
            if math.dist(a['position_m'],b['position_m'])<=EPS and math.dist(a['position_m'],service['position_m'])<=service['position_tolerance_m']+EPS:
                if intervals and abs(intervals[-1][1]-a['time_s'])<=EPS:intervals[-1][1]=b['time_s']
                else:intervals.append([a['time_s'],b['time_s']])
        credited=0.
        for begin,end in intervals:
            start=max(cursor,begin,service['window_s'][0]);finish=min(end,service['window_s'][1])
            if finish-start>=service['duration_s']-EPS:
                credited=service['duration_s'];cursor=start+credited;break
            if abs(finish-at)<=EPS and finish>start:
                credited=finish-start;cursor=finish
        if credited<service['duration_s']-EPS:
            remaining=copy.deepcopy(service);remaining['duration_s']-=credited;services.append(remaining)
    result['mandatory_services']=services
    result.pop('ground_wait_interval_s',None);result.pop('actual_departure_time_s',None)
    return result


def restore_full_task(original,suffix,context):
    state=context['tasks'][original['id']]
    if state['status']!='in_progress':return suffix
    result=copy.deepcopy(original)
    result['trajectory']=copy.deepcopy(state['frozen_prefix'])+copy.deepcopy(suffix['trajectory'][1:])
    result['planning_energy_used']=state['energy_used']+suffix.get('planning_energy_used',0.)
    result['execution_resume_at_s']=context['at_s']
    # Full history retains its actual takeoff/ground wait metadata.
    return result


def replan_inflight(world,constraints,diagnostics=None,budget=None,planning_hints=None):
    from .planning import generate_candidates
    return generate_candidates(world,constraints,diagnostics,budget,simulate_context(world),planning_hints)
