"""Small CPU experiment: benchmark, generate, train, reload and evaluate.

Run: python -m chengkong.training benchmark|train|evaluate --config ...
No automatic hyperparameter search; each fit is protected by an alarm timeout.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import hashlib
import json
import math
from pathlib import Path
import time

from .synthetic import (make_event,label_event,split_templates,FEATURE_VERSION,
    PAIR_FEATURES,NODE_FEATURES,ACTION_FEATURES,QUANTILE_FEATURES)


def dump(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def benchmark(config):
    rows=[]
    for i in range(6):
        tick=time.monotonic();event=make_event(config['seed'],i)
        generation=time.monotonic()-tick
        labeled=label_event(event,config['solver_max_expansions'],config['solver_timeout_seconds'])
        rows.append({'template':i,'tasks':len(event['world']['tasks']),
            'generation_s':generation,'label_s':labeled['label_seconds'],
            'proposal_count':len(labeled['actions'])})
    average=sum(r['label_s'] for r in rows)/len(rows)
    result={'pilot_events':len(rows),'rows':rows,'mean_label_seconds':average,
        'requested_events':config['template_count']*config['variants_per_template'],
        'estimated_generation_seconds':average*config['template_count']*config['variants_per_template'],
        'scale_choice':f"{config['template_count']*config['variants_per_template']} events / {config['template_count']} template families; pilot supports this bounded run"}
    dump(Path(config['data_dir'])/'benchmark.json',result)
    return result


def generate(config):
    pilot=benchmark(config)
    if pilot['estimated_generation_seconds']>config['max_total_generation_seconds']:
        raise ValueError('Pilot estimate exceeds generation budget; reduce configured scale explicitly')
    groups=split_templates(config['template_count'],config['seed']);rows=[];tick=time.monotonic()
    engine_sources={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in ['analysis.py','geometry.py','planning.py','verification.py','world.py','contracts.py']}
    data_dir=Path(config['data_dir']);data_dir.mkdir(parents=True,exist_ok=True)
    with (data_dir/'visible_scenarios.jsonl').open('w') as visible, (data_dir/'simulator_truth.jsonl').open('w') as truth:
        for i in range(config['template_count']):
            for variant in range(config['variants_per_template']):
                if time.monotonic()-tick>config['max_total_generation_seconds']:
                    raise TimeoutError('Total generation budget exhausted; partial files are not a completed dataset')
                event=make_event(config['seed'],i,variant)
                visible.write(json.dumps({k:v for k,v in event.items() if k!='simulator_truth'})+'\n')
                truth.write(json.dumps({'event_id':event['event_id'],'truth':event['simulator_truth']})+'\n')
                row=label_event(event,config['solver_max_expansions'],config['solver_timeout_seconds'])
                row['split']=groups[event['template_family']];rows.append(row)
    dump(data_dir/'dataset.json',rows)
    manifest={'seed':config['seed'],'feature_version':FEATURE_VERSION,'template_assignments':groups,
        'label_engine_source_sha256':engine_sources,
        'counts':dict(Counter(r['split'] for r in rows)),'generation_seconds':time.monotonic()-tick,
        'feature_names':{'sensitivity':PAIR_FEATURES,'association':NODE_FEATURES,'ranking':ACTION_FEATURES,'quantile':QUANTILE_FEATURES},
        'label_scope':'actual deterministic continuous geometry perturbations and bounded local SIPP plus independent task/resource checks',
        'future_truth_file':'simulator_truth.jsonl, labels and offline evaluation only',
        'upstream_predictions_in_downstream_features':False,
        'cross_fitting':'not applicable: models use visible raw features only, never other fitted predictions',
        'group_policy':'Every variant of one full event/layout template has one split; template itself excluded from features',
        'dataset_sha256':hashlib.sha256((data_dir/'dataset.json').read_bytes()).hexdigest(),
        'solver_status_counts':dict(Counter(a['solve_status'] for r in rows for a in r['actions']))}
    dump(data_dir/'data_manifest.json',manifest)
    return rows,manifest


def fit_models(config,rows,data_manifest):
    import numpy as np
    import xgboost as xgb
    from .model_runtime import SmallGNN,matrix
    if not 1<=config['max_configs_per_model']<=3:raise ValueError('At most three model configurations permitted')
    model_dir=Path(config['model_dir']);model_dir.mkdir(parents=True,exist_ok=True)
    import subprocess
    import sys
    calibration=[r for r in rows if r['split']=='calibration']
    dump(model_dir/'fit-config.json',config)
    durations={};losses=[]
    for name in ('sensitivity','gnn','ranker','quantile_lower','quantile_upper'):
        tick=time.monotonic()
        # Parent-enforced deadline can terminate native XGBoost code too.
        subprocess.run([sys.executable,'-m','chengkong.training','_fit-worker',
            '--config',str(model_dir/'fit-config.json'),'--worker-model',name],
            check=True,timeout=config['training_timeout_seconds'],capture_output=True,text=True)
        durations[name]=time.monotonic()-tick
    worker_stats=json.loads((model_dir/'gnn-training.json').read_text())
    losses=worker_stats['losses']
    qmodels=[];alpha=config['quantile_alpha']
    for name in ('lower','upper'):
        model=xgb.XGBRegressor();model.load_model(model_dir/f'quantile_{name}.json');qmodels.append(model)
    cx=matrix([r['quantile']['features'] for r in calibration],QUANTILE_FEATURES)
    lower=qmodels[0].predict(cx);upper=qmodels[1].predict(cx);lo=np.minimum(lower,upper);hi=np.maximum(lower,upper)
    residuals=defaultdict(list)
    for i,row in enumerate(calibration):
        target=row['quantile']['target_end_error_s'];residuals[row['template_family']].append(max(float(lo[i])-target,target-float(hi[i]),0))
    scores=sorted(max(values) for values in residuals.values());k=math.ceil((len(scores)+1)*(1-alpha))
    if k>len(scores):raise ValueError('Too few independent calibration groups for requested finite coverage')
    calibrated={'method':'split conformal quantile interval, maximum residual within template family',
        'alpha':alpha,'nominal_coverage':1-alpha,'correction_s':scores[k-1],
        'sample_count':len(calibration),'independent_template_groups':len(scores),'order_statistic_index_1based':k,
        'calibration_template_ids':sorted(residuals),'not_flight_safety_probability':True,
        'exchangeability_assumption':'Calibration/test template families sampled from same synthetic generator',
        'target':'actual activity end minus known planned end in simulation seconds'}
    dump(model_dir/'calibration.json',calibrated)
    source_hash=hashlib.sha256(b''.join(Path(__file__).with_name(f).read_bytes() for f in ['synthetic.py','training.py','model_runtime.py'])).hexdigest()
    version='synthetic-round2-'+data_manifest['dataset_sha256'][:12]
    manifest={'model_version':version,'feature_version':FEATURE_VERSION,'trained':True,'status':'trained_synthetic',
        'default_policy':'deterministic_rule','data_manifest':data_manifest,'config':config,
        'configuration_attempts_per_model':1,'training_seconds':durations,
        'gnn_training_loss':{'initial':losses[0],'final':losses[-1],'epochs':len(losses)},
        'source_sha256':source_hash,'xgboost_version':xgb.__version__,'numpy_version':np.__version__,
        'scope':'synthetic template experiment only; no real construction generalization or flight safety probability',
        'artifacts':{},'data_dir':str(Path(config['data_dir']).resolve())}
    for file in ['sensitivity.json','ranker.json','gnn.npz','quantile_lower.json','quantile_upper.json','calibration.json']:
        manifest['artifacts'][file]={'sha256':hashlib.sha256((model_dir/file).read_bytes()).hexdigest(),'bytes':(model_dir/file).stat().st_size}
    dump(model_dir/'manifest.json',manifest)
    return manifest


def fit_worker(config,name):
    import xgboost as xgb
    from .model_runtime import SmallGNN,matrix
    rows=json.loads((Path(config['data_dir'])/'dataset.json').read_text())
    training=[r for r in rows if r['split']=='train']
    model_dir=Path(config['model_dir'])
    parameters=dict(config['xgboost'],random_state=config['seed'],tree_method='hist',device='cpu')
    if name=='gnn':
        model=SmallGNN(len(NODE_FEATURES),config['gnn']['hidden'],config['seed'])
        losses=model.fit([r['graph'] for r in training],config['gnn']['epochs'],config['gnn']['learning_rate'])
        model.save(model_dir/'gnn.npz');dump(model_dir/'gnn-training.json',{'losses':losses})
        return
    if name=='sensitivity':
        pairs=[p for r in training for p in r['pairs']]
        model=xgb.XGBRegressor(objective='reg:squarederror',**parameters)
        model.fit(matrix([p['features'] for p in pairs],PAIR_FEATURES),[p['target'] for p in pairs])
    elif name=='ranker':
        actions=[a for r in training for a in r['actions']]
        model=xgb.XGBRanker(objective='rank:pairwise',**parameters)
        model.fit(matrix([a['features'] for a in actions],ACTION_FEATURES),[a['relevance'] for a in actions],group=[3]*(len(actions)//3))
    elif name in ('quantile_lower','quantile_upper'):
        q=config['quantile_alpha']/2 if name=='quantile_lower' else 1-config['quantile_alpha']/2
        model=xgb.XGBRegressor(objective='reg:quantileerror',quantile_alpha=q,**parameters)
        model.fit(matrix([r['quantile']['features'] for r in training],QUANTILE_FEATURES),[r['quantile']['target_end_error_s'] for r in training])
    else:raise ValueError('Unknown model worker')
    model.save_model(model_dir/(name+'.json'))


def evaluate(config,rows=None):
    import numpy as np
    from sklearn.metrics import mean_squared_error,ndcg_score
    from .model_runtime import ModelBundle,matrix
    rows=rows or json.loads((Path(config['data_dir'])/'dataset.json').read_text())
    bundle=ModelBundle(config['model_dir']);results={}
    for split in ['validation','test']:
        selected=[r for r in rows if r['split']==split];pairs=[p for r in selected for p in r['pairs']]
        y=np.array([p['target'] for p in pairs]);pred=bundle.sensitivity_model.predict(matrix([p['features'] for p in pairs],PAIR_FEATURES))
        # Explicit available-before-refinement baseline: conservative boxes and time.
        rule=np.array([float(p['features']['bbox_clearance_m']==0 and p['features']['temporal_overlap_s']>0) for p in pairs])
        pair_ndcg=[];pair_unknown_ndcg=[]
        for row in selected:
            ps=row['pairs'];truth=[p['target'] for p in ps]
            predictions=bundle.sensitivity_model.predict(matrix([p['features'] for p in ps],PAIR_FEATURES))
            unknown=[p['features']['geometry_missing']+p['features']['end_unconfirmed'] for p in ps]
            pair_ndcg.append(ndcg_score([truth],[predictions]));pair_unknown_ndcg.append(ndcg_score([truth],[unknown]))
        graph_errors=[];graph_rule_errors=[];graph_ndcg=[];graph_rule_ndcg=[];graph_priority_ndcg=[]
        for row in selected:
            g=row['graph'];p,_=bundle.gnn.forward(matrix(g['node_features'],NODE_FEATURES),g['adjacency'])
            target=np.asarray(g['targets']);r=np.array([float(n['min_bbox_clearance_m']==0 and n['max_temporal_overlap_s']>0) for n in g['node_features']])
            graph_errors.extend((p-target)**2);graph_rule_errors.extend((r-target)**2)
            graph_ndcg.append(ndcg_score([target],[p]));graph_rule_ndcg.append(ndcg_score([target],[r]))
            priority=[1/(1+n['priority']) for n in g['node_features']]
            graph_priority_ndcg.append(ndcg_score([target],[priority]))
        query_rows=defaultdict(list)
        for row in selected:
            for a in row['actions']:query_rows[a['query_id']].append(a)
        rank_ndcg=[];rule_ndcg=[];top1=[];rule_top1=[];solvable=[];learned_checks=[];rule_checks=[];selected_costs=[];rule_costs=[];oracle_costs=[]
        for actions in query_rows.values():
            scores=bundle.ranker.predict(matrix([a['features'] for a in actions],ACTION_FEATURES))
            relevance=np.asarray([a['relevance'] for a in actions]);ordered=sorted(range(3),key=lambda k:(-float(scores[k]),k))
            rank_ndcg.append(ndcg_score([relevance],[scores]));rule_ndcg.append(ndcg_score([relevance],[[3,2,1]]))
            top1.append(actions[ordered[0]]['feasible']);rule_top1.append(actions[0]['feasible']);solvable.append(any(a['feasible'] for a in actions))
            good=[a['actual_cost'] for a in actions if a['feasible']]
            if good:
                chosen=next((j,k) for j,k in enumerate(ordered,1) if actions[k]['feasible']);first=next(i for i,a in enumerate(actions,1) if a['feasible'])
                learned_checks.append(chosen[0]);rule_checks.append(first);selected_costs.append(actions[chosen[1]]['actual_cost']);rule_costs.append(actions[first-1]['actual_cost']);oracle_costs.append(min(good))
        qx=matrix([r['quantile']['features'] for r in selected],QUANTILE_FEATURES)
        a=bundle.lower.predict(qx);b=bundle.upper.predict(qx);lo=np.minimum(a,b);hi=np.maximum(a,b)
        qy=np.array([r['quantile']['target_end_error_s'] for r in selected]);delta=bundle.calibration['correction_s']
        covered=(qy>=lo-delta)&(qy<=hi+delta);raw=(qy>=lo)&(qy<=hi);groups=defaultdict(list)
        for row,c in zip(selected,covered):groups[row['template_family']].append(bool(c))
        def avg(values):return float(np.mean(values)) if len(values) else None
        results[split]={'events':len(selected),'template_groups':len(groups),'pairs':len(pairs),
            'sensitivity':{'mse':float(mean_squared_error(y,pred)),'rule_bbox_mse':float(mean_squared_error(y,rule)),
                'ndcg':avg(pair_ndcg),'rule_unknown_first_ndcg':avg(pair_unknown_ndcg),
                'label':'fraction of declared geometry/end perturbations conflicting; no accident probability'},
            'gnn':{'node_mse':avg(graph_errors),'rule_node_mse':avg(graph_rule_errors),
                'ndcg':avg(graph_ndcg),'rule_ndcg':avg(graph_rule_ndcg),
                'rule_priority_ndcg':avg(graph_priority_ndcg)},
            'ranking':{'queries':len(query_rows),'solvable_queries':sum(solvable),'ndcg_at_3':avg(rank_ndcg),'rule_ndcg_at_3':avg(rule_ndcg),
                'top_one_proposal_feasible_rate':avg(top1),'rule_top_one_feasible_rate':avg(rule_top1),
                'all_proposal_feasible_rate_no_learning':avg(solvable),
                'mean_evaluations_to_first_feasible_on_solvable':avg(learned_checks),
                'rule_mean_evaluations_to_first_feasible_on_solvable':avg(rule_checks),
                'first_feasible_mean_cost':avg(selected_costs),'rule_first_feasible_mean_cost':avg(rule_costs),
                'full_enumeration_min_mean_cost':avg(oracle_costs),
                'comparison_scope':'same actually solved proposal set, replayed ordering; no global multi-task optimality claim'},
            'quantile':{'nominal_coverage':1-config['quantile_alpha'],'uncalibrated_coverage':avg(raw),
                'calibrated_coverage':avg(covered),'calibrated_all_variants_group_coverage':avg([all(v) for v in groups.values()]),
                'uncalibrated_mean_width_s':avg(hi-lo),'calibrated_mean_width_s':avg(hi-lo+2*delta),
                'correction_s':delta,'not_flight_safety_probability':True}}
    result={'model_version':bundle.version,'default_policy':'deterministic_rule','results':results,
        'limits':['Small synthetic templates only; no real-world evaluation.',
            'Evaluation ordering replays recorded real solver outcomes; elapsed runtime improvement is not inferred.',
            'GNN labels express direct and neighboring synthetic impact, not global optimal repair membership.',
            'Conformal group coverage requires exchangeable template families and may be wide.',
            'No learned output bypasses geometry, time, task or resource review.']}
    dump(Path(config['model_dir'])/'evaluation.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['benchmark','generate','train','evaluate','_fit-worker'])
    parser.add_argument('--config',default='configs/training-small.json')
    parser.add_argument('--worker-model')
    args=parser.parse_args();config=json.loads(Path(args.config).read_text())
    if args.command=='_fit-worker':
        fit_worker(config,args.worker_model)
        return
    if args.command=='benchmark':result=benchmark(config)
    elif args.command=='generate':_,result=generate(config)
    elif args.command=='train':
        rows,data_manifest=generate(config);manifest=fit_models(config,rows,data_manifest);result=evaluate(config,rows)
        summary={'model_version':manifest['model_version'],'feature_version':FEATURE_VERSION,
            'artifact_dir':str(Path(config['model_dir']).resolve()),'data_dir':config['data_dir'],
            'status':'trained_synthetic','default_enabled':False,'default_policy':'deterministic_rule',
            'evaluation':result,'training_seconds':manifest['training_seconds'],
            'configuration_attempts_per_model':manifest['configuration_attempts_per_model'],
            'gnn_training_loss':manifest['gnn_training_loss'],'data_counts':data_manifest['counts'],
            'template_assignments':data_manifest['template_assignments'],
            'feature_names':data_manifest['feature_names'],'dataset_sha256':data_manifest['dataset_sha256']}
        dump('configs/learning-artifacts.json',summary)
    else:result=evaluate(config)
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
