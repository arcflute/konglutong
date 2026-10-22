"""Budgeted priority reordering with finite order backtracking and repair closure.

This is deliberately a bounded heuristic over the existing finite SIPP graph.
It is neither globally optimal nor complete; exhausted budgets and unsupported
inputs are separate from necessary-condition proofs of infeasibility.
"""
from __future__ import annotations

import copy
import itertools
import math
import time

from .analysis import check_joint_tasks,check_task
from .resources import task_energy,prepare_continuation

DEFAULT_BUDGET={'max_orders':12,'max_searches':96,'max_expansions':10000,'timeout_s':8.0}


def stability_cost(tasks,originals):
    changes=[]
    for old in originals:
        new=next(t for t in tasks if t['id']==old['id'])
        length=lambda t:sum(math.dist(a['position_m'],b['position_m']) for a,b in zip(t['trajectory'],t['trajectory'][1:]))
        changes.append({'task_id':old['id'],'changed':new['trajectory']!=old['trajectory'],
            'departure_shift_s':new.get('actual_departure_time_s',new['departure_time_s'])-old.get('actual_departure_time_s',old['departure_time_s']),
            'arrival_shift_s':new['trajectory'][-1]['time_s']-old['trajectory'][-1]['time_s'],
            'distance_delta_m':length(new)-length(old),'energy_delta':task_energy(new)-task_energy(old)})
    cost=[sum(c['changed'] for c in changes),sum(max(0.,c['arrival_shift_s']) for c in changes),
          sum(max(0.,c['distance_delta_m']) for c in changes),sum(max(0.,c['energy_delta']) for c in changes)]
    return changes,{'ordering':['changed_tasks','positive_arrival_delay_s','positive_distance_delta_m','positive_energy_delta'],
        'values':cost,'semantics':'lexicographic after all hard constraints pass; not a global optimum'}


def _future_conflicts(report,at):
    return [c for c in report['conflicts'] if 'interval_s' not in c or c['interval_s'][1]>at or (c['interval_s'][0]==c['interval_s'][1] and c['interval_s'][0]>=at)]


def _candidate(world,constraints,tasks,mode,searches,affected,history,context,index):
    changes,cost=stability_cost(tasks,world['tasks'])
    result={'id':f"candidate-{world['scenario_id']}-{world['environment_version']}-{world.get('reservation_version',1)}-{mode}-{index}",
        'candidate_schema_version':2,'kind':mode,'tasks':sorted(tasks,key=lambda t:t['id']),
        'base_environment_version':world['environment_version'],'base_reservation_version':world.get('reservation_version',1),
        'base_plan_versions':{t['id']:t['plan_version'] for t in world['tasks']},
        'rule_version':world['rule_version'],'model_version':world['model_version'],'scenario_id':world['scenario_id'],
        'valid_interval_s':list(world['horizon_s']),'changes':changes,'stability_cost':cost,'searches':searches,
        'repair_task_ids':sorted(affected),'repair_expansion_history':copy.deepcopy(history),'status':'pending_review',
        'conditions':['software-only synthetic local frame and fixed piecewise-linear motion',
            'ground wait occupies the declared launch pad continuously; no off-pad instant entry',
            'airborne waiting consumes spatial reservation and linear energy',
            'bounded priority search has no completeness or global-optimality guarantee',
            'same-aircraft reuse requires the explicit configured ground-carrier model'],
        'ranking_source':'deterministic_rule_v2','constraints_used':[c['id'] for c in constraints]}
    if context is not None:result['execution_context']=copy.deepcopy(context)
    return result


