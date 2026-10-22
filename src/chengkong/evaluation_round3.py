"""Frozen-test statistics and actual equal-budget online solver comparisons."""
from __future__ import annotations
from collections import Counter,defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
import time
import numpy as np
from sklearn.metrics import ndcg_score
from .synthetic_round3 import *
from .model_runtime import ModelBundle,matrix
from .pair_keys import pair_key,split_pair_key
from .learning_round3_runtime import ResidualGNN,priority_checks,interval_scale
from .training_round3 import dump,sha,load_rows


def avg(x):return float(np.mean(x)) if len(x) else None
def distribution(x):
    return {'count':len(x),'mean':avg(x),'p50':float(np.quantile(x,.5)) if len(x) else None,'p90':float(np.quantile(x,.9)) if len(x) else None,'max':max(map(float,x),default=None)}
def ndcg(y,p):return float(ndcg_score([y],[p])) if len(y)>1 else float(y[0]>0)
def paired_group_ci(values,seed=42):
    """Bootstrap independent families, never pretend variants are independent."""
    means=np.array([np.mean(x) for x in values.values()]);rng=np.random.default_rng(seed)
    if not len(means):return {'groups':0,'mean_difference':None,'bootstrap95':None}
    boot=np.mean(rng.choice(means,(1000,len(means)),replace=True),axis=1)
    return {'groups':len(means),'mean_difference':float(means.mean()),'bootstrap95':[float(np.quantile(boot,.025)),float(np.quantile(boot,.975))],
        'unit':'paired difference averaged within template then bootstrap templates; descriptive small-sample uncertainty'}


def audit_dataset(rows,manifest):
    names=PAIR_FEATURES+NODE_FEATURES+ACTION_FEATURES+QUANTILE_FEATURES
    forbidden=['actual_cost','feasible','relevance','solve_status','template_family','coverage_mode','actual_end_s','event_id','sequence_id']
    if set(names)&set(forbidden):raise AssertionError('Feature answer leakage')
    families=defaultdict(set);groups={};ties=0
    for r in rows:
        families[r['parent_template']].add(r['split'])
        q=defaultdict(list)
        for a in r['actions']:
            if set(a['features'])!=set(ACTION_FEATURES):raise AssertionError('Action feature schema drift')
            q[a['query_id']].append(a)
        for key,actions in q.items():
            if key in groups:raise AssertionError('Repeated query group')
            groups[key]=r['split']
            if sorted(a['action'] for a in actions)!=['delay','detour','keep']:raise AssertionError('Incomplete action group')
            equal=defaultdict(set)
            for a in actions:
                if a['feasible']:equal[round(a['actual_cost'],6)].add(a['relevance'])
            if any(len(v)>1 for v in equal.values()):raise AssertionError('Equal costs must retain equal ranks')
            ties+=len({a['relevance'] for a in actions})<len(actions)
    if any(len(v)>1 for v in families.values()):raise AssertionError('Cross-parent leakage')
    return {'status':'passed','independent_parent_families':len(families),'complete_candidate_groups':len(groups),'groups_with_tied_relevance':ties,
        'explicit_feature_allowlists':True,'ids_modes_labels_excluded':True,'upstream_fitted_predictions_used':False,
        'missingness_note':'Missing geometry mask is legitimately decision-visible; it is sampled independently of hidden outcome draw. No missingness bit alone provides an outcome label.'}


