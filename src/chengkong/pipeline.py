"""Versioned decision-visible analysis shared by CLI, simulation and local Web."""
from __future__ import annotations

import copy
import hashlib
import json

from .analysis import check_task,check_joint_tasks
from .planning import generate_candidates
from .verification import verify_candidate
from .world import visible_world,generate_constraints
from .evidence import generate_requests,resolve_requests
from .learning import capability_statuses


def analyze_world(world,*,engine=None,inflight=False,learned=False,model_bundle=None,planning_budget=None,existing_candidates=None):
    world=copy.deepcopy(world)
    world.setdefault('reservation_version',1)
    if model_bundle is not None:
        world['model_version']=model_bundle.version
    constraints=generate_constraints(world)
    incremental=engine.analyze(world,candidates=existing_candidates) if engine is not None else None
    reports=incremental['baseline_analysis'] if incremental else {t['id']:check_task(t,constraints,world) for t in world['tasks']}
    baseline_candidate={'id':'baseline-review','tasks':world['tasks'],
                        'base_environment_version':world['environment_version'],
                        'base_reservation_version':world['reservation_version'],
                        'base_plan_versions':{t['id']:t['plan_version'] for t in world['tasks']},
                        'scenario_id':world['scenario_id'],'rule_version':world['rule_version'],
                        'model_version':world['model_version'],'valid_interval_s':world['horizon_s']}
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
    diagnostics=[]
    kwargs={'budget':planning_budget,'planning_hints':hints}
    if inflight:
        from .execution import replan_inflight
        proposals=replan_inflight(world,constraints,diagnostics,**kwargs)
    else:
        proposals=generate_candidates(world,constraints,diagnostics,**kwargs)
    for proposal in proposals:
        if hints:
            proposal['ranking_model_version']=model_bundle.version
            proposal['ranking_source']='trained_synthetic_explicit_opt_in'
        proposal['review']=verify_candidate(proposal,world,constraints)
        proposal['status']=proposal['review']['status']
        identity={k:proposal.get(k) for k in ('kind','tasks','base_environment_version','base_plan_versions','base_reservation_version','rule_version','model_version','ranking_model_version','ranking_source','scenario_id','execution_context')}
        identity['as_of_s']=world['as_of_s']
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()[:16]
        proposal['id']='candidate-'+digest
        proposal['review']['candidate_id']=proposal['id']
    # Both evidence triggers share the exact source/budget/version contract.
    requests=[]
    for source_layer in ('conflict_analysis','maintenance_decision'):
        requests+=generate_requests(world,{'task_results':list(reports.values())},source_layer=source_layer,
                                    candidates=proposals,max_queries=1,
                                    deadline_s=min(world['horizon_s'][1],world['as_of_s']+60))
    if learned and hints:
        scores=advice['sensitivity'].get('scores',{})
        for request in requests:
            related=[scores.get(f"{tid}:{constraint['id']}",0.0)
                     for tid in request['related_task_ids'] for constraint in constraints
                     if constraint['object_id']==request['object_id']]
            request['sensitivity_priority_score']=max(related,default=0.0)
            request['ranking_model_version']=model_bundle.version
        requests.sort(key=lambda q:(-q['sensitivity_priority_score'],q['id']))
    started=any(t['departure_time_s']<world['as_of_s'] and reports[t['id']]['status']!='clear' for t in world['tasks'])
    status='candidates_generated' if proposals else 'no_candidate_within_supported_search'
    if started and not inflight:status='needs_operational_review_started_task'
    return {'schema_version':'chengkong-result-v2','data_kind':'synthetic','is_real_case':False,
            'analysis_mode':'inflight_simulation' if inflight else 'observed_snapshot_and_historical_replay',
            'world':world,'constraints':constraints,'baseline_analysis':reports,
            'baseline_full_review':verify_candidate(baseline_candidate,world,constraints),
            'baseline_joint_analysis':incremental['joint_analysis'] if incremental else check_joint_tasks(world['tasks'],world),
            'candidates':proposals,'planning_status':status,'current_plan_mutated':False,'search_diagnostics':diagnostics,
            'learning_status':capability_statuses(model_dir=model_bundle.path if model_bundle is not None else None),'learning_advice':advice,'learning_enabled':bool(learned and hints),
            'evidence_requests':requests,'evidence_results':resolve_requests(world,requests),
            'incremental':incremental,
            'limits':['synthetic local frame and fixed velocity model only',
                      'finite graph and bounded priority repair; no global completeness or optimality claim',
                      'learned scores only order work; every hard constraint is independently checked',
                      'historical unknowns remain traceable; evidence is never invented',
                      'not connected to flight control; explicit adoption required']}


def run_analysis(raw,scenario_id='end_unconfirmed',as_of_s=60,**options):
    return analyze_world(visible_world(raw,scenario_id,as_of_s),**options)
