"""Local CLI, optional synthetic-model inference, and loopback Web entry point."""
import argparse
import json
import os
from pathlib import Path

from .pipeline import analyze_world,run_analysis
from .world import load_example,visible_world


def _write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    tmp.replace(path)


def main(argv=None):
    parser=argparse.ArgumentParser(description='城空智护：合成施工信息→约束→维护候选→独立复核')
    parser.add_argument('command',choices=['analyze','demo','serve'])
    parser.add_argument('--input',default='examples/park_crane_synthetic.json')
    parser.add_argument('--scenario',default=None,help='A scenario ID declared by the selected input')
    parser.add_argument('--as-of',type=float,default=None,help='Defaults to input observed snapshot time')
    parser.add_argument('--output',default=None)
    parser.add_argument('--model-dir',default=None,help='Explicit trained synthetic artifact directory')
    parser.add_argument('--learned',action='store_true',help='Opt in to learned ordering; hard checks remain mandatory')
    parser.add_argument('--planning-mode',choices=['conservative','dynamic_windows'],default=None)
    parser.add_argument('--action-policy',choices=['legacy','keep_detour'],default=None)
    parser.add_argument('--inflight',action='store_true',help='Freeze executed prefixes and repair simulated remaining flight')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--db-dir',default='runs/web')
    parser.add_argument('--demo-identities',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.command=='serve':
            from .web import main as serve
            options=['--input',args.input,'--host',args.host,'--port',str(args.port),'--db-dir',args.db_dir]
            if args.model_dir:options+=['--model-dir',args.model_dir]
            if args.demo_identities:options+=['--demo-identities']
            if args.planning_mode:options+=['--planning-mode',args.planning_mode]
            if args.action_policy:options+=['--action-policy',args.action_policy]
            serve(options)
            return 0
        bundle=None
        if args.model_dir:
            from .model_runtime import ModelBundle
            bundle=ModelBundle(args.model_dir)
        if args.learned and bundle is None:
            raise ValueError('--learned requires --model-dir; use the rule default otherwise')
        options={'model_bundle':bundle,'learned':args.learned,'inflight':args.inflight,'measure':True}
        raw=load_example(args.input)
        def compute(scenario,moment):
            world=visible_world(raw,scenario,moment)
            if args.planning_mode or args.action_policy:
                policy=world.setdefault('planning_policy',{})
                if args.planning_mode:policy['mode']=args.planning_mode
                if args.action_policy:policy['action_order']=['retain','detour','delay'] if args.action_policy=='keep_detour' else ['retain','delay','detour']
            return analyze_world(world,**options)
        if args.command=='analyze':
            scenario=args.scenario or ('end_unconfirmed' if any(s['id']=='end_unconfirmed' for s in raw['scenario_definitions_for_simulator_only']) else raw['scenario_definitions_for_simulator_only'][0]['id'])
            result=compute(scenario,args.as_of if args.as_of is not None else raw['observed_snapshot']['as_of_s'])
            if args.output:
                _write(args.output,result)
                print(json.dumps({'status':'written','output':str(Path(args.output).resolve()),'planning_status':result['planning_status'],'candidates':len(result['candidates'])},ensure_ascii=False))
            else:print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        else:
            target=Path(args.output or os.environ.get('CHENGKONG_RUN_DIR','runs/demo'))
            summaries=[]
            for scenario in [s['id'] for s in raw['scenario_definitions_for_simulator_only']]:
                times=raw.get('simulation_check_times_s') or [t for t in [60,330,360,600]
                    if raw['observed_snapshot']['as_of_s']<=t<=raw['evaluation_horizon_s'][1]]
                if raw['observed_snapshot']['as_of_s'] not in times:times.insert(0,raw['observed_snapshot']['as_of_s'])
                for moment in times:
                    result=compute(scenario,moment)
                    path=target/f'{scenario}-{moment}.json';_write(path,result)
                    summaries.append({'scenario':scenario,'as_of_s':moment,'environment_version':result['world']['environment_version'],'task_status':{k:v['status'] for k,v in result['baseline_analysis'].items()},'candidates':len(result['candidates']),'candidate_statuses':[c['status'] for c in result['candidates']],'planning_status':result['planning_status'],'file':str(path)})
            _write(target/'summary.json',summaries)
            print(json.dumps({'status':'demo_written','summary':str((target/'summary.json').resolve()),'snapshots':len(summaries)},ensure_ascii=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError,ImportError) as exc:
        print(json.dumps({'status':'invalid_or_unsupported_input','error':str(exc)},ensure_ascii=False))
        return 2