def static_metrics(rows,bundle,config):
    import xgboost as xgb
    p=[p for r in rows for p in r['pairs']];y=np.array([p['target'] for p in p]);pred=bundle.sensitivity_model.predict(matrix([x['features'] for x in p],PAIR_FEATURES))
    rule=np.array([float(x['features']['bbox_clearance_m']==0 and x['features']['temporal_overlap_s']>0) for x in p]);sens_ci=defaultdict(list)
    offset=0;sens_ndcg=[]
    graph_metrics=defaultdict(list);graph_ci=defaultdict(list);nonuniform=0
    node=xgb.XGBRegressor();seed=config['seeds'][0];node.load_model(bundle.path/'attempts'/f'node_baseline-0-{seed}-none.json')
    seed_models={}
    for s in config['seeds']:
        for mode in ('weighted','isolated'):seed_models[(s,mode)]=ResidualGNN.load(bundle.path/'attempts'/f'gnn-0-{s}-{mode}.npz')
    rng=np.random.default_rng(config['seed']+99)
    for row in rows:
        count=len(row['pairs']);s=pred[offset:offset+count];rr=rule[offset:offset+count];yy=y[offset:offset+count]
        sens_ci[row['template_family']].append(float(np.mean((s-yy)**2-(rr-yy)**2)));offset+=count
        sens_ndcg.append(ndcg(yy,s));g=row['graph'];x=matrix(g['node_features'],NODE_FEATURES);target=g['targets'];a=np.asarray(g['adjacency']);n=len(target)
        predictions={'rule_bbox':[float(v['min_bbox_clearance_m']==0 and v['max_temporal_overlap_s']>0) for v in g['node_features']],
            'same_feature_node_xgb':node.predict(x),'priority_rule':[1/(1+v['priority']) for v in g['node_features']]}
        for (s,mode),m in seed_models.items():predictions[f'{mode}_seed_{s}']=m.forward(x,a if mode=='weighted' else np.eye(n))[0]
        predictions['trained_weighted_removed_edges']=bundle.gnn.forward(x,np.eye(n))[0]
        permutation=rng.permutation(n);predictions['trained_weighted_permuted_edges']=bundle.gnn.forward(x,a[permutation][:,permutation])[0]
        informative=len(set(target))>1;nonuniform+=informative
        for name,value in predictions.items():
            graph_metrics[name+'_ndcg'].append(ndcg(target,value));graph_metrics[name+'_mse'].append(float(np.mean((np.array(value)-target)**2)))
            if informative:graph_metrics[name+'_ndcg_discriminative'].append(ndcg(target,value))
        graph_ci[row['template_family']].append(graph_metrics[f'weighted_seed_{seed}_ndcg'][-1]-graph_metrics['rule_bbox_ndcg'][-1])
    ranking=defaultdict(list);common_cost_diff=defaultdict(list);groups=0;solvable=0;changed=0;allzero=0;ties=0
    for row in rows:
        qs=defaultdict(list)
        for a in row['actions']:qs[a['query_id']].append(a)
        for aa in qs.values():
            groups+=1;s=bundle.ranker.predict(matrix([a['features'] for a in aa],ACTION_FEATURES));order=sorted(range(len(aa)),key=lambda i:(-float(s[i]),i));changed+=order!=list(range(len(aa)))
            rel=[a['relevance'] for a in aa];allzero+=not any(rel);ties+=len(set(rel))<len(rel)
            ranking['model_ndcg'].append(ndcg(rel,s));ranking['rule_ndcg'].append(ndcg(rel,[3,2,1]));ranking['model_top1_feasible'].append(aa[order[0]]['feasible']);ranking['rule_top1_feasible'].append(aa[0]['feasible'])
            if any(a['feasible'] for a in aa):
                solvable+=1;chosen=next(i for i in order if aa[i]['feasible']);base=next(i for i,a in enumerate(aa) if a['feasible'])
                ranking['model_common_first_cost'].append(aa[chosen]['actual_cost']);ranking['rule_common_first_cost'].append(aa[base]['actual_cost']);common_cost_diff[row['template_family']].append(aa[chosen]['actual_cost']-aa[base]['actual_cost'])
    return {'sensitivity':{'pair_denominator':len(p),'mse':float(np.mean((pred-y)**2)),'bbox_rule_mse':float(np.mean((rule-y)**2)),
        'event_mean_ndcg':avg(sens_ndcg),'template_mse_difference_ci':paired_group_ci(sens_ci),'target':'fraction of declared parameter perturbations that conflict; not incident probability'},
        'gnn':{'event_graph_denominator':len(rows),'discriminative_graphs':nonuniform,'zero_positive_graphs':sum(not any(r['graph']['targets']) for r in rows),
            'metrics':{k:avg(v) for k,v in graph_metrics.items()},'weighted_vs_rule_template_ndcg_ci':paired_group_ci(graph_ci),
            'interpretation':'Nonuniform-label subset reports actual ranking discrimination; all-equal graphs otherwise inflate/flatten NDCG. No graph is removed from all-graph metrics.'},
        'ranking':{'complete_query_denominator':groups,'all_zero_relevance_queries':allzero,'queries_with_ties':ties,'solvable_queries':solvable,
            'overall_unsolved_fraction':1-solvable/groups,'changed_order_queries':changed,'metrics':{k:avg(v) for k,v in ranking.items()},
            'common_success_cost_denominator':solvable,'cost_difference_template_ci':paired_group_ci(common_cost_diff)}}


