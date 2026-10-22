"""Read-only round-three failure audit; searches never prove global infeasibility."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import argparse,json,math,time
from pathlib import Path
from chengkong.analysis import check_task,check_joint_tasks
from chengkong.world import generate_constraints
from chengkong.planning import generate_candidates
from chengkong.execution import replan_inflight
from chengkong.verification import _task_requirements

CATEGORIES=['input_information_insufficient','task_requirements_or_resources','candidate_graph_action_window_coverage',
 'priority_reservation_backtracking_limit','timeout','search_budget_exhausted','planner_review_semantics',
 'implementation_error','undetermined']

def classify(diagnostics,reports,joint):
    categories=[];evidence=[]
    for tid,r in reports.items():
        if r.get('unchecked'):categories.append(CATEGORIES[0]);evidence.append({'task_id':tid,'unchecked':r['unchecked']})
    if joint.get('unchecked'):categories.append(CATEGORIES[0]);evidence.append({'joint_unchecked':joint['unchecked']})
    for d in diagnostics:
        state=d.get('status','');failure=d.get('failure_class','')
        if 'timeout' in state or failure=='timeout':categories.append('timeout')
        if 'budget_exhausted' in state or failure=='search_budget_exhausted':categories.append('search_budget_exhausted')
        if d.get('budget_dimension')=='max_orders':categories.append('priority_reservation_backtracking_limit')
        if failure=='independent_review_rejected':categories.append('planner_review_semantics');evidence.append(d)
        for a in d.get('attempts',[]):
            s=a.get('status','')
            if 'timeout' in s:categories.append('timeout')
            elif 'budget_exhausted' in s:categories.append('search_budget_exhausted')
            elif 'unsupported' in s:categories.append('candidate_graph_action_window_coverage')
            elif s not in ('found',):categories.append('candidate_graph_action_window_coverage')
    if joint.get('conflicts'):categories.append('task_requirements_or_resources');evidence.append({'joint_conflicts':joint['conflicts']})
    return {'categories':sorted(set(categories)) or ['undetermined'],'evidence':evidence,'global_infeasibility_proven':False,
            'category_semantics':'Potential/observed limiting mechanisms; graph no-result is not a proof that a feasible plan exists or does not exist.'}


def summarize_failure(world,constraints,diagnostics,candidates=(),execution_context=None):
    """Traceable mechanisms and check coverage; no no-path -> infeasible shortcut."""
    at=world['as_of_s'] if execution_context else None
    reports={t['id']:check_task(t,constraints,world,at) for t in world['tasks']}
    joint=check_joint_tasks(world['tasks'],world,at)
    report=classify(diagnostics,reports,joint)
    task_requirements=[]
    for task in world['tasks']:
        violations,unchecked,metrics=_task_requirements(task,task,dict(world,_candidate_tasks=world['tasks']),execution_context)
        task_requirements.append({'task_id':task['id'],'violations':violations,'unchecked':unchecked,
            'object_status':reports[task['id']]['status'],'metrics':metrics})
        if violations and 'task_requirements_or_resources' not in report['categories']:
            report['categories'].append('task_requirements_or_resources')
        if unchecked and 'input_information_insufficient' not in report['categories']:
            report['categories'].append('input_information_insufficient')
    attempted=sum(len(d.get('attempts',[])) for d in diagnostics)
    total=len(world['tasks'])*len(constraints)
    unknown=sum(len({u.get('constraint_id') for u in r['unchecked'] if u.get('constraint_id')}) for r in reports.values())
    unscoped=[tid for tid,r in reports.items() if any(not u.get('constraint_id') for u in r['unchecked'])]
    if unscoped:
        # An unscoped task-level failure cannot support a precise pair fraction.
        unknown_fraction=None
    else:unknown_fraction=unknown/max(1,total)
    report.update(status='joint_candidate_available' if candidates else 'no_verified_joint_candidate',
        task_requirements=task_requirements,required_task_constraint_pairs=total,
        unresolved_task_constraint_pairs=unknown,unchecked_pair_fraction=unknown_fraction,
        unscoped_unchecked_task_ids=unscoped,unchecked_task_count=sum(bool(r['unchecked']) for r in reports.values()),
        joint_unchecked_count=len(joint.get('unchecked',[])),
        geometry_check_scope='All current original task-object pairs attempted; unresolved geometry is not a completed safety determination; candidate review is separate.',
        solver_attempts=attempted,diagnostic_status_counts=dict(Counter(d.get('status','task_attempt') for d in diagnostics)),
        known_feasible_candidate_count=len(candidates),notes=['Multiple mechanisms may coexist.',
            'Task occurrences in failed joint decisions are not individually proven infeasible.',
            'No claim of global optimality, exhaustive route topology or all priority permutations.'])
    if candidates:report['categories']=[]
    return report

def audit(output,high_budget=True):
    root=Path('/root/autodl-tmp/chengkong-models/round3');data=Path('/root/autodl-tmp/chengkong-data/round3')
    old=json.loads((root/'evaluation.json').read_text())['joint_online'];original=[r for r in old['records'] if r['strategy']=='rule']
    all_events={e['event_id']:e for e in map(json.loads,(data/'visible_scenarios.jsonl').read_text().splitlines())}
    records=[]
    for oldrow in original:
        e=all_events[oldrow['event_id']];w=deepcopy(e['world']);constraints=generate_constraints(w)
        inprogress=oldrow['inflight'];reports={t['id']:check_task(t,constraints,w,w['as_of_s'] if inprogress else None) for t in w['tasks']}
        joint=check_joint_tasks(w['tasks'],w,w['as_of_s'] if inprogress else None);diag=[];tick=time.perf_counter()
        cs=(replan_inflight if inprogress else generate_candidates)(w,constraints,diag,budget=old['budget'])
        taskrows=[]
        for t in w['tasks']:
            violations,unchecked,metrics=_task_requirements(t,t,w)
            taskrows.append({'task_id':t['id'],'object_check':reports[t['id']], 'original_requirements_violations':violations,
                'original_requirements_unchecked':unchecked,'joint_related':[x for x in joint['conflicts']+joint['unchecked'] if t['id'] in x.get('task_ids',[])],
                'search_attempts':[d for d in diag if d.get('task_id')==t['id'] or d.get('failed_task_id')==t['id']]})
        row={'event_id':e['event_id'],'template':e['template_family'],'required_tasks':len(w['tasks']),
             'old_event_feasible':oldrow['feasible'],'reproduced_event_feasible':bool(cs),'seconds':time.perf_counter()-tick,
             'diagnostics':diag,'tasks':taskrows,'failure':classify(diag,reports,joint) if not cs else None}
        if high_budget and not cs and all(r['checks_completed'] for r in reports.values()):
            b={'max_orders':96,'max_searches':512,'max_expansions':20000,'timeout_s':8.0};dd=[];tick=time.perf_counter()
            cc=(replan_inflight if inprogress else generate_candidates)(w,constraints,dd,budget=b)
            row['high_budget_reference']={'budget':b,'known_feasible':bool(cc),'seconds':time.perf_counter()-tick,'diagnostics':dd,
                 'selected':cc[0] if cc else None,'global_infeasibility_proven':False,'optimality_proven':False,
                 'visible_input_unchanged':True}
        records.append(row)
    report={'schema':'round4-baseline-diagnostic-v1','definitions':{'events':12,'independent_templates':6,'task_occurrences':44,
        'old_success_count':5,'old_covered_task_occurrences':14,'old_tasks_completed_meaning':'All tasks in an event with a fully reviewed joint plan; zero credit on a failed event. Not a task-by-task execution count.',
        'remaining_30_meaning':'Task occurrences in seven events with no accepted complete joint candidate; not 30 proven individually infeasible tasks.'},
        'records':records,'failure_event_categories':dict(Counter(c for r in records if r['failure'] for c in r['failure']['categories']))}
    Path(output).parent.mkdir(parents=True,exist_ok=True);Path(output).write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({'output':str(output),'reproduced_success':sum(r['reproduced_event_feasible'] for r in records),'categories':report['failure_event_categories'],
        'references':[(r['event_id'],r['high_budget_reference']['known_feasible']) for r in records if 'high_budget_reference' in r]},ensure_ascii=False))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='/root/autodl-tmp/chengkong-data/round4/round3-failure-audit.json');p.add_argument('--no-reference',action='store_true');a=p.parse_args();audit(a.output,not a.no_reference)
