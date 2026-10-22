"""Public deterministic computation entry, receiving only decision-visible world."""
from .analysis import check_task, check_joint_tasks
from .planning import generate_candidates
from .verification import verify_candidate
from .world import visible_world, generate_constraints
from .evidence import generate_requests, resolve_requests
from .learning import capability_statuses


def run_analysis(raw,scenario_id='end_unconfirmed',as_of_s=60):
    world=visible_world(raw,scenario_id,as_of_s)
    constraints=generate_constraints(world)
    reports={t['id']:check_task(t,constraints,world) for t in world['tasks']}
    baseline_candidate={'id':'baseline-review','tasks':world['tasks'],
                        'base_environment_version':world['environment_version'],
                        'base_plan_versions':{t['id']:t['plan_version'] for t in world['tasks']},
                        'scenario_id':world['scenario_id'],'rule_version':world['rule_version'],
                        'model_version':world['model_version'],'valid_interval_s':world['horizon_s']}
    search_diagnostics=[]
    proposals=generate_candidates(world,constraints,search_diagnostics)
    for proposal in proposals:
        proposal['review']=verify_candidate(proposal,world,constraints)
        proposal['status']=proposal['review']['status']
    started=any(t['departure_time_s']<world['as_of_s'] and reports[t['id']]['status']!='clear' for t in world['tasks'])
    requests=generate_requests(world, {'task_results':list(reports.values())},
                               max_queries=1, deadline_s=min(world['horizon_s'][1],world['as_of_s']+60))
    return {'schema_version':'chengkong-result-v1','data_kind':'synthetic','is_real_case':False,
            'analysis_mode':'observed_snapshot_and_historical_replay' if as_of_s>60 else 'synthetic_forward_planning',
            'world':world,'constraints':constraints,'baseline_analysis':reports,
            'baseline_full_review':verify_candidate(baseline_candidate,world,constraints),
            'baseline_joint_analysis':check_joint_tasks(world['tasks'],world),'candidates':proposals,
            'planning_status':'needs_operational_review_started_task' if started else ('candidates_generated' if proposals else 'no_candidate_within_supported_search'),
            'current_plan_mutated':False, 'search_diagnostics':search_diagnostics,
            'learning_status':capability_statuses(),
            'evidence_requests':requests, 'evidence_results':resolve_requests(world,requests),
            'limits':['synthetic only; local metre frame and simulation seconds','finite route graph, bounded SIPP and priority planning; no completeness or optimality claim','no real flight control, learned inference, calibrated safety probability or automatic adoption','full recomputation on events in this iteration; selective incremental recomputation pending','beyond horizon remains unknown; not an activity completion time']}
