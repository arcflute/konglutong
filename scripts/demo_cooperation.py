#!/usr/bin/env python3
"""Reproducible synthetic demonstrations using the production analysis pipeline.

No saved result, test fixture or hidden simulator outcome is used as a solver
answer. The repair-expansion stress case is constructed from a previously
computed route corner so a newly introduced mission meets that corner in time.
That deliberate stress-case construction is described in the resulting JSON.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'src'))

from chengkong.execution import frozen_prefix,simulate_context
from chengkong.pipeline import analyze_world
from chengkong.world import load_example,visible_world


def mission(template,pads,launch_id,landing_id,release,*,altitude=60.,slack=None):
    """Construct the declared vertical/cruise/service/landing mission from units."""
    task=copy.deepcopy(template);index={p['id']:p for p in pads}
    launch=index[launch_id]['position_m'];landing=index[landing_id]['position_m']
    up=altitude/task['nominal_vertical_speed_mps']
    cruise=math.dist(launch,landing)/task['nominal_horizontal_speed_mps']
    dwell=task['mandatory_services'][0]['duration_s'];end=release+2*up+cruise+dwell
    task.update(launch_pad_id=launch_id,landing_pad_id=landing_id,departure_time_s=release)
    if slack is not None:task['deadline_s']=end+slack
    service=task['mandatory_services'][0]
    service['position_m']=[*landing[:2],altitude]
    service['window_s']=[release,task['deadline_s']-up]
    task['trajectory']=[{'time_s':at,'position_m':list(point)} for at,point in [
        (release,launch),(release+up,[*launch[:2],altitude]),
        (release+up+cruise,[*landing[:2],altitude]),
        (release+up+cruise+dwell,[*landing[:2],altitude]),(end,landing)]]
    return task


def cooperation_cases(raw):
    base=visible_world(raw,'end_unconfirmed',60)
    # Finite priority backtracking: the second task has no arrival slack.
    opposite=copy.deepcopy(base)
    opposite['activities'][0]['planned_interval_s']=[600,700]
    a,b=opposite['tasks'][:2]
    first=mission(a,opposite['pads'],a['launch_pad_id'],a['landing_pad_id'],a['departure_time_s'])
    urgent=mission(b,opposite['pads'],a['landing_pad_id'],a['launch_pad_id'],a['departure_time_s'],slack=0.)
    first['priority']=1;urgent['priority']=2
    opposite['tasks']=[first,urgent]
    backtracking=analyze_world(opposite)
    backtracked=[r for r in backtracking['search_diagnostics'] if r.get('status')=='priority_order_backtracked']
    if not backtracking['candidates'] or not backtracked:
        raise RuntimeError('Backtracking stress case did not produce a reviewed candidate; inspect the declared search budget.')

    # Derive a new horizontal corridor that meets the actual computed detour.
    first_result=analyze_world(base)
    if not first_result['candidates']:raise RuntimeError('Base example produced no detour for expansion construction.')
    original=base['tasks'][0]
    planned=next(t for t in first_result['candidates'][0]['tasks'] if t['id']==original['id'])
    baseline_y=original['trajectory'][0]['position_m'][1]
    corner=next(p for p in planned['trajectory'] if p['position_m'][2]>0 and abs(p['position_m'][1]-baseline_y)>1e-6)
    expanded=copy.deepcopy(base);third=expanded['tasks'][2]
    for pad in expanded['pads']:
        if pad['id'] in (third['launch_pad_id'],third['landing_pad_id']):pad['position_m'][1]=corner['position_m'][1]
    launch=next(p['position_m'] for p in expanded['pads'] if p['id']==third['launch_pad_id'])
    altitude=corner['position_m'][2]
    release=corner['time_s']-altitude/third['nominal_vertical_speed_mps']-math.dist([*launch[:2],altitude],corner['position_m'])/third['nominal_horizontal_speed_mps']
    expanded['tasks'][2]=mission(third,expanded['pads'],third['launch_pad_id'],third['landing_pad_id'],release,altitude=altitude)
    expansion=analyze_world(expanded)
    expansions=[r for r in expansion['search_diagnostics'] if r.get('status')=='repair_set_expanded']
    if not expansions or not expansion['candidates']:raise RuntimeError('The declared route interaction did not produce repair-set expansion.')

    # A configured same-aircraft carrier consumes more energy than remains.
    energy=copy.deepcopy(base);energy['tasks']=energy['tasks'][:2]
    energy['activities'][0]['planned_interval_s']=[600,700]
    energy['tasks'][1]['aircraft_id']=energy['tasks'][0]['aircraft_id']
    energy['aircraft_turnaround_model']={'ground_transfer_speed_mps':20.,
        'ground_transfer_energy_per_m':.5,'turnaround_s':10.,'recharge_rate_per_s':0.}
    energy_result=analyze_world(energy)
    shortages=[c for c in energy_result['baseline_joint_analysis']['conflicts'] if c['kind']=='aircraft_continuity_energy']
    if not shortages or energy_result['baseline_full_review']['feasible']:raise RuntimeError('Insufficient aircraft energy was not rejected.')
    return {'priority_backtracking':backtracking,'repair_expansion':expansion,'aircraft_energy_rejection':energy_result}, {
        'priority_backtracking':{'failed_orders':len(backtracked),'successful_order':backtracking['candidates'][0]['search_priority_order'],
            'feasible':backtracking['candidates'][0]['review']['feasible']},
        'repair_expansion':{'construction':'new synthetic mission deliberately synchronized with a previously computed detour corner',
            'corner':corner,'added_task_ids':sorted({t for e in expansions for t in e['added_task_ids']}),
            'repair_task_ids':expansion['candidates'][0]['repair_task_ids'],'feasible':expansion['candidates'][0]['review']['feasible']},
        'aircraft_energy_rejection':{'feasible':energy_result['baseline_full_review']['feasible'],'energy_conflicts':shortages}}


def inflight_cases(raw):
    results={};summaries={}
    for at in (120,210,360):
        world=visible_world(raw,'end_unconfirmed',at)
        result=analyze_world(world,inflight=True)
        if not result['candidates']:raise RuntimeError(f'No in-flight maintenance candidate at {at}s')
        candidate=result['candidates'][0];context=simulate_context(result['world'])
        preserved={t['id']:frozen_prefix(t,at)==context['tasks'][t['id']]['frozen_prefix'] for t in candidate['tasks']}
        if not all(preserved.values()):raise RuntimeError('Executed prefix changed')
        key=f'inflight_{at}';results[key]=result
        summaries[key]={'at_s':at,'feasible':candidate['review']['feasible'],'prefix_preserved':preserved,
            'current_simulated_states':{tid:{k:v for k,v in row.items() if k!='frozen_prefix'} for tid,row in context['tasks'].items()},
            'historical_conflict_count':len(candidate['review']['historical_conflicts']),
            'future_conflict_count':len(candidate['review']['joint_report']['conflicts'])}
    return results,summaries


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=PROJECT/'examples/park_crane_synthetic.json')
    parser.add_argument('--case',choices=['all','cooperation','inflight'],default='all')
    parser.add_argument('--output',type=Path,default=PROJECT/'runs/cooperation-demo')
    args=parser.parse_args();raw=load_example(args.input);results={};summaries={}
    for name,build in [('cooperation',cooperation_cases),('inflight',inflight_cases)]:
        if args.case in ('all',name):
            reports,summary=build(raw);results.update(reports);summaries.update(summary)
    args.output.mkdir(parents=True,exist_ok=True)
    for name,result in results.items():
        (args.output/f'{name}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    document={'data_kind':'synthetic','is_real_case':False,'case':args.case,'summary':summaries,
        'files':{name:str((args.output/f'{name}.json').resolve()) for name in results},
        'limits':['bounded search and declared synthetic models only','no flight control or external adoption',
            'configured ground-carrier movement has time/energy but no ground-route obstacle model']}
    summary_path=args.output/'summary.json'
    summary_path.write_text(json.dumps(document,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':'verified_demonstrations_written','summary':str(summary_path.resolve()),'cases':summaries},ensure_ascii=False))


if __name__=='__main__':main()