def cooperative_candidates(world,constraints,diagnostics=None,budget=None,execution_context=None,planning_hints=None,ranking_hook=None):
    from .planning import search_task,_ground_departures,_geometry_bounds
    from .verification import verify_candidate, _task_requirements
    from .execution import validate_context,suffix_task,restore_full_task
    diagnostics=[] if diagnostics is None else diagnostics
    config=DEFAULT_BUDGET | (budget or {});start=time.monotonic()
    calls=0;orders_used=0
    if any(not isinstance(config[k],int) or isinstance(config[k],bool) or config[k]<0 for k in ('max_orders','max_searches','max_expansions')) or config['timeout_s']<0:
        diagnostics.append({'status':'invalid_search_budget'});return []
    deadline=start+config['timeout_s'];at=world['as_of_s'] if execution_context is not None else -math.inf
    if execution_context is not None and not validate_context(execution_context,world):
        diagnostics.append({'status':'invalid_execution_context'});return []
    baseline={t['id']:check_task(t,constraints,world,world['as_of_s'] if execution_context else None) for t in world['tasks']}
    if any(not r['checks_completed'] for r in baseline.values()):
        diagnostics.append({'status':'incomplete_constraint_checks','failure_class':'information_insufficient'});return []
    try:
        for c in constraints:_geometry_bounds(c['geometry'])
    except (ValueError,TypeError,KeyError) as exc:
        diagnostics.append({'status':'unsupported_or_incomplete_search_input','failure_class':'information_insufficient','reason':str(exc)});return []
    joint=check_joint_tasks(world['tasks'],world,world['as_of_s'] if execution_context else None)
    affected={t['id'] for t in world['tasks'] if _future_conflicts(baseline[t['id']],at)}
    affected.update(tid for c in _future_conflicts(joint,at) for tid in c.get('task_ids',[]))
    # Requirements can invalidate an otherwise geometrically clear task. These
    # must join the repair set before candidate generation, not only at review.
    for task in world['tasks']:
        violations,unchecked,_=_task_requirements(task,task,dict(world,_candidate_tasks=world['tasks']),execution_context)
        if unchecked:
            diagnostics.append({'status':'incomplete_task_requirement_information','failure_class':'information_insufficient',
                'task_id':task['id'],'unchecked':unchecked})
            return []
        if violations:
            if execution_context and execution_context['tasks'][task['id']]['status']=='completed':
                diagnostics.append({'status':'historical_task_requirement_violation','task_id':task['id'],
                    'violations':violations,'action':'cannot_rewrite_executed_prefix'})
            else:
                affected.add(task['id'])
                diagnostics.append({'status':'repair_set_requirement_expanded','task_id':task['id'],'violations':violations})
    # Reuse energy/admission depends on the complete aircraft chain. Reserving
    # only a changed successor silently treated it as a fresh full battery.
    aircraft={t['aircraft_id'] for t in world['tasks'] if t['id'] in affected}
    closure={t['id'] for t in world['tasks'] if t['aircraft_id'] in aircraft}
    if closure-affected:
        diagnostics.append({'status':'repair_set_aircraft_chain_expanded','added_task_ids':sorted(closure-affected)})
        affected.update(closure)
    if joint['unchecked']:
        diagnostics.append({'status':'incomplete_joint_resource_information','failure_class':'information_insufficient','unchecked':joint['unchecked']});return []
    original={t['id']:t for t in world['tasks']}
    if execution_context is None and any(original[t]['departure_time_s']<world['as_of_s'] for t in affected):
        diagnostics.append({'status':'needs_operational_review_started_task','action':'use replan_inflight for software-only frozen-prefix maintenance'});return []
    modes=['retain'] if not affected else ['delay','detour']
    hints=dict(planning_hints or {})
    hints.setdefault('action_order',world.get('planning_policy',{}).get('action_order',[]))
    if hints.get('action_order'):
        modes.sort(key=lambda x:(hints['action_order'].index(x) if x in hints['action_order'] else len(hints['action_order'])))
    proposals=[{'id':f'proposal-{mode}','action':mode,'task_ids':sorted(affected)} for mode in modes]
    ranking_info=None
    if ranking_hook is not None:
        try:
            ranking_info=ranking_hook(copy.deepcopy(proposals))
            ranking=ranking_info.get('proposal_order',[]) if isinstance(ranking_info,dict) else ranking_info
            if sorted(ranking)==sorted(p['id'] for p in proposals):
                lookup={p['id']:p for p in proposals};proposals=[lookup[i] for i in ranking]
            else:diagnostics.append({'status':'invalid_ranking_hook_order_rule_fallback'})
        except Exception as exc:
            diagnostics.append({'status':'ranking_hook_failed_rule_fallback','reason':str(exc)})
    candidates=[];expansion_history=[];first_feasible_seconds=None;search_seconds=0.;review_seconds=0.;candidate_found_seconds={}
    for proposal_index,proposal in enumerate(proposals):
        mode_order_start=orders_used;mode_call_start=calls
        mode_order_limit=max(1,config['max_orders']//len(proposals))
        mode_call_limit=max(1,config['max_searches']//len(proposals))
        mode_deadline=start+config['timeout_s']*(proposal_index+1)/len(proposals)
        mode=proposal['action'];repair=set(affected)
        restart=True
        while restart:
            restart=False
            if time.monotonic()>=mode_deadline:
                diagnostics.append({'status':'cooperative_timeout','failure_class':'timeout'});break
            order_base=sorted(repair,key=lambda tid:(original[tid]['priority'],tid))
            if hints.get('task_order'):
                ranking={v:i for i,v in enumerate(hints['task_order'])}
                order_base.sort(key=lambda tid:(ranking.get(tid,len(ranking)),original[tid]['priority'],tid))
            orders=itertools.permutations(order_base) if repair else [()]
            for order in orders:
                # Priority permutations cannot reverse continuous same-aircraft
                # missions. Different aircraft still receive finite backtracking.
                if any(original[left]['aircraft_id']==original[right]['aircraft_id'] and
                       original[left]['departure_time_s']>original[right]['departure_time_s']
                       for i,left in enumerate(order) for right in order[i+1:]):
                    continue
                if orders_used>=config['max_orders'] or orders_used-mode_order_start>=mode_order_limit:
                    diagnostics.append({'status':'cooperative_budget_exhausted','failure_class':'search_budget_exhausted','budget_dimension':'max_orders'});break
                if time.monotonic()>=mode_deadline:
                    diagnostics.append({'status':'cooperative_timeout','failure_class':'timeout'});break
                orders_used+=1;planned=[];searches=[];failed=False
                # Only repaired prefixes are reserved during search. The complete
                # joint check below detects newly affected locked tasks and grows
                # the repair set before this proposal may be accepted.
                for tid in order:
                    task=original[tid]
                    if execution_context is not None and execution_context['tasks'][tid]['status']=='completed':
                        planned.append(copy.deepcopy(task));continue
                    predecessors=[t for t in planned if t['aircraft_id']==task['aircraft_id'] and original[t['id']]['departure_time_s']<task['departure_time_s']]
                    predecessor=max(predecessors,key=lambda t:original[t['id']]['departure_time_s']) if predecessors else None
                    source=suffix_task(task,execution_context) if execution_context and execution_context['tasks'][tid]['status']=='in_progress' else prepare_continuation(task,predecessor,dict(world,_candidate_tasks=planned))
                    options=_ground_departures(source,constraints) if mode=='delay' else [source['departure_time_s']]
                    attempts=[];found=[]
                    for departure in options:
                        if calls>=config['max_searches'] or calls-mode_call_start>=mode_call_limit:
                            attempts.append({'status':'search_budget_exhausted','budget_dimension':'max_searches'});break
                        if time.monotonic()>=mode_deadline:
                            attempts.append({'status':'search_timeout'});break
                        minimum=sum(s['duration_s'] for s in source['mandatory_services'])
                        if minimum+source['departure_time_s']>source['deadline_s']:
                            attempts.append({'status':'proven_infeasible_necessary_condition','proof':'mandatory_service_duration_exceeds_available_time'});break
                        calls+=1
                        search_tick=time.monotonic()
                        result=search_task(source,world,constraints,planned,allow_detour=mode=='detour',
                            start_after_s=departure,max_expansions=config['max_expansions'],timeout_s=max(0.,mode_deadline-time.monotonic()))
                        search_seconds+=time.monotonic()-search_tick
                        attempts.append({k:v for k,v in result.items() if k!='task'})
                        if result['status']=='found':found.append(result)
                    diagnostics.append({'mode':mode,'task_id':tid,'priority_order':list(order),'attempts':attempts,'order_attempt':orders_used})
                    if not found:
                        failed=True
                        diagnostics.append({'status':'priority_order_backtracked','failed_task_id':tid,'priority_order':list(order),
                            'failure_class':('timeout' if any(a['status']=='search_timeout' for a in attempts) else
                              'search_budget_exhausted' if any(a['status']=='search_budget_exhausted' for a in attempts) else
                              'proven_infeasible_necessary_condition' if any(a['status']=='proven_infeasible_necessary_condition' for a in attempts) else 'bounded_search_no_result'),
                            'global_infeasibility_proven':False})
                        break
                    result=min(found,key=lambda r:(r['task']['planning_energy_used'],r['task']['trajectory'][-1]['time_s']))
                    planned.append(restore_full_task(task,result['task'],execution_context) if execution_context else result['task'])
                    searches.append({k:v for k,v in result.items() if k!='task'} | {'task_id':tid})
                if failed:continue
                complete=planned+[copy.deepcopy(t) for t in world['tasks'] if t['id'] not in repair]
                new_joint=check_joint_tasks(complete,world,world['as_of_s'] if execution_context else None)
                added={tid for c in _future_conflicts(new_joint,at) for tid in c.get('task_ids',[]) if tid not in repair}
                if added:
                    reason={'mode':mode,'added_task_ids':sorted(added),'previous_repair_task_ids':sorted(repair),
                        'reason':'new_route_or_resource_interactions','conflicts':_future_conflicts(new_joint,at)}
                    expansion_history.append(reason);diagnostics.append({'status':'repair_set_expanded',**reason})
                    repair.update(added);restart=True;break
                candidate=_candidate(world,constraints,complete,mode,searches,repair,expansion_history,execution_context,len(candidates))
                candidate['search_priority_order']=list(order)
                if ranking_info is not None:
                    candidate['ranking_source']='configured_learning_proposal_order';candidate['proposal_ranking']=ranking_info
                review_tick=time.monotonic()
                review=verify_candidate(candidate,world,constraints)
                review_seconds+=time.monotonic()-review_tick
                candidate['review']=review;candidate['status']=review['status']
                if review['feasible']:
                    if first_feasible_seconds is None:first_feasible_seconds=time.monotonic()-start
                    candidate_found_seconds[candidate['id']]=time.monotonic()-start
                    candidates.append(candidate)
                    diagnostics.append({'status':'cooperative_order_succeeded','mode':mode,'priority_order':list(order),'order_attempt':orders_used})
                    break
                diagnostics.append({'status':'priority_order_backtracked','mode':mode,'priority_order':list(order),
                    'failure_class':'independent_review_rejected','violations':review['violations'],'unchecked':review['unchecked'],
                    'global_infeasibility_proven':False})
            if not restart:break
    candidates.sort(key=lambda c:tuple(c['stability_cost']['values']))
    for index,candidate in enumerate(candidates):
        candidate['recommended']=index==0
        candidate['selection_reason']=('Hard constraints passed; minimum lexicographic stability cost among generated and checked candidates.'
            if index==0 else 'Hard constraints passed; larger lexicographic stability cost than the recommended generated candidate.')
    diagnostics.append({'status':'bounded_cooperative_search_summary','budget':config,'search_calls':calls,'order_attempts':orders_used,
        'feasible_candidates':len(candidates),'global_optimum_proven':False,'search_complete':False,
        'first_feasible_seconds':first_feasible_seconds,'candidate_found_seconds':candidate_found_seconds,'search_seconds':search_seconds,'independent_review_seconds':review_seconds,
        'elapsed_seconds':time.monotonic()-start,'action_order':[p['action'] for p in proposals]})
    return candidates
