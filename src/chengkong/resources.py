"""Explicit synthetic aircraft reuse and finite stationary ground resources.

Ground transfer is handling by a fictional ground carrier, not a flight leg. It
has configured time/energy, followed by turnaround and optional charging. The
landing pad stays occupied until handling leaves; the launch pad is occupied
from carrier arrival until the next flight. No battery reset or teleport occurs.
"""
from __future__ import annotations

import math

EPS = 1e-8


def task_energy(task):
    e = task['energy_assumptions']
    return sum((b['time_s']-a['time_s'])*
               (e['ground_wait_cost_per_s'] if a['position_m'][2] == b['position_m'][2] == 0
                else e['airborne_cost_per_s'])
               for a,b in zip(task['trajectory'],task['trajectory'][1:]))


def aircraft_continuity(tasks, world):
    result = {'conflicts': [], 'unchecked': [], 'transitions': [], 'reservations': []}
    groups = {}
    for task in tasks:
        groups.setdefault(task['aircraft_id'], []).append(task)
    for aircraft, rows in groups.items():
        rows.sort(key=lambda t:(t['trajectory'][0]['time_s'],t['id']))
        if len(rows) < 2:
            continue
        for left,right in zip(rows,rows[1:]):
            if left['trajectory'][-1]['time_s']>right['trajectory'][0]['time_s']:
                result['conflicts'].append({'kind':'aircraft_double_booking','task_ids':[left['id'],right['id']],
                    'aircraft_id':aircraft,'interval_s':[right['trajectory'][0]['time_s'],left['trajectory'][-1]['time_s']]})
        model = world.get('aircraft_turnaround_model')
        if not model:
            result['unchecked'].append({'kind':'aircraft_turnaround_unmodeled',
                'task_ids':[t['id'] for t in rows], 'aircraft_id':aircraft,
                'reason':'No explicit ground-transfer, turnaround and charging model configured.'})
            continue
        try:
            speed=float(model['ground_transfer_speed_mps'])
            cost=float(model['ground_transfer_energy_per_m'])
            turn=float(model['turnaround_s']); charge=float(model['recharge_rate_per_s'])
            if not all(math.isfinite(v) for v in (speed,cost,turn,charge)) or speed<=0 or min(cost,turn,charge)<0:
                raise ValueError('invalid turnaround model')
        except (KeyError,TypeError,ValueError) as exc:
            result['unchecked'].append({'reason':'invalid_aircraft_turnaround_model','detail':str(exc)})
            continue
        remaining=float(rows[0]['energy_assumptions']['capacity'])-task_energy(rows[0])
        for previous,task in zip(rows,rows[1:]):
            end=previous['trajectory'][-1]; start=task['trajectory'][0]
            distance=math.dist(end['position_m'],start['position_m'])
            transfer=distance/speed
            available_at=end['time_s']+transfer+turn
            charging=max(0.,start['time_s']-available_at)
            capacity=float(task['energy_assumptions']['capacity'])
            before_charge=remaining-distance*cost
            available=min(capacity,before_charge+charging*charge)
            used=task_energy(task); after=available-used
            row={'aircraft_id':aircraft,'task_ids':[previous['id'],task['id']],
                'mode':'explicit_synthetic_ground_carrier', 'from_pad_id':previous['landing_pad_id'],
                'to_pad_id':task['launch_pad_id'], 'distance_m':distance,
                'interval_s':[end['time_s'],available_at], 'transfer_duration_s':transfer,
                'turnaround_s':turn,'charging_duration_s':charging,
                'transfer_energy_used':distance*cost,'energy_before_charge':before_charge,
                'charged_energy':max(0.,available-before_charge),'energy_at_next_start':available,
                'energy_after_next_task':after,'model':dict(model)}
            result['transitions'].append(row)
            if available_at>start['time_s']+EPS and end['time_s']<=start['time_s']+EPS:
                result['conflicts'].append({'kind':'aircraft_transition_time',
                    'task_ids':row['task_ids'],'aircraft_id':aircraft,
                    'interval_s':[end['time_s'],available_at],'available_at_s':available_at,
                    'next_task_start_s':start['time_s']})
            if before_charge < -EPS or after < task['energy_assumptions']['required_landing_reserve']-EPS:
                result['conflicts'].append({'kind':'aircraft_continuity_energy',
                    'task_ids':row['task_ids'],'aircraft_id':aircraft,
                    'energy_before_charge':before_charge,'landing_energy_remaining':after,
                    'required_reserve':task['energy_assumptions']['required_landing_reserve']})
            arrival=end['time_s']+transfer
            if arrival<start['time_s']:
                result['reservations'].append({'resource_id':task['launch_pad_id'],
                    'task_id':task['id'],'interval_s':[arrival,start['time_s']],
                    'kinds':['aircraft_turnaround_and_charge'], 'aircraft_id':aircraft})
            remaining=after
    return result


