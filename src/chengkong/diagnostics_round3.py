"""Named post-evaluation diagnostics, never model selection or test-set tuning."""
from collections import defaultdict
from pathlib import Path
import json
import time
import numpy as np
from sklearn.metrics import ndcg_score
from .synthetic_round3 import solve_action,generate_constraints,NODE_FEATURES
from .learning_round3_runtime import ResidualGNN,priority_checks
from .model_runtime import ModelBundle,matrix
from .training_round3 import dump,load_rows
from .evaluation_round3 import distribution,paired_group_ci,avg


def run(config):
    root=Path(config['model_dir']);data=Path(config['data_dir']);bundle=ModelBundle(root);rows=load_rows(config,'test');ids={r['event_id'] for r in rows}
    events=[json.loads(x) for x in (data/'visible_scenarios.jsonl').read_text().splitlines()];events=[e for e in events if e['event_id'] in ids]
    records=[]
    # This simple alternate rule is deliberately labelled post-hoc diagnostic,
    # not substituted retroactively into the preregistered baseline.
    for event in events:
        world=event['world'];constraints=generate_constraints(world)
        for task in world['tasks']:
            start=time.perf_counter();selected=None;attempts=[]
            for action in ['keep','detour']:
                result=solve_action(world,constraints,task,action,config['solver_max_expansions'],config['solver_timeout_seconds']);attempts.append(result['solve_status'])
                if result['feasible']:selected=result;break
            records.append({'query_id':event['event_id']+':'+task['id'],'feasible':selected is not None,'attempts':len(attempts),'statuses':attempts,
                'seconds':time.perf_counter()-start,'actual_cost':selected['actual_cost'] if selected else None})
    gnn={}
    import xgboost as xgb
    node=xgb.XGBRegressor();node.load_model(root/'attempts'/f'node_baseline-0-{config["seeds"][0]}-none.json')
    for seed in config['seeds']:
        weighted=ResidualGNN.load(root/'attempts'/f'gnn-0-{seed}-weighted.npz');isolated=ResidualGNN.load(root/'attempts'/f'gnn-0-{seed}-isolated.npz');diff=defaultdict(list);dx=defaultdict(list)
        for row in rows:
            g=row['graph'];x=matrix(g['node_features'],NODE_FEATURES);truth=[g['targets']]
            a=weighted.forward(x,g['adjacency'])[0];b=isolated.forward(x,np.eye(len(g['targets'])))[0];c=node.predict(x)
            diff[row['template_family']].append(float(ndcg_score(truth,[a])-ndcg_score(truth,[b])))
            dx[row['template_family']].append(float(ndcg_score(truth,[a])-ndcg_score(truth,[c])))
        gnn[str(seed)]={'vs_same_feature_isolated':paired_group_ci(diff),'vs_same_feature_node_xgb':paired_group_ci(dx)}
    stress=[];raw_path=Path('examples/motion_crane_synthetic.json')
    if raw_path.exists():
        from .world import load_example,visible_world
        raw=load_example(raw_path);scenario=raw['scenario_definitions_for_simulator_only'][0]['id']
        for at in (60,300,450):
            world=visible_world(raw,scenario,at);constraints=generate_constraints(world)
            baseline=priority_checks(world,constraints);learned=priority_checks(world,constraints,bundle)
            signature=lambda result: {tid:sorted(json.dumps(c,sort_keys=True) for c in report['conflicts']) for tid,report in result['reports'].items()}
            stress.append({'at_s':at,'scenario':scenario,'required_pairs':baseline['required_pairs'],'same_full_conflicts':signature(baseline)==signature(learned),
                'same_unchecked':{t:len(r['unchecked']) for t,r in baseline['reports'].items()}=={t:len(r['unchecked']) for t,r in learned['reports'].items()},
                'learned_all_pairs_checked':learned['all_pairs_checked'],'outside_training_domain':True,
                'scope':'Integration stress only, not dynamic-motion prediction quality or independent test-family count'})
    output={'status':'post_final_evaluation_diagnostics_not_selection','model_version':bundle.version,
        'alternate_rule':{'order':['keep','detour','delay'],'budget':2,'queries':len(records),'feasible_queries':sum(r['feasible'] for r in records),
            'completion_fraction':avg([r['feasible'] for r in records]),'mean_attempts':avg([r['attempts'] for r in records]),'seconds_all_queries':distribution([r['seconds'] for r in records]),
            'interpretation':'A very simple alternate rule may match learned success; improvement over keep-delay-detour alone is not robust overall advantage.'},
        'gnn_neighbor_value_intervals':gnn,'motion_out_of_domain_integration':stress,'records':records}
    dump(root/'diagnostic-addendum.json',output);return {k:v for k,v in output.items() if k!='records'}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--config',default='configs/training-round3.json');a=p.parse_args();print(json.dumps(run(json.loads(Path(a.config).read_text())),ensure_ascii=False,indent=2))
