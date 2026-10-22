"""Budgeted independent round3 protocol. Round2 files are read-only diagnostics."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .synthetic_round3 import *


def dump(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load_rows(config,split):return json.loads((Path(config['data_dir'])/(split+'.json')).read_text())
def verify_paths(config):
    if any(not any(part.startswith('round3') for part in Path(config[k]).resolve().parts) or 'round2' in Path(config[k]).resolve().parts for k in ('data_dir','model_dir')):raise ValueError('Output must explicitly belong to round3 and never round2')
    if len(config['xgboost_configs'])>3 or len(config['quantile_scale_modes'])>3 or config['max_configs_per_model']>3:raise ValueError('Maximum three primary model configurations')
    if config['total_training_budget_seconds']>3600:raise ValueError('Requested cumulative training budget exceeds authorized initial 3600 s')


def audit_round2(config):
    """Recompute original metrics in memory: deliberately never call old evaluate."""
    import numpy as np
    from sklearn.metrics import ndcg_score
    from .model_runtime import ModelBundle,matrix
    from . import synthetic as v2
    data=Path('/root/autodl-tmp/chengkong-data/round2');model=Path('/root/autodl-tmp/chengkong-models/round2')
    b=ModelBundle(model);rows=json.loads((data/'dataset.json').read_text());test=[r for r in rows if r['split']=='test'];pairs=[p for r in test for p in r['pairs']]
    pred=b.sensitivity_model.predict(matrix([p['features'] for p in pairs],v2.PAIR_FEATURES));mse=float(np.mean((pred-np.array([p['target'] for p in pairs]))**2))
    graph_ndcg=[];query=defaultdict(list);tie_count=0
    for r in test:
        g=r['graph'];p,_=b.gnn.forward(matrix(g['node_features'],v2.NODE_FEATURES),g['adjacency']);graph_ndcg.append(float(ndcg_score([g['targets']],[p])))
        for a in r['actions']:query[a['query_id']].append(a)
    first=[];cost=[]
    for actions in query.values():
        p=b.ranker.predict(matrix([a['features'] for a in actions],v2.ACTION_FEATURES));order=sorted(range(len(actions)),key=lambda i:(-float(p[i]),i));first.append(actions[order[0]]['feasible'])
        good=[a for a in actions if a['feasible']]
        if good:cost.append(next(actions[i]['actual_cost'] for i in order if actions[i]['feasible']))
        if len(good)>1 and len({round(a['actual_cost'],6) for a in good})<len(good):tie_count+=1
    x=matrix([r['quantile']['features'] for r in test],v2.QUANTILE_FEATURES);a=b.lower.predict(x);z=b.upper.predict(x);lo=np.minimum(a,z);hi=np.maximum(a,z);y=np.array([r['quantile']['target_end_error_s'] for r in test]);cor=b.calibration['correction_s']
    groupmap=defaultdict(set)
    for r in rows:groupmap[r['template_family']].add(r['split'])
    out={'status':'reproduced_read_only','original_model_version':b.version,'test_already_observed_diagnostic_only':True,
        'metrics':{'sensitivity_mse':{'value':mse,'denominator_pairs':len(pairs),'unit':'task-constraint pair'},
            'gnn_ndcg':{'value':float(np.mean(graph_ndcg)),'denominator_event_graphs':len(test),'zero_relevance_graphs':sum(not any(r['graph']['targets']) for r in test)},
            'repair_top1_feasible':{'value':float(np.mean(first)),'numerator':sum(first),'denominator_complete_queries':len(query)},
            'first_feasible_cost':{'value':float(np.mean(cost)),'denominator_common_solvable_queries':len(cost),'overall_failed_queries':len(query)-len(cost)},
            'end_error':{'coverage':float(np.mean((y>=lo-cor)&(y<=hi+cor))),'mean_width_s':float(np.mean(hi-lo+2*cor)),'test_events':len(test),'test_templates':len({r['template_family'] for r in test}),'calibration_templates':b.calibration['independent_template_groups'],'finite_sample_k':b.calibration['order_statistic_index_1based']}},
        'findings':[{'issue':'sensitivity_not_in_precise_check_order','evidence':'Round2 pipeline computed check_task before sensitivity(); only evidence request order changed','resolution':'Round3 priority_checks executes model order before actual precise checks; all obligations retained'},
            {'issue':'graph_target_constructed_using_same_adjacency','evidence':'0.65 direct + 0.35 neighbor average labels favor graph smoothing by construction','resolution':'Round3 labels use actual direct/joint hard conflicts, no graph-smoothed labels'},
            {'issue':'equal_cost_candidates_received_unequal_rank_labels','affected_test_queries':tie_count,'resolution':'Round3 relevance ties are equal within 1e-6 cost rounding'},
            {'issue':'replayed_attempt_count_not_measured_runtime','resolution':'Round3 records actual independent ordered solver runs and inference overhead'},
            {'issue':'no_cross_family_leak_observed','families_with_multiple_splits':[k for k,v in groupmap.items() if len(v)>1]},
            {'issue':'finite_sample_calibration_valid_for_declared_alpha','resolution':'Keep group maxima, explicit insufficient-group status rather than clamping'}],
        'source_hashes':{'dataset':sha(data/'dataset.json'),'evaluation':sha(model/'evaluation.json'),'manifest':sha(model/'manifest.json')}}
    dump(Path(config['data_dir'])/'round2-audit.json',out);return out


def benchmark(config):
    rows=[]
    for i in range(6):
        t=time.perf_counter();event=make_event(config['seed'],i);generation=time.perf_counter()-t;row=label_event(event,config['solver_max_expansions'],config['solver_timeout_seconds'])
        rows.append({'template_id':event['template_family'],'tasks':len(event['world']['tasks']),'sites':len(event['world']['activities']),
            'generation_seconds':generation,'label_seconds':row['label_seconds'],'stored_bytes':len(json.dumps(row))+len(json.dumps(event))})
    average=sum(x['label_seconds'] for x in rows)/6;out={'pilot_independent_templates':6,'rows':rows,'mean_label_seconds':average,
        'estimated_label_seconds':average*config['template_count']*config['variants_per_template'],
        'estimated_storage_bytes':sum(x['stored_bytes'] for x in rows)/6*config['template_count']*config['variants_per_template'],
        'scale_choice':{'independent_templates':config['template_count'],'variants_each':config['variants_per_template'],'decision_snapshots_each_event':1}}
    dump(Path(config['data_dir'])/'benchmark.json',out);return out


def generate(config):
    verify_paths(config);root=Path(config['data_dir']);root.mkdir(parents=True,exist_ok=True)
    if (root/'data_manifest.json').exists():raise FileExistsError('Round3 dataset already exists; do not overwrite an observed experiment')
    pilot=benchmark(config)
    if pilot['estimated_label_seconds']>config['max_generation_seconds']:raise ValueError('Pilot exceeds generation budget')
    split=split_templates(config['template_count'],config['seed']);protocol={'protocol_version':'round3-v1','created_unix_s':time.time(),
        'template_assignments':split,'seed':config['seed'],'seeds':config['seeds'],'config':config,
        'selection_metrics':{'sensitivity':'validation pair MSE','gnn':'fixed weighted residual GNN; seed0 primary, second seed robustness, no-edge and XGB comparators',
            'ranker':'validation all-query NDCG including zero-relevance groups','quantile':'validation interval score (width + 2/alpha miss distance)'},
        'test_policy':'Frozen before any model fit; selection workers load train and validation only; final test evaluated after selection-frozen.json',
        'independent_unit':'parent layout/event template, not event variant or decision snapshot'}
    dump(root/'protocol-frozen.json',protocol)
    rows=[];visible=[];truth=[];started=time.monotonic()
    hashes={p.name:sha(p) for p in Path(__file__).parent.glob('*.py') if p.name in ['analysis.py','world.py','geometry.py','planning.py','verification.py','synthetic_round3.py']}
    for i in range(config['template_count']):
        for variant in range(config['variants_per_template']):
            if time.monotonic()-started>config['max_generation_seconds']:raise TimeoutError('Generation budget exhausted; retain partial checkpoint')
            event=make_event(config['seed'],i,variant);r=label_event(event,config['solver_max_expansions'],config['solver_timeout_seconds']);r['split']=split[event['template_family']];rows.append(r)
            visible.append({k:v for k,v in event.items() if k!='simulator_truth'});truth.append({'event_id':event['event_id'],'truth':event['simulator_truth']})
    for name in ('train','validation','calibration','test'):dump(root/(name+'.json'),[r for r in rows if r['split']==name])
    for name,items in [('visible_scenarios.jsonl',visible),('simulator_truth.jsonl',truth)]:
        (root/name).write_text(''.join(json.dumps(x,ensure_ascii=False,allow_nan=False)+'\n' for x in items))
    manifest={'feature_version':FEATURE_VERSION,'counts':dict(Counter(r['split'] for r in rows)),
        'independent_templates':dict(Counter(split.values())),'total_tasks':sum(len(r['graph']['task_ids']) for r in rows),
        'total_pairs':sum(len(r['pairs']) for r in rows),'total_action_groups':sum(len(r['actions'])//3 for r in rows),
        'coverage_modes':dict(Counter(r['coverage_mode'] for r in rows)),'template_assignments':split,
        'features':{'sensitivity':PAIR_FEATURES,'gnn':NODE_FEATURES,'ranking':ACTION_FEATURES,'quantile':QUANTILE_FEATURES},
        'seed':config['seed'],'generation_seconds':time.monotonic()-started,'source_hashes':hashes,
        'protocol_sha256':sha(root/'protocol-frozen.json'),'split_sha256':{name:sha(root/(name+'.json')) for name in ('train','validation','calibration','test')},
        'source_hashes_after_generation':{p.name:sha(p) for p in Path(__file__).parent.glob('*.py') if p.name in hashes},
        'split_policy':'Parent template and all derived variants/sequence IDs share exactly one split',
        'feature_leakage_policy':'Explicit allowlists; no IDs, mode, truth, success, costs or downstream predictions in features',
        'upstream_predictions_in_downstream_features':False,'motion_scope':'Conservative parametric envelopes; precise crane dynamics not represented in training labels'}
    if hashes!=manifest['source_hashes_after_generation']:raise RuntimeError('Label code changed during generation; checkpoint invalid, do not fit')
    dump(root/'data_manifest.json',manifest);return manifest


def fit_worker(config,kind,index,seed,mode):
    import numpy as np
    import xgboost as xgb
    from .model_runtime import matrix
    from .learning_round3_runtime import ResidualGNN,interval_scale
    rows=load_rows(config,'train');out=Path(config['model_dir'])/'attempts';out.mkdir(parents=True,exist_ok=True)
    tag=f'{kind}-{index}-{seed}-{mode}';parameters=dict(config['xgboost_configs'][index],random_state=seed,tree_method='hist',device='cpu')
    if kind=='gnn':
        g=ResidualGNN(len(NODE_FEATURES),config['gnn']['hidden'],seed);loss=g.fit([r['graph'] for r in rows],config['gnn']['epochs'],config['gnn']['learning_rate'],mode)
        g.save(out/(tag+'.npz'));dump(out/(tag+'.json'),{'loss_first':loss[0],'loss_last':loss[-1],'epochs':len(loss),'edge_mode':mode});return
    if kind=='sensitivity':
        p=[p for r in rows for p in r['pairs']];m=xgb.XGBRegressor(objective='reg:squarederror',**parameters);m.fit(matrix([x['features'] for x in p],PAIR_FEATURES),[x['target'] for x in p])
    elif kind=='ranker':
        groups=[];actions=[]
        for r in rows:
            qs=defaultdict(list)
            for a in r['actions']:qs[a['query_id']].append(a)
            for group in qs.values():groups.append(len(group));actions.extend(group)
        m=xgb.XGBRanker(objective='rank:pairwise',**parameters);m.fit(matrix([a['features'] for a in actions],ACTION_FEATURES),[a['relevance'] for a in actions],group=groups)
    elif kind=='node_baseline':
        nodes=[x for r in rows for x in r['graph']['node_features']];targets=[y for r in rows for y in r['graph']['targets']]
        m=xgb.XGBRegressor(objective='reg:squarederror',**parameters);m.fit(matrix(nodes,NODE_FEATURES),targets)
    elif kind.startswith('quantile_'):
        alpha=config['quantile_alpha'];q=alpha/2 if kind.endswith('lower') else 1-alpha/2
        m=xgb.XGBRegressor(objective='reg:quantileerror',quantile_alpha=q,**parameters)
        m.fit(matrix([r['quantile']['features'] for r in rows],QUANTILE_FEATURES),[r['quantile']['target_end_error_s']/interval_scale(r['quantile']['features'],mode) for r in rows])
    else:raise ValueError('Unknown worker model')
    m.save_model(out/(tag+'.json'))


def fit(config):
    import numpy as np
    import xgboost as xgb
    from sklearn.metrics import ndcg_score
    from .model_runtime import matrix
    from .learning_round3_runtime import finite_group_calibration,interval_scale
    verify_paths(config);data=Path(config['data_dir']);root=Path(config['model_dir']);root.mkdir(parents=True,exist_ok=True)
    if (root/'selection-frozen.json').exists():raise FileExistsError('Selection already frozen; do not retune against observed test')
    manifest=json.loads((data/'data_manifest.json').read_text());validation=load_rows(config,'validation');start=time.monotonic();durations={}
    dump(root/'fit-config.json',config)
    jobs=[];seed=config['seeds'][0]
    for k in ('sensitivity','ranker'):
        for i in range(len(config['xgboost_configs'])):jobs.append((k,i,seed,'none'))
    for s in config['seeds']:
        for mode in ('weighted','isolated'):jobs.append(('gnn',0,s,mode))
    jobs.append(('node_baseline',0,seed,'none'))
    for mode in config['quantile_scale_modes']:
        for k in ('quantile_lower','quantile_upper'):jobs.append((k,0,seed,mode))
    for k,i,s,m in jobs:
        remaining=config['total_training_budget_seconds']-(time.monotonic()-start)
        if remaining<=0:raise TimeoutError('Cumulative training budget exhausted; checkpoints retained')
        tick=time.monotonic();subprocess.run([sys.executable,'-m','chengkong.training_round3','worker','--config',str(root/'fit-config.json'),
            '--kind',k,'--index',str(i),'--seed',str(s),'--mode',m],timeout=min(config['worker_timeout_seconds'],remaining),check=True,capture_output=True,text=True)
        durations[f'{k}-{i}-{s}-{m}']=time.monotonic()-tick
    attempts=root/'attempts';selection={};scores={}
    pairs=[p for r in validation for p in r['pairs']];px=matrix([p['features'] for p in pairs],PAIR_FEATURES);py=np.array([p['target'] for p in pairs])
    for kind in ('sensitivity','ranker'):
        measures=[]
        for i in range(len(config['xgboost_configs'])):
            tag=f'{kind}-{i}-{seed}-none';m=xgb.XGBRegressor() if kind=='sensitivity' else xgb.XGBRanker();m.load_model(attempts/(tag+'.json'))
            if kind=='sensitivity':score=float(np.mean((m.predict(px)-py)**2))
            else:
                nd=[]
                for r in validation:
                    groups=defaultdict(list)
                    for a in r['actions']:groups[a['query_id']].append(a)
                    for aa in groups.values():nd.append(ndcg_score([[a['relevance'] for a in aa]],[m.predict(matrix([a['features'] for a in aa],ACTION_FEATURES))]))
                score=-float(np.mean(nd))
            measures.append((score,tag))
        measures.sort();selection[kind]=measures[0][1];scores[kind]=[{'tag':tag,'selection_loss':score} for score,tag in measures]
        shutil.copyfile(attempts/(selection[kind]+'.json'),root/(kind+'.json'))
    selection['gnn']=f'gnn-0-{seed}-weighted';shutil.copyfile(attempts/(selection['gnn']+'.npz'),root/'gnn.npz')
    qx=matrix([r['quantile']['features'] for r in validation],QUANTILE_FEATURES);qy=np.array([r['quantile']['target_end_error_s'] for r in validation]);qm=[]
    for mode in config['quantile_scale_modes']:
        models=[]
        for side in ('lower','upper'):
            m=xgb.XGBRegressor();m.load_model(attempts/f'quantile_{side}-0-{seed}-{mode}.json');models.append(m)
        scales=np.array([interval_scale(r['quantile']['features'],mode) for r in validation]);a=models[0].predict(qx)*scales;b=models[1].predict(qx)*scales;lo=np.minimum(a,b);hi=np.maximum(a,b)
        interval_score=float(np.mean(hi-lo+2/config['quantile_alpha']*np.maximum(lo-qy,0)+2/config['quantile_alpha']*np.maximum(qy-hi,0)))
        qm.append((interval_score,mode))
    qm.sort();qmode=qm[0][1];selection['quantile_scale_mode']=qmode;scores['quantile']=[{'mode':m,'validation_interval_score':s} for s,m in qm]
    for side in ('lower','upper'):shutil.copyfile(attempts/f'quantile_{side}-0-{seed}-{qmode}.json',root/f'quantile_{side}.json')
    # Seal model choices before opening calibration or test rows.
    frozen={'frozen_unix_s':time.time(),'selection':selection,'validation_scores':scores,'test_sha256':manifest['split_sha256']['test'],
        'protocol_sha256':manifest['protocol_sha256'],'training_seconds':durations,'total_fit_wall_seconds':time.monotonic()-start,
        'default_policy':'deterministic_rule','selection_uses_test':False,'max_primary_configurations':2}
    dump(root/'selection-frozen.json',frozen)
    calibration=load_rows(config,'calibration');models=[]
    for side in ('lower','upper'):
        m=xgb.XGBRegressor();m.load_model(root/f'quantile_{side}.json');models.append(m)
    cx=matrix([r['quantile']['features'] for r in calibration],QUANTILE_FEATURES);a=models[0].predict(cx);b=models[1].predict(cx);res=defaultdict(list)
    for i,r in enumerate(calibration):
        target=r['quantile']['target_end_error_s']/interval_scale(r['quantile']['features'],qmode);lo,hi=sorted([float(a[i]),float(b[i])]);res[r['template_family']].append(max(lo-target,target-hi,0))
    cal=finite_group_calibration(res,config['quantile_alpha']);cal.update(scale_mode=qmode,alpha=config['quantile_alpha'],calibration_rows=len(calibration),
        calibration_template_ids=sorted(res),group_scores={k:max(v) for k,v in res.items()},method='maximum standardized interval nonconformity per independent template',
        not_flight_safety_probability=True,assumptions='Exchangeable synthetic parent families; calibration/test variants generated with same within-family process')
    dump(root/'calibration.json',cal)
    version='synthetic-round3-'+hashlib.sha256((manifest['split_sha256']['train']+json.dumps(selection,sort_keys=True)).encode()).hexdigest()[:12]
    out={'model_version':version,'feature_version':FEATURE_VERSION,'status':'trained_synthetic','trained':True,'default_policy':'deterministic_rule','scope':config['scope'],
        'data_manifest':manifest,'config':config,'selection':frozen,'calibration':cal,'artifacts':{},'data_dir':str(data),'xgboost_version':xgb.__version__,'numpy_version':np.__version__}
    for f in ('sensitivity.json','ranker.json','gnn.npz','quantile_lower.json','quantile_upper.json','calibration.json'):out['artifacts'][f]={'sha256':sha(root/f),'bytes':(root/f).stat().st_size}
    dump(root/'manifest.json',out);return out


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['audit','benchmark','generate','train','fit','evaluate','worker']);p.add_argument('--config',default='configs/training-round3.json')
    p.add_argument('--kind');p.add_argument('--index',type=int,default=0);p.add_argument('--seed',type=int,default=0);p.add_argument('--mode',default='none');args=p.parse_args();config=json.loads(Path(args.config).read_text());verify_paths(config)
    if args.command=='worker':fit_worker(config,args.kind,args.index,args.seed,args.mode);return
    if args.command=='audit':result=audit_round2(config)
    elif args.command=='benchmark':result=benchmark(config)
    elif args.command=='generate':result=generate(config)
    elif args.command in ('train','fit'):
        if args.command=='train' and not (Path(config['data_dir'])/'data_manifest.json').exists():generate(config)
        result=fit(config)
    else:
        from .evaluation_round3 import evaluate
        result=evaluate(config)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))

if __name__=='__main__':main()