def waiting_reservations(tasks,world):
    """Additional named waiting areas; no movement into them is inferred."""
    reservations=[];unchecked=[]
    areas={x['id']:x for x in world.get('waiting_areas',[])}
    for task in tasks:
        for a,b in zip(task['trajectory'],task['trajectory'][1:]):
            if a['position_m']!=b['position_m'] or a['position_m'][2]!=0:
                continue
            for rid,area in areas.items():
                if math.dist(a['position_m'],area['position_m'])<=EPS:
                    reservations.append({'resource_id':rid,'task_id':task['id'],
                        'interval_s':[a['time_s'],b['time_s']],'kinds':['ground_waiting_area']})
    return reservations,unchecked


def capacity_conflicts(reservations,resources):
    conflicts=[];unchecked=[]
    for resource in resources:
        try:
            capacity=resource['capacity']
            if isinstance(capacity,bool) or not isinstance(capacity,int) or capacity<1:
                raise ValueError('resource capacity must be a positive integer')
        except (KeyError,TypeError,ValueError) as exc:
            unchecked.append({'resource_id':resource.get('id'),'reason':'unknown_resource_capacity','detail':str(exc)})
            continue
        rows=[r for r in reservations if r['resource_id']==resource['id']]
        times=sorted({v for r in rows for v in r['interval_s']})
        for start,end in zip(times,times[1:]):
            active=sorted({r['task_id'] for r in rows if r['interval_s'][0]<=start<r['interval_s'][1]})
            if len(active)>capacity:
                conflicts.append({'kind':'waiting_area_capacity' if resource.get('resource_kind')=='waiting_area' else 'pad_capacity',
                    'resource_id':resource['id'],'interval_s':[start,end], 'task_ids':active,'capacity':capacity})
    return conflicts,unchecked


def prepare_continuation(task,predecessor,world):
    """Make launch admission explicit when a reused aircraft arrives after release.

    This estimate reserves enough charging for the original mission. A detour's
    additional consumption is still checked by the complete continuity review.
    """
    import copy
    result=copy.deepcopy(task)
    model=world.get('aircraft_turnaround_model')
    if predecessor is None or not model:return result
    end=predecessor['trajectory'][-1]
    launch=next(p['position_m'] for p in world['pads'] if p['id']==task['launch_pad_id'])
    distance=math.dist(end['position_m'],launch)
    remaining=predecessor['energy_assumptions']['capacity']-task_energy(predecessor)
    history=[t for t in world.get('_candidate_tasks',[]) if t['aircraft_id']==task['aircraft_id'] and t['id']!=task['id'] and t['trajectory'][-1]['time_s']<=end['time_s']]
    prior=aircraft_continuity(history,world) if len(history)>1 else None
    if prior and prior['transitions']:
        remaining=prior['transitions'][-1]['energy_after_next_task']
    remaining-=distance*model['ground_transfer_energy_per_m']
    ready=end['time_s']+distance/model['ground_transfer_speed_mps']+model['turnaround_s']
    needed=task_energy(task)+task['energy_assumptions']['required_landing_reserve']
    rate=model['recharge_rate_per_s']
    charge=max(0.,(needed-remaining)/rate) if rate>0 else 0.
    entry=max(task['departure_time_s'],ready+charge)
    if entry>task['departure_time_s']+EPS:
        shift=entry-task['departure_time_s']
        for p in result['trajectory']:p['time_s']+=shift
        result['departure_time_s']=entry
        result['ground_entry']={'mode':'configured_ground_carrier','predecessor_task_id':predecessor['id'],
            'arrival_time_s':end['time_s']+distance/model['ground_transfer_speed_mps'],
            'ready_time_s':ready,'launch_admission_s':entry,'original_release_s':task['departure_time_s']}
        result.pop('ground_wait_interval_s',None);result.pop('actual_departure_time_s',None)
    return result
