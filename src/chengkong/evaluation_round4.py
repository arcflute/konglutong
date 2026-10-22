"""Receipt-ordered, fixed-budget synthetic sequence evaluation; explicit adoption.

Old round-three weights are an out-of-domain comparison, never safety authority.
All raw outcomes, failures and matched-success comparisons are saved.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from copy import deepcopy
import argparse,json,math,tempfile,time,signal
from pathlib import Path
from .synthetic_round4 import freeze,generate,make_sequence,sha
from .world import visible_world,advance_world,generate_constraints
from .analysis import check_task,check_joint_tasks
from .planning import generate_candidates
from .execution import replan_inflight,simulate_context
from .cooperation import _candidate,stability_cost
from .verification import verify_candidate,_task_requirements
from .resources import task_energy
from .storage import PlanStore
from .evidence import generate_requests,resolve_requests
from .diagnostics_round4 import summarize_failure


def dump(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False))


class TimedBundle:
    """Same stored weights/features; explicit feature and inference clocks."""
    def __init__(self,bundle,noedges=False):
        self.bundle=bundle;self.version=bundle.version;self.path=bundle.path;self.noedges=noedges;self.feature_s=0.;self.infer_s=0.
        self.gnn=bundle.gnn
        if noedges:
            from .learning_round3_runtime import ResidualGNN
            files=sorted((bundle.path/'attempts').glob('gnn-0-*-isolated.npz'))
            if not files:raise ValueError('Required same-feature trained no-edge control is absent')
            self.gnn=ResidualGNN.load(files[0])
    def reset(self):self.feature_s=0.;self.infer_s=0.
    def sensitivity(self,world,constraints):
        from .synthetic_round3 import pair_features,PAIR_FEATURES
        from .model_runtime import matrix
        from .pair_keys import pair_key
        tick=time.perf_counter();pairs=[(t,c,pair_features(t,c,world)) for t in world['tasks'] for c in constraints]
        x=matrix([p[2] for p in pairs],PAIR_FEATURES);self.feature_s+=time.perf_counter()-tick
        tick=time.perf_counter();pred=self.bundle.sensitivity_model.predict(x) if pairs else [];self.infer_s+=time.perf_counter()-tick
        scores={pair_key(t['id'],c['id']):float(v) for (t,c,_),v in zip(pairs,pred)}
        return {'pair_order':sorted(scores,key=lambda k:(-scores[k],k)),'scores':scores}
    def hints(self,world,constraints):
        import numpy as np
        from .synthetic_round3 import graph_features,action_features,NODE_FEATURES,ACTION_FEATURES
        from .model_runtime import matrix
        tick=time.perf_counter();g=graph_features(world,constraints);x=matrix(g['node_features'],NODE_FEATURES)
        aa=[action_features(t,a,world,constraints,g) for a in ('delay','detour') for t in world['tasks']];xx=matrix(aa,ACTION_FEATURES)
        self.feature_s+=time.perf_counter()-tick;tick=time.perf_counter()
        y=self.gnn.forward(x,np.eye(len(x)) if self.noedges else g['adjacency'])[0];scores=dict(zip(g['task_ids'],map(float,y)))
        pred=self.bundle.ranker.predict(xx);n=len(world['tasks']);ranks={a:float(np.mean(pred[i*n:(i+1)*n])) for i,a in enumerate(('delay','detour'))}
        self.infer_s+=time.perf_counter()-tick
        return {'task_order':sorted(scores,key=lambda k:(-scores[k],k)),'action_order':sorted(ranks,key=lambda k:(-ranks[k],k)),
            'model_version':self.version,'source':'round3_out_of_domain_stress','node_scores':scores,'action_scores':ranks}


def run_sequence(seq,strategy,config,bundle=None,reference=False,output_dir=None,global_deadline=None):
    from .learning_round3_runtime import priority_checks
    started=time.perf_counter();raw=seq['input'];world=visible_world(raw,seq['sequence_id'],seq['decision_times_s'][0])
    world['model_version']=bundle.version if bundle else 'none';world.setdefault('reservation_version',1)
    world['planning_policy']['mode']='conservative' if strategy=='conservative_rule' else 'dynamic_windows'
    world['planning_policy']['action_order']=['retain','detour','delay'] if strategy in ('dynamic_keep_detour','high_budget_reference') else ['retain','delay','detour']
    oldworld=deepcopy(world);modifications=Counter();rows=[];pending={};pending_cost=0.;resolved_waits=[]
    # Every strategy gets its own isolated store; simulated adoption uses the
    # actual explicit transaction and never touches demonstration databases.
    with tempfile.TemporaryDirectory(prefix='chengkong-round4-') as folder:
        store=PlanStore(Path(folder)/'state.db');store.initialize(world)
        for di,at in enumerate(seq['decision_times_s']):
            if global_deadline is not None and time.monotonic()>=global_deadline:raise TimeoutError('Global/reference budget exhausted before decision; checkpoint retained')
            tick=time.perf_counter();timings={};world=store.current_world()
            newly=[e for e in raw['scenario_definitions_for_simulator_only'][0]['events'] if e['observed_at_s']<=at and e['id'] not in {x['id'] for x in world['observed_events']}]
            world=advance_world(world,at,newly);store.replace_world(world,expected_revision=store.current_world()['state_revision']);world=store.current_world()
            visible_ids={e['id'] for e in world['observed_events']}
            assert all(e['observed_at_s']<=at for e in world['observed_events'])
            phase=time.perf_counter();constraints=generate_constraints(world);timings['constraints_s']=time.perf_counter()-phase
            phase=time.perf_counter();context=simulate_context(world);inflight=any(v['status']=='in_progress' for v in context['tasks'].values())
            context=context if inflight or any(t['departure_time_s']<at for t in world['tasks']) else None
            if bundle:bundle.reset()
            checks=priority_checks(world,constraints,bundle);hints=bundle.hints(world,constraints) if bundle else None
            timings['initial_checks_and_model_s']=time.perf_counter()-phase
            timings['features_s']=bundle.feature_s if bundle else 0.;timings['inference_s']=bundle.infer_s if bundle else 0.
            baseline=_candidate(world,constraints,deepcopy(world['tasks']),'retain',[],[],[],context,0)
            phase=time.perf_counter();baseline_review=verify_candidate(baseline,world,constraints);timings['baseline_review_s']=time.perf_counter()-phase
            baseline_elapsed=time.perf_counter()-tick
            d=[];budget=dict(config['reference_budget'] if reference else config['budget']);phase=time.perf_counter()
            decision_limit=config['reference_budget']['timeout_s'] if reference else config.get('decision_compute_budget_seconds',budget['timeout_s'])
            pre_search_elapsed=phase-tick
            remaining=max(0.,decision_limit-pre_search_elapsed)
            if global_deadline is not None:remaining=min(remaining,max(0.,global_deadline-time.monotonic()))
            budget['timeout_s']=min(budget['timeout_s'],remaining)
            cs=(replan_inflight if context else generate_candidates)(world,constraints,d,budget=budget,planning_hints=hints)
            timings['search_with_internal_review_s']=time.perf_counter()-phase
            summary=next((r for r in reversed(d) if r.get('status')=='bounded_cooperative_search_summary'),{})
            feasible=[c for c in cs if c.get('review',{}).get('feasible') and summary.get('candidate_found_seconds',{}).get(c['id'],math.inf)<=remaining]
            late_feasible=[c['id'] for c in cs if c.get('review',{}).get('feasible') and summary.get('candidate_found_seconds',{}).get(c['id'],math.inf)>remaining]
            chosen=min(feasible,key=lambda c:tuple(c['stability_cost']['values'])) if feasible else None
            # Do not replace an already fully reviewed incumbent just because a
            # newly generated route happens to have a different shape.
            if baseline_review['feasible'] and baseline_elapsed<=decision_limit:chosen=baseline
            verified_decision_elapsed=time.perf_counter()-tick
            if chosen is None and (late_feasible or baseline_review['feasible'] and baseline_elapsed>decision_limit):
                d.append({'status':'decision_deadline_exhausted','failure_class':'timeout','deadline_seconds':decision_limit,'late_feasible_candidate_ids':late_feasible,'global_infeasibility_proven':False})
            phase=time.perf_counter();requests=[]
            for layer in ('conflict_analysis','maintenance_decision'):
                requests+=generate_requests(world,source_layer=layer,candidates=None,max_queries=1,deadline_s=min(world['horizon_s'][1],at+60))
            responses=resolve_requests(world,requests);timings['evidence_s']=time.perf_counter()-phase
            for request in requests:
                key=(request['object_id'],request['field'])
                pending.setdefault(key,at)
            for key,first in list(pending.items()):
                obj=next((a for a in world['activities'] if a['id']==key[0]),None)
                if obj and obj['completion_status']=='confirmed':resolved_waits.append(at-first);del pending[key]
            changed=[];adopted=False;phase=time.perf_counter()
            if chosen and not baseline_review['feasible']:
                changed=[x['task_id'] for x in chosen['changes'] if x['changed']]
                result=store.adopt(chosen,explicit=True)
                if not result['adopted']:raise AssertionError(('Reviewed simulation candidate not adoptable',result))
                adopted=True;modifications.update(changed)
            timings['transaction_adoption_s']=time.perf_counter()-phase
            failure=summarize_failure(world,constraints,d,feasible,context) if not chosen else None
            metrics=[]
            if chosen:
                for task in chosen['tasks']:
                    original=next(x for x in world['tasks'] if x['id']==task['id'])
                    _,_,met=_task_requirements(task,original,dict(world,_candidate_tasks=chosen['tasks']),context);metrics.append(met)
            timings['total_s']=time.perf_counter()-tick
            current=store.current_world();rows.append({'decision_id':seq['sequence_id']+f':d{di}','template_id':seq['template_id'],'parent_template':seq['parent_template'],'strategy':strategy,
                'as_of_s':at,'visible_event_ids':sorted(visible_ids),'newly_arrived_ids':[e['id'] for e in newly],
                'visible_construction_sha256':sha({k:world[k] for k in ('facilities','activities','known_sources','event_log')}),
                'environment_version':world['environment_version'],'plan_versions':{t['id']:t['plan_version'] for t in current['tasks']},
                'reservation_version':current['reservation_version'],'rule_version':world['rule_version'],'model_version':world['model_version'],
                'model_scope':'out_of_domain_unvalidated_sequence_and_motion' if bundle else 'deterministic_rule',
                'joint_feasible':chosen is not None,'required_task_occurrences':len(world['tasks']),'joint_plan_covered_task_occurrences':len(world['tasks']) if chosen else 0,
                'incumbent_retained':baseline_review['feasible'],'adopted':adopted,'changed_task_ids':changed,
                'selected_kind':chosen['kind'] if chosen else None,'selected_changes':chosen['changes'] if chosen else [],
                'selected_stability_cost':chosen['stability_cost'] if chosen else None,'selected_task_metrics':metrics,
                'cumulative_cost_from_common_initial':stability_cost(chosen['tasks'],oldworld['tasks'])[1] if chosen else None,
                'absolute_selected_cost':{'path_length_m':sum(m.get('path_length_m',0) for m in metrics),'energy_used':sum(m.get('energy_used',0) for m in metrics),'arrival_time_sum_s':sum(m.get('arrival_time_s',0) for m in metrics)} if chosen else None,
                'first_feasible_seconds':baseline_elapsed if chosen is baseline else
                    pre_search_elapsed+min(summary['candidate_found_seconds'][c['id']] for c in feasible) if feasible else None,
                'first_feasible_scope':'From decision start including state update, constraints, model features/inference, checks and review; model loading once separately recorded.',
                'decision_compute_budget_seconds':decision_limit,'remaining_solver_seconds':remaining,'verified_decision_elapsed_s':verified_decision_elapsed,
                'decision_deadline_overrun_s':max(0.,verified_decision_elapsed-decision_limit),'late_feasible_candidate_ids':late_feasible,
                'required_pairs':checks['required_pairs'],'attempted_pairs':checks['completed_pairs'],'unchecked_pairs':len(checks['unchecked_pair_ids']),
                'unresolved_pair_count':sum(x['status']=='not_checked' for x in checks['executed']),
                'pair_check_scope':'Current tasks against every constraint; completed attempts with unknown geometry remain unresolved.',
                'evidence_requests':requests,'evidence_results':responses,'pending_evidence_count':len(pending),
                'search_diagnostics':d,'candidate_action_set':['keep','delay','detour'],'keep_test':'same baseline full review before repair search','search_budget':budget,'failure':failure,'timings':timings,'search_summary':summary,
                'independent_review_rejections':[x for x in d if x.get('failure_class')=='independent_review_rejected'],
                'historical_conflicts':baseline_review.get('historical_conflicts',[]),
                'selected_candidate':chosen,'current_tasks':current['tasks']})
            if output_dir:dump(Path(output_dir)/(seq['template_id']+'-'+strategy+'-checkpoint.json'),{'completed_decisions':di+1,'rows':rows,'current_world':current})
        final=store.current_world();final_constraints=generate_constraints(final);joint=check_joint_tasks(final['tasks'],final)
        # Reconstruct execution to the last receipt. Credit needs full-path
        # object/mission/joint checks, not just existence of a future candidate.
        completed=[];unverified=[]
        for task in final['tasks']:
            if task['trajectory'][-1]['time_s']>final['as_of_s']:continue
            report=check_task(task,final_constraints,final);v,u,_=_task_requirements(task,task,dict(final,_candidate_tasks=final['tasks']))
            related=[c for c in joint['conflicts']+joint['unchecked'] if task['id'] in c.get('task_ids',[]) or c.get('task_id')==task['id'] or not c.get('task_ids') and not c.get('task_id')]
            historical=[c for row in rows for c in row['historical_conflicts'] if c.get('task_id')==task['id']]
            if report['status']=='clear' and not v and not u and not related and not historical:completed.append(task['id'])
            else:unverified.append(task['id'])
        initial_by={t['id']:t for t in oldworld['tasks']};changes,stability=stability_cost(final['tasks'],oldworld['tasks'])
        result={'template_id':seq['template_id'],'parent_template':seq['parent_template'],'strategy':strategy,'coverage_pattern':seq['coverage_pattern'],'rows':rows,
            'simulation_adoption_policy':config['simulation_adoption_policy'],'required_unique_tasks':len(final['tasks']),
            'simulated_executed_tasks_with_valid_checks':completed,'ended_but_unverified_tasks':unverified,
            'still_scheduled_tasks':[t['id'] for t in final['tasks'] if t['trajectory'][-1]['time_s']>final['as_of_s']],
            'execution_metric_scope':'Counterfactual incumbent replay; full trajectory checked against final known historical constraints plus past recorded conflicts. Not actual flight or guaranteed safety.',
            'task_modification_counts':dict(modifications),'repeat_modifications':sum(max(0,n-1) for n in modifications.values()),
            'final_stability_cost':stability,'final_changes':changes,'evidence_resolved_wait_seconds':resolved_waits,
            'evidence_censored_wait_seconds':[final['as_of_s']-first for first in pending.values()],
            'evidence_wait_is_flight_delay':False,'total_seconds':time.perf_counter()-started,'final_world':final}
        return result


def aggregate(records):
    grouped=defaultdict(list)
    for r in records:grouped[r['strategy']].append(r)
    summaries={}
    for name,seqs in grouped.items():
        rows=[row for seq in seqs for row in seq['rows']];good=[r for r in rows if r['joint_feasible']]
        summaries[name]={'independent_parent_topologies':len({s['parent_template'] for s in seqs}),'parameter_sequences':len(seqs),'decision_denominator':len(rows),'joint_feasible_decisions':len(good),
            'required_task_occurrences':sum(r['required_task_occurrences'] for r in rows),'joint_plan_covered_task_occurrences':sum(r['joint_plan_covered_task_occurrences'] for r in rows),
            'unique_task_denominator':sum(s['required_unique_tasks'] for s in seqs),'executed_with_valid_checks':sum(len(s['simulated_executed_tasks_with_valid_checks']) for s in seqs),
            'ended_unverified':sum(len(s['ended_but_unverified_tasks']) for s in seqs),'still_scheduled':sum(len(s['still_scheduled_tasks']) for s in seqs),
            'failed_decision_categories':dict(Counter(x for r in rows if r['failure'] for x in r['failure']['categories'])),
            'required_pairs':sum(r['required_pairs'] for r in rows),'attempted_pairs':sum(r['attempted_pairs'] for r in rows),
            'unresolved_pairs':sum(r['unresolved_pair_count'] for r in rows),'unchecked_pair_fraction':sum(r['unchecked_pairs'] for r in rows)/max(1,sum(r['required_pairs'] for r in rows)),
            'first_feasible_timed_success_denominator':sum(r['first_feasible_seconds'] is not None for r in good),
            'first_feasible_seconds_success_only':sum(r['first_feasible_seconds'] for r in good if r['first_feasible_seconds'] is not None)/max(1,sum(r['first_feasible_seconds'] is not None for r in good)),
            'decision_deadline_overruns':sum(r['decision_deadline_overrun_s']>0 for r in rows),
            'deadline_overrun_seconds':sum(r['decision_deadline_overrun_s'] for r in rows),
            'late_feasible_candidates_not_credited':sum(len(r['late_feasible_candidate_ids']) for r in rows),
            'changed_task_occurrences':sum(len(r['changed_task_ids']) for r in rows),'repeat_modifications':sum(s['repeat_modifications'] for s in seqs),
            'evidence_queries':sum(sum(x['query_cost'] for x in r['evidence_results']) for r in rows),
            'evidence_wait_seconds_sum':sum(sum(s['evidence_resolved_wait_seconds']+s['evidence_censored_wait_seconds']) for s in seqs),
            'review_rejected_candidates':sum(len(r['independent_review_rejections']) for r in rows),
            'total_seconds':sum(s['total_seconds'] for s in seqs),'stage_seconds':{k:sum(r['timings'].get(k,0) for r in rows) for k in rows[0]['timings']} if rows else {},
            'final_positive_delay_sum_s':sum(s['final_stability_cost']['values'][1] for s in seqs),
            'final_positive_distance_delta_sum_m':sum(s['final_stability_cost']['values'][2] for s in seqs),
            'final_positive_energy_delta_sum':sum(s['final_stability_cost']['values'][3] for s in seqs)}
    decisions=defaultdict(dict)
    for seq in records:
        if seq['strategy']=='high_budget_reference':continue
        for r in seq['rows']:decisions[r['decision_id']][seq['strategy']]=r
    names=[k for k in grouped if k!='high_budget_reference'];common=[rs for rs in decisions.values() if set(rs)==set(names) and all(r['joint_feasible'] for r in rs.values())]
    common_summary={}
    for name in names:
        rr=[r[name] for r in common]
        common_summary[name]={'decision_denominator':len(rr),'mean_delay_s':sum(r['cumulative_cost_from_common_initial']['values'][1] for r in rr)/max(1,len(rr)),
            'mean_distance_delta_m':sum(r['cumulative_cost_from_common_initial']['values'][2] for r in rr)/max(1,len(rr)),
            'mean_energy_delta':sum(r['cumulative_cost_from_common_initial']['values'][3] for r in rr)/max(1,len(rr)),
            'mean_absolute_path_m':sum(r['absolute_selected_cost']['path_length_m'] for r in rr)/max(1,len(rr)),
            'mean_absolute_energy':sum(r['absolute_selected_cost']['energy_used'] for r in rr)/max(1,len(rr)),
            'mean_absolute_arrival_sum_s':sum(r['absolute_selected_cost']['arrival_time_sum_s'] for r in rr)/max(1,len(rr)),
            'mean_total_seconds':sum(r['timings']['total_s'] for r in rr)/max(1,len(rr))}
    return {'strategies':summaries,'all_strategy_common_success_decisions':len(common),'common_success':common_summary,
        'comparison_limit':'Arrival/environment/requirements equal; incumbent plans can diverge after explicit simulated adoption. Common-success cost is cumulative from the same initial plan and absolute path/energy/arrival, never zeroed by divergent incumbents. Only three hand-defined synthetic parent topologies.'}


def run(config,mode='pilot',correction_reason=None):
    root=Path(config['result_dir']);root.mkdir(parents=True,exist_ok=True);protocol=freeze(config)
    from .model_runtime import ModelBundle
    tick=time.perf_counter();bundle=ModelBundle(config['model_dir']);load_s=time.perf_counter()-tick
    strategies=list(config['strategies'])+['dynamic_learned_noedges_ood'];records=[];start=time.monotonic()
    if mode=='pilot':selected=[r for r in protocol['templates'] if r['split']=='train'][:2]
    else:
        generate(config)
        selection={'protocol_sha256':protocol['identity_sha256'],'model_version':bundle.version,
            'model_manifest_sha256':sha(bundle.manifest),'model_scope':'OOD sequence/dynamic/same-aircraft, not retrained',
            'strategies':strategies,'code_sha256':{str(p):sha(p.read_text()) for p in sorted(Path('src/chengkong').glob('*.py'))},'configuration_selection':'fixed prior to final test; no test-driven tuning','created_at_unix_s':time.time()}
        frozen=root/'selection-frozen.json'
        if not frozen.exists():dump(frozen,selection)
        else:
            old=json.loads(frozen.read_text())
            if any(old[k]!=selection[k] for k in selection if k not in ('created_at_unix_s','code_sha256')):raise ValueError('Frozen selection/model/strategy/protocol changed')
            if old['code_sha256']!=selection['code_sha256']:
                correction_path=root/('selection-code-correction-'+sha(selection['code_sha256'])[:16]+'.json')
                if not correction_path.exists():
                    if not correction_reason:raise ValueError('Frozen evaluation code changed: explicit --correction-reason required; no model/strategy retuning allowed')
                    dump(correction_path,{'reason':correction_reason,'created_at_unix_s':time.time(),
                        'original_selection_code_sha256':old['code_sha256'],'corrected_code_sha256':selection['code_sha256'],
                        'protocol_model_strategy_budget_unchanged':True,'previous_test_already_observed':True,
                        'first_result_preserved':True,'claim':'Corrected execution reproduction, not a new untouched test.'})
        selected=[r for r in protocol['templates'] if r['split']=='test']
    reference_seconds=0.;reference_status=[];reference_parents=set();adapter_load_seconds=defaultdict(float)
    global_deadline=time.monotonic()+config['total_evaluation_budget_seconds']
    for i,record in enumerate(selected):
        seq=make_sequence(record)
        # Alternate execution order to avoid always giving one scheme warm cache.
        order=strategies if i%2==0 else list(reversed(strategies))
        for strategy in order:
            if time.monotonic()-start>config['total_evaluation_budget_seconds']:
                dump(root/(mode+'-budget-checkpoint.json'),{'records':records,'stopped_before':[record['template_id'],strategy]});raise TimeoutError('Configured evaluation budget exhausted; checkpoint retained')
            load_tick=time.perf_counter();adapter=TimedBundle(bundle,noedges=strategy=='dynamic_learned_noedges_ood') if 'learned' in strategy else None
            adapter_load_seconds[strategy]+=time.perf_counter()-load_tick
            r=run_sequence(seq,strategy,config,adapter,output_dir=root/'checkpoints'/mode,global_deadline=global_deadline);records.append(r)
            print(record['template_id'],strategy,sum(row['joint_feasible'] for row in r['rows']),len(r['rows']),round(r['total_seconds'],3),flush=True)
        if mode!='pilot' and record['parent_template'] not in reference_parents and len(reference_parents)<config['reference_template_limit']:
            reference_parents.add(record['parent_template'])
            remaining_reference=config['total_reference_budget_seconds']-reference_seconds
            if remaining_reference<=0:reference_status.append({'template_id':record['template_id'],'status':'reference_budget_exhausted'});continue
            reference_started=time.monotonic()
            def stop_reference(*_):raise TimeoutError('Configured cumulative reference budget exhausted')
            old_handler=signal.signal(signal.SIGALRM,stop_reference);signal.setitimer(signal.ITIMER_REAL,min(remaining_reference,max(.001,global_deadline-time.monotonic())))
            try:
                r=run_sequence(seq,'high_budget_reference',config,reference=True,output_dir=root/'checkpoints'/mode,global_deadline=min(global_deadline,reference_started+remaining_reference));records.append(r)
                reference_status.append({'template_id':record['template_id'],'status':'reference_completed','optimality_proven':False,'global_infeasibility_proven':False})
            except TimeoutError as exc:reference_status.append({'template_id':record['template_id'],'status':'reference_budget_exhausted','reason':str(exc),'checkpoint_retained':True})
            finally:
                signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old_handler);reference_seconds+=time.monotonic()-reference_started
    result={'schema_version':'round4-sequence-results-v1','mode':mode,'model_load_seconds_once':load_s,'model_version':bundle.version,
        'model_scope':'Round3 weights used outside learned sequence/motion/same-aircraft domain; no new calibration coverage claim.',
        'runtime_scope':'Evaluator uses the same saved features/weights, batches one graph extraction for GNN and ranker, and forwards predictions into the real cooperative planner. Model load is cold; candidate decisions are warm. This batching is not an assertion that Web end-to-end timings equal these evaluation timings.',
        'code_sha256':{str(p):sha(p.read_text()) for p in sorted(Path('src/chengkong').glob('*.py'))},'protocol_sha256':protocol['identity_sha256'],'budget':config['budget'],'reference_budget':config['reference_budget'],
        'reference_seconds':reference_seconds,'reference_parent_count':len(reference_parents),'reference_status':reference_status,'adapter_load_seconds':dict(adapter_load_seconds),
        'cold_load_cost_policy':'model_load_seconds_once plus each strategy adapter_load_seconds excluded from warm decision time but explicitly charged once for cold learned deployment; rule has no model load.',
        'summary':aggregate(records),'records':records,'elapsed_seconds':time.monotonic()-start}
    path=root/(mode+'-results.json')
    if path.exists():path=root/(mode+'-reproduction-'+str(time.time_ns())+'.json')
    dump(path,result);print(json.dumps({'result':str(path),'summary':result['summary']},ensure_ascii=False));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['pilot','evaluate','generate']);p.add_argument('--config',default='configs/evaluation-round4.json');p.add_argument('--correction-reason');a=p.parse_args();c=json.loads(Path(a.config).read_text())
    if a.mode=='generate':print(json.dumps(generate(c)))
    else:run(c,'pilot' if a.mode=='pilot' else 'final',a.correction_reason)
