"""Evidence-linked maintenance explanations; no new feasibility authority."""
from __future__ import annotations
from copy import deepcopy
import math


def movement_segments(task):
    rows=[]
    for i,(a,b) in enumerate(zip(task['trajectory'],task['trajectory'][1:])):
        stationary=a['position_m']==b['position_m']
        services=[s['id'] for s in task['mandatory_services'] if stationary and
                  math.dist(s['position_m'],a['position_m'])<=1e-6 and
                  a['time_s']<s['window_s'][1] and s['window_s'][0]<b['time_s']]
        rows.append({'segment_index':i,'interval_s':[a['time_s'],b['time_s']],
            'from_m':list(a['position_m']),'to_m':list(b['position_m']),
            'kind':'service_or_wait' if services else 'ground_wait' if stationary and a['position_m'][2]==0 else 'air_wait' if stationary else 'flight',
            'service_ids':services,'duration_s':b['time_s']-a['time_s']})
    return rows


def constraint_semantics(c):
    motion=c.get('motion')
    if motion:
        error=motion.get('model',{}).get('uncertainty_bounds')
        mode='time_varying_with_declared_bounds' if error is not None else 'full_envelope_without_declared_error_bounds'
        return {'constraint_id':c['id'],'object_id':c['object_id'],'kind':c['kind'],'interval_s':c['interval_s'],
                'semantics':mode,'component':motion.get('component'),'source_ids':c.get('source_ids',[]),
                'uncertainty_bounds':deepcopy(error),'motion_status':motion.get('motion_status'),
                'rule':'Physical body and explicitly moving protection; no permission inferred for a separate continuous prohibition.'}
    return {'constraint_id':c['id'],'object_id':c['object_id'],'kind':c['kind'],'interval_s':c['interval_s'],
            'semantics':'continuous_activity_prohibition' if c['kind']=='activity' else 'persistent_physical_occupancy',
            'source_ids':c.get('source_ids',[]),'rule':'Active throughout its declared interval; motion gaps do not remove this constraint.'}


def maintenance_explanations(result):
    world=result['world'];original={t['id']:t for t in world['tasks']};rows=[]
    for candidate in result['candidates']:
        actions=[]
        for task in candidate['tasks']:
            old=original[task['id']];segments=movement_segments(task)
            change=next(c for c in candidate['changes'] if c['task_id']==task['id'])
            waits=[r for r in segments if r['kind']!='flight']
            action='keep' if not change['changed'] else 'detour' if change['distance_delta_m']>1e-5 else 'wait_or_retime'
            trigger=result['baseline_analysis'][task['id']]
            search=next((s for s in candidate.get('searches',[]) if s.get('task_id')==task['id']),{})
            actions.append({'task_id':task['id'],'action':action,'changed':change['changed'],'changes':deepcopy(change),
                'trigger_constraints':sorted({c['constraint_id'] for c in trigger['conflicts']}),
                'trigger_task_violations':[v for v in result['baseline_full_review'].get('violations',[]) if v.get('task_id')==task['id']],
                'mandatory_services':deepcopy(old['mandatory_services']),'deadline_s':old['deadline_s'],
                'wait_and_service_intervals':waits,'flight_intervals':[s for s in segments if s['kind']=='flight'],
                'planning_mode':search.get('planning_mode',world.get('planning_policy',{}).get('mode','conservative')),
                'temporal_windows':deepcopy(search.get('temporal_windows')),
                'planning_explanation':deepcopy(search.get('planning_explanation')),
                'review_status':candidate['review']['status'],'requirements_preserved':candidate['review']['feasible']})
        rows.append({'candidate_id':candidate['id'],'recommended':candidate.get('recommended',False),
            'selection_reason':candidate.get('selection_reason'),'actual_stability_cost':deepcopy(candidate['stability_cost']),
            'actions':actions,'conditions':deepcopy(candidate['conditions']),
            'authority':'explanation only; explicit adoption and version recheck remain mandatory'})
    reports=list(result['baseline_analysis'].values())
    return {'policy':deepcopy(world.get('planning_policy',{'mode':'conservative'})),
        'constraint_semantics':[constraint_semantics(c) for c in result['constraints']],
        'candidates':rows,'baseline_completion':{'task_reports':len(reports),'complete_task_reports':sum(r['checks_completed'] for r in reports),
            'unchecked_items':sum(len(r['unchecked']) for r in reports),
            'task_report_unchecked_fraction':sum(not r['checks_completed'] for r in reports)/len(reports) if reports else 0,
            'scope':'initial task-object reports; task requirements and joint resource review are separately mandatory'},
        'no_candidate_action':None if rows else {'action':'defer_decision','reason':'No checked candidate returned under current input and budget; no global infeasibility proof.'}}


def operation_timeline(result,candidate_id=None):
    world=result['world'];candidate=next((c for c in result['candidates'] if c['id']==candidate_id),None)
    tracks=[]
    for c in result['constraints']:
        motion=c.get('motion')
        if motion:
            key=c['object_id']+':'+str(motion.get('component'))
            tracks.append({'id':key,'label':key,'kind':'motion_validity','interval_s':c['interval_s'],
                'semantics':constraint_semantics(c)['semantics'],'source_ids':c.get('source_ids',[]),
                'motion_program':deepcopy(motion['model'].get('program',[])),'commands':deepcopy(motion.get('commands',[]))})
        else:
            tracks.append({'id':c['id'],'label':c['id'],'kind':'continuous_prohibition','interval_s':c['interval_s'],'semantics':constraint_semantics(c)['semantics']})
    for prefix,tasks in [('current',world['tasks']),('candidate',candidate['tasks'] if candidate else [])]:
        for task in tasks:
            for row in movement_segments(task):tracks.append({'id':f'{prefix}:{task["id"]}:{row["segment_index"]}',
                'label':task['id'],'task_id':task['id'],'plan':prefix,**row})
    return {'known_as_of_s':world['as_of_s'],'horizon_s':world['horizon_s'],'environment_version':world['environment_version'],
        'candidate_id':candidate_id,'tracks':tracks,'simulation_only':True}
