"""Versioned decision-visible analysis shared by CLI, simulation and local Web."""
from __future__ import annotations

import copy
import hashlib
import json
import time

from .analysis import check_task,check_joint_tasks
from .planning import generate_candidates
from .verification import verify_candidate
from .world import visible_world,generate_constraints
from .evidence import generate_requests,resolve_requests
from .learning import capability_statuses
from .pair_keys import pair_key


def analyze_world(world,*,engine=None,inflight=False,learned=False,model_bundle=None,planning_budget=None,existing_candidates=None,checking_budget=None,measure=False):
    started_clock=time.perf_counter();timings={};phase=started_clock
    world=copy.deepcopy(world)
    world.setdefault('reservation_version',1)
    if model_bundle is not None:
        world['model_version']=model_bundle.version
    constraints=generate_constraints(world)
    baseline_candidate={'id':'baseline-review','tasks':world['tasks'],
                        'base_environment_version':world['environment_version'],
                        'base_reservation_version':world['reservation_version'],
                        'base_plan_versions':{t['id']:t['plan_version'] for t in world['tasks']},
                        'scenario_id':world['scenario_id'],'rule_version':world['rule_version'],
                        'model_version':world['model_version'],'valid_interval_s':world['horizon_s']}
    timings['constraint_generation_s']=time.perf_counter()-phase;phase=time.perf_counter()
    advice={};hints=None
    if model_bundle is not None:
        try:
            advice['sensitivity']=model_bundle.sensitivity(world,constraints)
            advice['association']=model_bundle.association(world,constraints)
            proposals=[{'id':mode,'action':mode,'task_ids':[t['id'] for t in world['tasks']]} for mode in ('delay','detour')]
            advice['repair_ranking']=model_bundle.rank_proposals(world,constraints,proposals)
            if world['activities']:
                from .synthetic import quantile_features
                advice['end_error_interval']=model_bundle.end_error_interval(quantile_features(world))
            if learned:
                hints={'task_order':advice['association'].get('task_order',[]),
                       'action_order':advice['repair_ranking'].get('proposal_order',[]),
                       'model_version':model_bundle.version,'source':'trained_synthetic_explicit_opt_in'}
        except (ValueError,KeyError,ImportError,TypeError) as exc:
            advice={'status':'unavailable','reason':str(exc)}
    timings['model_advice_s']=time.perf_counter()-phase;phase=time.perf_counter()
    # The learned priority is applied before the first exact object check.
    # This scheduling experiment does not replace mandatory full candidate review.
    checking_schedule=None;incremental=None
    if (learned and hints) or checking_budget is not None:
        from .learning_round3_runtime import priority_checks
        checking_schedule=priority_checks(world,constraints,model_bundle if learned and hints else None,**(checking_budget or {}))
        reports=checking_schedule.pop('reports')
        checking_schedule['scope']='initial task-object checking pass; planning and full verification have separate mandatory checks'
        checking_schedule['incremental_cache']='bypassed_to_measure_explicit_check_order'
    else:
        incremental=engine.analyze(world,candidates=existing_candidates) if engine is not None else None
        reports=incremental['baseline_analysis'] if incremental else {t['id']:check_task(t,constraints,world) for t in world['tasks']}
    timings['initial_check_s']=time.perf_counter()-phase;phase=time.perf_counter()
    diagnostics=[]
    kwargs={'budget':planning_budget,'planning_hints':hints}
    if inflight:
        from .execution import replan_inflight
        proposals=replan_inflight(world,constraints,diagnostics,**kwargs)
    else:
        proposals=generate_candidates(world,constraints,diagnostics,**kwargs)
    timings['search_with_internal_review_s']=time.perf_counter()-phase;phase=time.perf_counter()
    for proposal in proposals:
        if hints:
            proposal['ranking_model_version']=model_bundle.version
            proposal['ranking_source']='trained_synthetic_explicit_opt_in'
        proposal['review']=verify_candidate(proposal,world,constraints)
        proposal['status']=proposal['review']['status']
        # The immutable artifact also includes its search/review provenance and
        # recommendation context. Equal trajectories under different budgets
        # can have different recommendations, so route-only IDs are insufficient.
        identity=copy.deepcopy(proposal)
        identity.pop('id',None)
        identity['review'].pop('candidate_id',None)
        identity['as_of_s']=world['as_of_s']
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()[:16]
        proposal['id']='candidate-'+digest
        proposal['review']['candidate_id']=proposal['id']
    timings['candidate_final_review_s']=time.perf_counter()-phase;phase=time.perf_counter()
    # Both evidence triggers share the exact source/budget/version contract.
    requests=[]
    for source_layer in ('conflict_analysis','maintenance_decision'):
        requests+=generate_requests(world,{'task_results':list(reports.values())},source_layer=source_layer,
                                    candidates=proposals,max_queries=1,
                                    deadline_s=min(world['horizon_s'][1],world['as_of_s']+60))
    if learned and hints:
        scores=advice['sensitivity'].get('scores',{})
        for request in requests:
            related=[scores.get(pair_key(tid,constraint['id']),0.0)
                     for tid in request['related_task_ids'] for constraint in constraints
                     if constraint['object_id']==request['object_id']]
            request['sensitivity_priority_score']=max(related,default=0.0)
            request['ranking_model_version']=model_bundle.version
        requests.sort(key=lambda q:(-q['sensitivity_priority_score'],q['id']))
    started=any(t['departure_time_s']<world['as_of_s'] and reports[t['id']]['status']!='clear' for t in world['tasks'])
    status='candidates_generated' if proposals else 'no_candidate_within_supported_search'
    if started and not inflight:status='needs_operational_review_started_task'
    evidence_results=resolve_requests(world,requests)
    timings['evidence_s']=time.perf_counter()-phase;phase=time.perf_counter()
    baseline_review=verify_candidate(baseline_candidate,world,constraints)
    joint_analysis=incremental['joint_analysis'] if incremental else check_joint_tasks(world['tasks'],world)
    timings['baseline_full_review_s']=time.perf_counter()-phase
    timings['total_s']=time.perf_counter()-started_clock
    result={'schema_version':'chengkong-result-v2','data_kind':world.get('data_kind','synthetic'),'is_real_case':False,
            'analysis_mode':'inflight_simulation' if inflight else 'observed_snapshot_and_historical_replay',
            'world':world,'constraints':constraints,'baseline_analysis':reports,
            'baseline_full_review':baseline_review,
            'baseline_joint_analysis':joint_analysis,
            'candidates':proposals,'planning_status':status,'current_plan_mutated':False,'search_diagnostics':diagnostics,
            'learning_status':capability_statuses(model_dir=model_bundle.path if model_bundle is not None else None),'learning_advice':advice,'learning_enabled':bool(learned and hints),
            'evidence_requests':requests,'evidence_results':evidence_results,
            'incremental':incremental,
            'checking_schedule':checking_schedule,'timings':timings,
            'timing_scope':'wall clock per stage; search includes its internal mandatory reviews; load outside pipeline; model_advice includes feature extraction/inference',
            'limits':['synthetic local frame and fixed velocity model only',
                      'finite graph and bounded priority repair; no global completeness or optimality claim',
                      'learned scores only order work; every hard constraint is independently checked',
                      'historical unknowns remain traceable; evidence is never invented',
                      'not connected to flight control; explicit adoption required']}
    if not proposals:
        from .diagnostics_round4 import summarize_failure
        from .execution import simulate_context
        phase=time.perf_counter()
        result['failure_diagnosis']=summarize_failure(world,constraints,diagnostics,
            execution_context=simulate_context(world) if inflight else None)
        timings['failure_diagnosis_s']=time.perf_counter()-phase
    domain_reasons=[]
    if any(f.get('motion_model') for f in world['facilities']):domain_reasons.append('parameterized_dynamic_motion')
    if len({t['aircraft_id'] for t in world['tasks']})<len(world['tasks']):domain_reasons.append('same_aircraft_continuous_tasks')
    if len(world.get('event_log',[]))>1:domain_reasons.append('repeated_event_sequence_decisions')
    result['learning_applicability']={'status':'out_of_training_scope' if domain_reasons else 'synthetic_training_scope_only',
        'reasons':domain_reasons,'default_strategy':'rule','actual_model_version':world['model_version'],
        'new_domain_coverage_validated':False,'real_world_effectiveness_validated':False,
        'note':'Round2/3 models can only order work experimentally. Old end-error coverage is not a round4 or flight-safety guarantee.'}
    if domain_reasons and 'end_error_interval' in advice:
        advice['end_error_interval']['applicability']='out_of_training_scope_no_coverage_claim'
    from .explanations import maintenance_explanations,operation_timeline
    phase=time.perf_counter()
    result['maintenance_explanations']=maintenance_explanations(result)
    result['operation_timeline']=operation_timeline(result,proposals[0]['id'] if proposals else None)
    timings['explanation_s']=time.perf_counter()-phase
    timings['total_s']=time.perf_counter()-started_clock
    if not measure:
        # Wall-clock profiling is opt-in. The default computation contract stays
        # reproducible, and immutable candidate payloads never carry run clocks.
        result.pop('timings');result.pop('timing_scope')
        for diagnostic in diagnostics:
            for key in ('first_feasible_seconds','search_seconds','independent_review_seconds','elapsed_seconds','candidate_found_seconds'):
                diagnostic.pop(key,None)
    return result


def run_analysis(raw,scenario_id='end_unconfirmed',as_of_s=60,**options):
    return analyze_world(visible_world(raw,scenario_id,as_of_s),**options)
