"""Independent hand-computable necessary bounds for observed round3 failures.

No planner, geometry or label implementation imports. The proof scope is the
supported fixed-altitude vertical-launch/horizontal-cruise graph and fixed pad
admission model, not arbitrary aircraft dynamics or global optimality.
"""
import json,math
from pathlib import Path

root=Path('/root/autodl-tmp/chengkong-data')
audit=json.loads((root/'round4/round3-failure-audit-archived.json').read_text())
events={e['event_id']:e for e in map(json.loads,(root/'round3/visible_scenarios.jsonl').read_text().splitlines())}
rows=[]
for case in audit['records']:
    if case['reproduced_event_feasible']:continue
    world=events[case['event_id']]['world'];tasks=world['tasks'];energies=[];reservations=[]
    aircraft_counts={t['aircraft_id']:sum(u['aircraft_id']==t['aircraft_id'] for u in tasks) for t in tasks}
    for task in tasks:
        altitudes={p['position_m'][2] for p in task['trajectory'] if p['position_m'][2]>0}
        if len(altitudes)!=1:continue
        z=next(iter(altitudes));services=task['mandatory_services']
        if any(s['position_m'][2]!=z for s in services):continue
        points=[task['trajectory'][0]['position_m'],*[s['position_m'] for s in services],task['trajectory'][-1]['position_m']]
        tolerances=[0,*[s['position_tolerance_m'] for s in services],0]
        horizontal=sum(max(0,math.dist(a[:2],b[:2])-tolerances[i]-tolerances[i+1]) for i,(a,b) in enumerate(zip(points,points[1:])))
        flight=horizontal/task['nominal_horizontal_speed_mps']+2*z/task['nominal_vertical_speed_mps']+sum(s['duration_s'] for s in services)
        lower=flight*task['energy_assumptions']['airborne_cost_per_s']+task['energy_assumptions']['required_landing_reserve']
        energies.append({'task_id':task['id'],'minimum_energy_plus_reserve':lower,'capacity':task['energy_assumptions']['capacity'],
            'necessary_energy_condition_fails':lower>task['energy_assumptions']['capacity'],
            'derivation':'sum(max(0, horizontal service-to-service distance - endpoint tolerances))/v_horizontal + 2*cruise_z/v_vertical + all service durations; multiply airborne rate and add reserve. Obstacles/waits can only increase this bound.',
            'scope':'supported fixed-cruise-altitude vertical launch/landing route class; not arbitrary coupled ascent/cruise dynamics'})
        if aircraft_counts[task['aircraft_id']]==1 and 'ground_entry' not in task:
            start=task['departure_time_s'];reservations.append({'task_id':task['id'],'resource_id':task['launch_pad_id'],
                'minimum_interval_s':[start,start+z/task['nominal_vertical_speed_mps']]})
    conflicts=[]
    for pad in world['pads']:
        rr=[r for r in reservations if r['resource_id']==pad['id']];times=sorted({x for r in rr for x in r['minimum_interval_s']})
        for begin,end in zip(times,times[1:]):
            active=[r['task_id'] for r in rr if r['minimum_interval_s'][0]<=begin<r['minimum_interval_s'][1]]
            if len(active)>pad['capacity']:conflicts.append({'resource_id':pad['id'],'interval_s':[begin,end],'task_ids':active,'capacity':pad['capacity'],
                'necessary_condition':'fixed release-time pad entry, no off-pad carrier admission, each requires at least cruise-height/vertical-speed time on launch pad'})
    rows.append({'event_id':case['event_id'],'energy_bounds':energies,'minimum_launch_pad_conflicts':conflicts,
        'global_arbitrary_route_infeasibility_proven':False,'reference_claim':'Necessary-condition impossibility only within explicitly supported route/admission model.'})
out=root/'round4/independent-necessary-bounds.json';out.write_text(json.dumps({'method':'arithmetic_lower_bounds_no_planner_imports','rows':rows},indent=2))
print(json.dumps({'output':str(out),'energy_bound_failures':[(r['event_id'],e['task_id']) for r in rows for e in r['energy_bounds'] if e['necessary_energy_condition_fails']],
    'pad_conflict_events':[r['event_id'] for r in rows if r['minimum_launch_pad_conflicts']]}))