def sensitivity_online(events,rows,bundle):
    records=[];byid={r['event_id']:r for r in rows}
    for event in events:
        world=event['world'];constraints=generate_constraints(world);row=byid[event['event_id']];critical={p['pair_id'] for p in row['pairs'] if p['observed_conflict']}
        related={split_pair_key(k)[0] for k in critical};budget=max(1,(len(world['tasks'])*len(constraints)+1)//2)
        for name,model in [('rule',None),('learned',bundle)]:
            half=priority_checks(world,constraints,model,max_pairs=budget);full=priority_checks(world,constraints,model)
            done={x['pair_id'] for x in half['executed']};taskdone={tid for tid in related if all(pair_key(tid,c['id']) in done for c in constraints)}
            records.append({'event_id':event['event_id'],'family':event['template_family'],'strategy':name,'pair_budget':budget,'required_pairs':full['required_pairs'],
                'first_conflict_seconds':full['first_conflict_seconds'],'inference_seconds':full['inference_seconds'],'total_seconds':full['total_seconds'],
                'relevant_pairs':len(critical),'budget_relevant_pairs_checked':len(critical&done),'relevant_tasks':len(related),'budget_relevant_tasks_fully_checked':len(taskdone),
                'unchecked_pair_fraction':len(half['unchecked_pair_ids'])/max(1,full['required_pairs']),
                'unprocessed_never_clear':all(any(u.get('reason')=='priority_check_budget_exhausted' for u in half['reports'][split_pair_key(k)[0]]['unchecked']) for k in half['unchecked_pair_ids'])})
    summary={}
    for name in ('rule','learned'):
        selected=[r for r in records if r['strategy']==name];critical=sum(r['relevant_pairs'] for r in selected);tasks=sum(r['relevant_tasks'] for r in selected)
        summary[name]={'events':len(selected),'events_with_conflict':sum(r['first_conflict_seconds'] is not None for r in selected),
            'first_conflict_seconds':distribution([r['first_conflict_seconds'] for r in selected if r['first_conflict_seconds'] is not None]),
            'budget_relevant_pair_completion':sum(r['budget_relevant_pairs_checked'] for r in selected)/critical if critical else None,
            'budget_relevant_task_full_completion':sum(r['budget_relevant_tasks_fully_checked'] for r in selected)/tasks if tasks else None,
            'related_task_denominator':tasks,'related_pair_denominator':critical,'mean_unchecked_pair_fraction':avg([r['unchecked_pair_fraction'] for r in selected]),
            'total_seconds':sum(r['total_seconds'] for r in selected),'inference_seconds':sum(r['inference_seconds'] for r in selected)}
    return {'scope':'Actually executed current-constraint checks; 50% pair-count budget and full pass. Timing includes feature/model inference; no low-score obligation discarded.', 'summary':summary,'records':records}


def ranking_online(events,bundle,config):
    records=[];budget=config['online_query_attempt_budget'];solve_status=Counter()
    for ei,event in enumerate(events):
        world=event['world'];constraints=generate_constraints(world);graph=graph_features(world,constraints)
        for task in world['tasks']:
            proposals=[{'id':a,'action':a,'task_ids':[task['id']]} for a in ('keep','delay','detour')]
            tick=time.perf_counter();advice=bundle.rank_proposals(world,constraints,proposals);infer=time.perf_counter()-tick
            for strategy in (('rule','learned') if ei%2==0 else ('learned','rule')):
                order=['keep','delay','detour'] if strategy=='rule' else advice['proposal_order'];start=time.perf_counter();attempts=[];selected=None
                for action in order[:budget]:
                    result=solve_action(world,constraints,task,action,config['solver_max_expansions'],config['solver_timeout_seconds']);attempts.append(result);solve_status[result['solve_status']]+=1
                    if result['feasible']:selected=result;break
                records.append({'query_id':event['event_id']+':'+task['id'],'family':event['template_family'],'strategy':strategy,'order':order,
                    'inference_seconds':infer if strategy=='learned' else 0,'seconds_to_first_or_stop':time.perf_counter()-start+(infer if strategy=='learned' else 0),
                    'attempts':len(attempts),'feasible':selected is not None,'selected':selected,'attempt_statuses':[a['solve_status'] for a in attempts],
                    'stop_class':'feasible' if selected else 'timeout' if any(a['solve_status']=='timeout' for a in attempts) else
                        'information_insufficient' if any('unsupported' in a['solve_status'] for a in attempts) else 'attempt_budget_exhausted',
                    'global_infeasibility_proven':False})
    queries=defaultdict(dict)
    for r in records:queries[r['query_id']][r['strategy']]=r
    common=[q for q in queries.values() if all(q[x]['feasible'] for x in ('rule','learned'))];summary={}
    for name in ('rule','learned'):
        rr=[r for r in records if r['strategy']==name];good=[r for r in rr if r['feasible']]
        summary[name]={'query_denominator':len(rr),'feasible_count':len(good),'task_completion_rate_under_fixed_attempt_budget':len(good)/len(rr),
            'overall_failure_fraction':1-len(good)/len(rr),'attempts_all_queries':distribution([r['attempts'] for r in rr]),
            'seconds_all_queries':distribution([r['seconds_to_first_or_stop'] for r in rr]),
            'first_feasible_seconds_success_only':distribution([r['seconds_to_first_or_stop'] for r in good]),
            'mean_inference_seconds':avg([r['inference_seconds'] for r in rr]),'stop_classes':dict(Counter(r['stop_class'] for r in rr)),
            'common_success_cost':avg([q[name]['selected']['actual_cost'] for q in common]),
            'common_success_seconds':distribution([q[name]['seconds_to_first_or_stop'] for q in common]),
            'common_success_attempts':distribution([q[name]['attempts'] for q in common]),
            'common_success_metrics':{key:avg([q[name]['selected']['metrics'][key] for q in common]) for key in ('changed_tasks','delay_s','distance_m','extra_distance_m','energy_used')}}
    return {'scope':'Actual repeated local solver calls; same complete action set, features, two-attempt budget, expansion/time limits; fixed other tasks. Joint integration separately evaluated.',
        'common_success_query_denominator':len(common),'attempt_budget':budget,'solve_status_counts':dict(solve_status),'summary':summary,'records':records}


def conditional_end_advice(world,task,upper_error,width):
    """A hypothetical first-activity completion, never an occupancy release."""
    slack=task['deadline_s']-task['trajectory'][-1]['time_s']
    evidence=width is None or width>slack
    if task['departure_time_s']<world['as_of_s']:
        return 'requires_frozen_prefix_replan',evidence
    if upper_error is None:return 'defer_and_request_evidence',evidence
    end=world['activities'][0]['planned_interval_s'][1]+upper_error
    delay=max(0,end-task['departure_time_s'])
    return ('delay_if_completion_confirmed' if delay<=slack else 'detour_or_hold_needs_solver'),evidence


def calibration_metrics(rows,bundle,config,events):
    worlds={event['event_id']:event['world'] for event in events}
    records=[];group=defaultdict(list);rawgroup=defaultdict(list);impact=Counter()
    for row in rows:
        result=bundle.end_error_interval(row['quantile']['features']);lo,hi=result['uncalibrated_interval_s'];a,b=result['interval_s'];y=row['quantile']['target_end_error_s']
        covered=(a is None or a<=y<=b);raw=lo<=y<=hi;group[row['template_family']].append(covered);rawgroup[row['template_family']].append(raw)
        world=worlds[row['event_id']]
        # End-error model concerns only the first activity. Keep absolute times
        # aligned; a task may not depart until long after the decision time.
        planned_end=world['activities'][0]['planned_interval_s'][1]
        for task in world['tasks']:
            slack=task['deadline_s']-task['trajectory'][-1]['time_s']

            for name,upper,width in [('raw',hi,hi-lo),('calibrated',b,(b-a) if a is not None else None)]:
                suggested,needs_evidence=conditional_end_advice(world,task,upper,width)
                impact[name+':'+suggested]+=1
                if needs_evidence:impact[name+':evidence_due_interval_exceeding_slack']+=1
        records.append({'event_id':row['event_id'],'family':row['template_family'],'target_s':y,'raw_interval_s':[lo,hi],'calibrated_interval_s':[a,b],
            'raw_covered':raw,'covered':covered,'width_s':b-a if a is not None else None})
    return {'target':'First activity end-time error, one scalar, not mission safety or joint time-series coverage',
        'nominal_coverage':bundle.calibration['nominal_coverage'],'calibration_status':bundle.calibration['status'],
        'calibration_independent_templates':bundle.calibration['independent_template_groups'],'calibration_rows':bundle.calibration['calibration_rows'],
        'order_statistic_k':bundle.calibration['order_statistic_index_1based'],'selected_scale_mode':bundle.calibration['scale_mode'],
        'test_event_denominator':len(rows),'test_family_denominator':len(group),
        'raw_event_coverage':avg([r['raw_covered'] for r in records]),'calibrated_event_coverage':avg([r['covered'] for r in records]),
        'raw_all_variants_family_coverage':avg([all(v) for v in rawgroup.values()]),'calibrated_all_variants_family_coverage':avg([all(v) for v in group.values()]),
        'raw_width_s':distribution([r['raw_interval_s'][1]-r['raw_interval_s'][0] for r in records]),
        'calibrated_width_s':distribution([r['width_s'] for r in records if r['width_s'] is not None]),
        'conditional_advice_impact_counts':dict(impact),'impact_scope':'First-activity conditional delay uses max(0, planned_end + predicted_error - task_departure), compared with deadline minus original arrival; other activities/resources ignored, so advisory only. Predictions never end occupancy or authorize a candidate.',
        'records':records}


def joint_online(events,bundle,config):
    from .planning import generate_candidates
    from .execution import replan_inflight
    records=[];budget={'max_orders':4,'max_searches':12,'max_expansions':512,'timeout_s':1.0}
    for event in events[:config['joint_evaluation_event_limit']]:
        world=deepcopy(event['world']);world['model_version']=bundle.version;constraints=generate_constraints(world)
        for name in ('rule','learned'):
            start=time.perf_counter();hints=None
            if name=='learned':
                association=bundle.association(world,constraints);rank=bundle.rank_proposals(world,constraints,[{'id':a,'action':a} for a in ('delay','detour')]);hints={'task_order':association['task_order'],'action_order':rank['proposal_order'],'model_version':bundle.version}
            infer=time.perf_counter()-start;diagnostics=[]
            inprogress=any(t['departure_time_s']<world['as_of_s']<t['trajectory'][-1]['time_s'] for t in world['tasks'])
            candidates=(replan_inflight if inprogress else generate_candidates)(world,constraints,diagnostics,budget=budget,planning_hints=hints)
            feasible=[c for c in candidates if c.get('review',{}).get('status')=='conditionally_feasible']
            selected=min(feasible,key=lambda c:c['stability_cost']['values']) if feasible else None
            records.append({'event_id':event['event_id'],'family':event['template_family'],'strategy':name,'inflight':inprogress,
                'elapsed_seconds':time.perf_counter()-start,'inference_seconds':infer,'feasible':bool(feasible),'tasks_completed':len(world['tasks']) if feasible else 0,
                'required_tasks':len(world['tasks']),'candidate_count':len(candidates),'selected_stability_cost':selected.get('stability_cost') if selected else None,
                'diagnostic_status_counts':dict(Counter(d.get('status','task_attempt') for d in diagnostics)),
                'task_orders':[d['priority_order'] for d in diagnostics if 'priority_order' in d]})
    return {'scope':'Fixed first test event IDs selected by protocol order, actual joint planner under identical candidate generation and hard constraints; limited subset, not statistical proof',
        'budget':budget,'events':len(records)//2,'records':records}


def evaluate(config):
    root=Path(config['model_dir']);data=Path(config['data_dir']);frozen=json.loads((root/'selection-frozen.json').read_text())
    if sha(data/'test.json')!=frozen['test_sha256']:raise ValueError('Frozen final test changed')
    bundle=ModelBundle(root);rows=load_rows(config,'test');allrows=sum((load_rows(config,s) for s in ('train','validation','calibration','test')),[])
    events=[json.loads(line) for line in (data/'visible_scenarios.jsonl').read_text().splitlines()];ids={r['event_id'] for r in rows};events=[e for e in events if e['event_id'] in ids]
    started=time.monotonic();report={'model_version':bundle.version,'test_policy':'First final evaluation after immutable model selection; subsequent evaluations reproduce frozen selection without retuning',
        'default_policy':'deterministic_rule','split_counts':bundle.manifest['data_manifest']['counts'],'independent_template_counts':bundle.manifest['data_manifest']['independent_templates'],
        'audit':audit_dataset(allrows,bundle.manifest['data_manifest']),'static_metrics':static_metrics(rows,bundle,config)}
    report['sensitivity_online']=sensitivity_online(events,rows,bundle);report['ranking_online']=ranking_online(events,bundle,config)
    report['calibration']=calibration_metrics(rows,bundle,config,events);report['joint_online']=joint_online(events,bundle,config);report['evaluation_seconds']=time.monotonic()-started
    report['limits']=['Synthetic local geometry only; no real data and no certified probabilities.',
        'Labels and query timing target local fixed-reservation actions; full multi-task results are separately reported.',
        'Precise crane motion is an external stress domain, not represented in this learning corpus.',
        'Elapsed times are small CPU workloads and sensitive to scheduler/cache; repeated seeds apply to graph weights, not independent test families.',
        'Conformal guarantee requires exchangeable families; single scalar outcome and sampled within-family process only.',
        'Default rule remains unchanged; negative comparisons retained.']
    # Keep the first observed final test intact; reruns get independent timing records.
    output=root/'evaluation.json'
    first_evaluation=not output.exists()
    if not first_evaluation:output=root/('evaluation-reproduction-'+str(time.time_ns())+'.json')
    report['result_path']=str(output)
    dump(output,report)
    summary={k:v for k,v in report.items() if k not in ('sensitivity_online','ranking_online','calibration','joint_online')}
    for k in ('sensitivity_online','ranking_online','calibration','joint_online'):summary[k]={a:b for a,b in report[k].items() if a!='records'}
    summary.update(artifact_dir=str(root),data_dir=str(data),evaluation_sha256=sha(output),result_path=str(output))
    if first_evaluation:dump('configs/learning-round3-artifacts.json',summary)
    return summary
