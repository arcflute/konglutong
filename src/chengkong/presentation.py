"""Versioned read-only presentation and export; no simulator future disclosure."""
from __future__ import annotations
import copy
import math


def spatial_view(world,time_s=None):
    at=world['as_of_s'] if time_s is None else time_s
    if isinstance(at,bool) or not isinstance(at,(int,float)) or not math.isfinite(at):
        raise ValueError('view_time_must_be_finite_seconds')
    if not world['horizon_s'][0]<=at<=world['horizon_s'][1]:raise ValueError('view_time_outside_horizon')
    from .world import generate_constraints
    from .motion import motion_snapshot,geometry_at
    from .execution import frozen_prefix
    objects=[];building_ids={b['id'] for b in world['buildings']}
    # The constraint history, not today's object geometry/installed flag, decides
    # what occupied this effective time under the currently available knowledge.
    for c in generate_constraints(world):
        if not c['interval_s'][0]<=at<c['interval_s'][1]:
            continue
        # Physical moving components are emitted by motion_snapshot below.
        # Moving activity protection is a separate versioned constraint layer.
        if 'motion' in c and c['kind']!='activity':continue
        geometry=(geometry_at(c['motion'],at) if c['motion'].get('motion_status')=='supported' else None) if 'motion' in c else c['geometry']
        component=('building' if c['object_id'] in building_ids else
                   'activity_protection' if c['kind']=='activity' else 'physical_'+c['id'].split(':')[2])
        objects.append({'id':c['object_id'],'component':component,'constraint_id':c['id'],
            'geometry':copy.deepcopy(geometry),'geometry_status':c['geometry_status'],
            'protection_semantics':c.get('protection_semantics'),
            'source_ids':list(c['source_ids']),'interval_s':list(c['interval_s']),
            'environment_version':c['environment_version'],
            'data_kind':'conservative_envelope' if c['kind']=='activity' else world.get('data_kind','synthetic')})
    # Motion unknown/null remains visible as unknown instead of falling back to
    # a legacy static pose. Actual motion uses the same versioned backend rules.
    snapshot=motion_snapshot(world,at)
    return {'environment_version':world['environment_version'],'reservation_version':world.get('reservation_version',1),
            'state_revision':world.get('state_revision',1),'known_as_of_s':world['as_of_s'],'view_time_s':at,
            'temporal_status':'prediction_from_current_known_rules' if at>world['as_of_s'] else 'reconstructed_from_current_knowledge' if at<world['as_of_s'] else 'current_known_state',
            'objects':objects,'motion_snapshot':snapshot,'bounds_m':world['bounds_m'],
            'executed_prefixes':{t['id']:frozen_prefix(t,world['as_of_s']) for t in world['tasks']},
            'coordinate_system':world['coordinate_system'],'data_kind':world.get('data_kind','synthetic'),
            'note':'Preview changes neither information arrival nor the live clock; use history for what was known at that time.'}


def event_timeline(world,history=()):
    rows=[]
    for event in world.get('event_log',[]):
        for field,kind in [('observed_at_s','information_arrival'),('effective_at_s','event_effective')]:
            if field in event:rows.append({'id':event['id']+':'+kind,'event_id':event['id'],'kind':kind,
                'time_s':event[field],'event_kind':event.get('event',event)['kind'],'source':event.get('event',event).get('source_kind'),
                'pending':event['effective_at_s']>world['as_of_s']})
    for activity in world['activities']:
        rows.append({'id':activity['id']+':planned','object_id':activity['id'],'kind':'planned_end',
                     'time_s':activity['planned_interval_s'][1],'is_confirmation':False})
        if activity.get('completion_confirmed_at_s') is not None:
            rows.append({'id':activity['id']+':confirmed','object_id':activity['id'],'kind':'confirmed_completion',
                         'time_s':activity['completion_confirmed_at_s'],'is_confirmation':True})
    for item in history:
        if item['kind']=='adopted':
            rows.append({'id':f"adopted:{item['id']}",'kind':'plan_adopted',
                'time_s':item['after']['as_of_s'],'candidate_id':item.get('candidate_id'),
                'reservation_version':item['after'].get('reservation_version')})
    return sorted(rows,key=lambda r:(r['time_s'],r['id']))


def export_result(result,history,*,material_audit=(),field_audit=()):
    w=result['world']
    return {'schema_version':'chengkong-export-v3','data_kind':w.get('data_kind','synthetic'),
        'is_real_case':False,'scenario_id':w['scenario_id'],'as_of_s':w['as_of_s'],
        'versions':{k:w.get(k) for k in ('environment_version','reservation_version','state_revision','rule_version','model_version')},
        'plan_versions':{t['id']:t['plan_version'] for t in w['tasks']},
        'visible_world':copy.deepcopy(w),'events':copy.deepcopy(w.get('event_log',[])),
        'timeline':event_timeline(w,history),'candidates':copy.deepcopy(result['candidates']),
        'baseline_analysis':copy.deepcopy(result['baseline_analysis']),
        'baseline_full_review':copy.deepcopy(result['baseline_full_review']),
        'unresolved_information':copy.deepcopy(w.get('information_gaps',[])),
        'evidence_requests':copy.deepcopy(result.get('evidence_requests',[])),
        'material_audit':copy.deepcopy(list(material_audit)),'field_audit':copy.deepcopy(list(field_audit)),
        'learning_status':copy.deepcopy(result['learning_status']),'learning_advice':copy.deepcopy(result.get('learning_advice',{})),
        'learning_applicability':copy.deepcopy(result.get('learning_applicability')),
        'planning_policy':copy.deepcopy(w.get('planning_policy',{'mode':'conservative'})),
        'maintenance_explanations':copy.deepcopy(result.get('maintenance_explanations')),
        'failure_diagnosis':copy.deepcopy(result.get('failure_diagnosis')),
        'search_diagnostics':copy.deepcopy(result.get('search_diagnostics')),
        'timings':copy.deepcopy(result.get('timings')),
        'conditions':copy.deepcopy(result['limits']),'history':copy.deepcopy(list(history))}
