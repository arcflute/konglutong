"""Field-scoped, source-traceable evidence; no answers are inferred from absence.

The finite supported field list is intentional. Updates are construction events
and remain proposals until a caller explicitly accepts them through the importer.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from .contracts import ContractError, UnsupportedData, number, stable_id, vector, parse_interval, validate_geometry

SUPPORTED_FIELDS = ('geometry.position', 'facility.height', 'activity.range', 'planned_interval', 'completion', 'parking_pose')
ALIASES = {'completion_status': 'completion'}


def _object(world, identifier):
    for key in ('facilities', 'activities', 'buildings'):
        for obj in world[key]:
            if obj['id'] == identifier:
                return key, obj
    raise ContractError('evidence_object_not_found')


def field_value(world, object_id, field):
    kind, obj = _object(world, object_id)
    if field == 'completion':
        return {'status': obj.get('completion_status'), 'confirmed_at_s': obj.get('completion_confirmed_at_s')}
    if field == 'planned_interval': return deepcopy(obj.get('planned_interval_s'))
    if field == 'activity.range': return deepcopy(obj.get('activity_envelope'))
    if field == 'parking_pose': return deepcopy(obj.get('motion_model', {}).get('parking_pose', obj.get('pose')))
    if field == 'facility.height': return deepcopy(obj.get('physical_geometry'))
    if field == 'geometry.position': return deepcopy(obj.get('physical_geometry', obj.get('geometry')))
    raise UnsupportedData('unsupported_field:' + str(field))


def _translate(geometry, delta):
    result = deepcopy(geometry)
    if validate_geometry(result) != 'supported': raise UnsupportedData('position_requires_supported_known_geometry')
    if result['type'] == 'aabb':
        for key in ('min_m', 'max_m'): result[key] = [a+b for a,b in zip(result[key], delta)]
    elif result['type'] == 'capsule':
        for key in ('start_m', 'end_m'): result[key] = [a+b for a,b in zip(result[key], delta)]
    else:
        result['center_xy_m'] = [a+b for a,b in zip(result['center_xy_m'], delta[:2])]
        result['z_min_m'] += delta[2]; result['z_max_m'] += delta[2]
    return result


def event_for_field(world, record):
    """Validate explicit values; never estimate geometric fields from a picture."""
    payload = record['payload']; field = ALIASES.get(payload.get('field'), payload.get('field'))
    if field not in SUPPORTED_FIELDS: raise UnsupportedData('unsupported_field:' + str(field))
    object_id = stable_id(payload.get('object_id'), 'object_id'); kind, obj = _object(world, object_id)
    value = payload.get('value')
    if value is None: raise ContractError('missing_field_value_preserves_unknown')
    event = {'id': 'import:' + record['id'], 'observed_at_s': record['observed_at_s'],
             'effective_at_s': record['effective_at_s'], 'source_kind': 'reviewed_structured_record',
             'evidence_refs': [{'record_id': record['id'], 'source_id': record['source_id'],
                                'locator': record.get('locator', 'payload.value')}],
             'field_evidence': deepcopy(record)}
    if field in ('activity.range', 'planned_interval', 'completion'):
        if kind != 'activities': raise UnsupportedData('field_requires_activity')
        event['activity_id'] = object_id
        if field == 'activity.range':
            if validate_geometry(value) != 'supported': raise UnsupportedData('unsupported_activity_geometry')
            event.update(kind='activity_envelope_changed', activity_envelope=deepcopy(value))
        elif field == 'planned_interval':
            parse_interval(value)
            if value[0] != obj['planned_interval_s'][0]: raise UnsupportedData('changing_recorded_activity_start_requires_separate_correction')
            event.update(kind='planned_end_changed', new_planned_end_s=value[1])
        else:
            if not isinstance(value,dict) or value.get('status') != 'confirmed': raise UnsupportedData('completion_requires_explicit_confirmation_record')
            effective = number(value.get('confirmed_at_s'), 'confirmed_at_s', minimum=0)
            if effective != event['effective_at_s']: raise ContractError('completion_time_must_match_effective_time')
            event.update(kind='activity_completion_confirmed')
    else:
        if kind != 'facilities': raise UnsupportedData('field_requires_facility')
        event['facility_id'] = object_id
        if field == 'parking_pose' and obj.get('motion_model'):
            # Motion contract validator owns units/ranges and safe stopping semantics.
            model = deepcopy(obj['motion_model']); model['parking_pose'] = deepcopy(value)
            event.update(kind='facility_motion_changed', motion_model=model)
        else:
            if obj.get('motion_model'): raise UnsupportedData('motion_geometry_requires_complete_motion_model_update')
            geometry = deepcopy(obj['physical_geometry'])
            if field == 'geometry.position':
                if not isinstance(value,dict): raise ContractError('position_requires_explicit_from_and_to_anchors')
                vector(value.get('from_anchor_m'), 'from_anchor_m'); vector(value.get('to_anchor_m'), 'to_anchor_m')
                # Anchor is a declared surveyed correspondence; not inferred centroid.
                delta = [b-a for a,b in zip(value['from_anchor_m'], value['to_anchor_m'])]
                geometry = [_translate(g,delta) for g in geometry]
                event.update(kind='facility_geometry_changed', physical_geometry=geometry,
                             parameters={'position_anchor_from_m':value['from_anchor_m'], 'position_anchor_to_m':value['to_anchor_m']})
            elif field == 'facility.height':
                if not isinstance(value,dict): raise ContractError('height_requires_component_index_and_top_z_m')
                index = value.get('component_index')
                if isinstance(index,bool) or not isinstance(index,int) or not 0 <= index < len(geometry): raise ContractError('invalid_geometry_component_index')
                top = number(value.get('top_z_m'), 'top_z_m'); g = geometry[index]
                if g['type'] == 'vertical_cylinder': g['z_max_m'] = top
                elif g['type'] == 'aabb': g['max_m'][2] = top
                else: raise UnsupportedData('height_only_supports_cylinder_or_aabb_component')
                validate_geometry(g)
                event.update(kind='facility_geometry_changed', physical_geometry=geometry)
            elif field == 'parking_pose':
                if not isinstance(value,dict) or not isinstance(value.get('physical_geometry'),list): raise ContractError('static_parking_pose_requires_explicit_physical_geometry')
                for g in value['physical_geometry']:
                    if validate_geometry(g) != 'supported': raise UnsupportedData('unsupported_parking_geometry')
                event.update(kind='facility_pose_changed', physical_geometry=deepcopy(value['physical_geometry']), pose=deepcopy(value.get('pose')))
    return event


def make_field_request(world, object_id, field, *, reason='operator_requested_field_verification', max_queries=1, deadline_s=None):
    field = ALIASES.get(field, field)
    if field not in SUPPORTED_FIELDS: return {'status':'unsupported', 'field':field, 'supported_fields':list(SUPPORTED_FIELDS), 'answer':None}
    _object(world,object_id)
    if isinstance(max_queries,bool) or not isinstance(max_queries,int) or max_queries < 1: raise ContractError('invalid_query_budget')
    deadline = world['as_of_s']+60 if deadline_s is None else number(deadline_s,'deadline_s',minimum=world['as_of_s'])
    context = {key:world[key] for key in ('environment_version','scenario_id','rule_version','model_version')}
    digest=hashlib.sha256(json.dumps([context,object_id,field,reason],sort_keys=True).encode()).hexdigest()[:16]
    return {'id':'field-request-'+digest,'status':'pending','object_id':object_id,'field':field,'reason':reason,
            'max_queries':max_queries,'deadline_s':deadline,'source_layer':'field_validation','answer':None,
            'supported_fields':list(SUPPORTED_FIELDS), **context}


def query_field_evidence(world, request, records=None, *, queries_used=0, timeout_s=5, simulated_delay_s=0, source_kind='available_records'):
    """Return actual records, including conflicts. Acceptance remains separate."""
    result={'request_id':request.get('id'),'status':'unknown','answer':None,'records':[], 'world_updated':False,
            'queries_used':queries_used,'field':request.get('field'),'object_id':request.get('object_id')}
    if isinstance(queries_used,bool) or not isinstance(queries_used,int) or queries_used<0:raise ContractError('invalid_query_usage')
    if request.get('field') not in SUPPORTED_FIELDS: return {**result,'status':'unsupported'}
    if any(request.get(k) != world[k] for k in ('environment_version','scenario_id','rule_version','model_version')): return {**result,'status':'stale_request'}
    if queries_used >= request['max_queries']: return {**result,'status':'budget_exhausted'}
    if world['as_of_s'] >= request['deadline_s']: return {**result,'status':'deadline_exceeded'}
    if source_kind not in ('available_records','synthetic_evidence_source'): raise ContractError('unsupported_evidence_source_kind')
    delay=number(simulated_delay_s,'simulated_delay_s',minimum=0); timeout=number(timeout_s,'timeout_s',minimum=0)
    if delay and source_kind != 'synthetic_evidence_source': raise ContractError('simulated_delay_requires_synthetic_source')
    result['queries_used']+=1
    if delay > timeout or world['as_of_s']+delay >= request['deadline_s']: return {**result,'status':'timeout','unknown_preserved':True}
    if records is not None:
        rows=records
    else:
        rows=list(world.get('field_evidence',[]));seen={r['id'] for r in rows}
        rows.extend(row for row in world.get('imported_records',[]) if row['id'] not in seen)
    selected=[]; expired=[]
    for row in rows:
        if not isinstance(row,dict):return {**result,'status':'invalid_evidence','reason':'evidence_record_must_be_object'}
        if world.get('import_record_decisions',{}).get(row.get('id'),'accepted')!='accepted':continue
        if row.get('acceptance_status','accepted')!='accepted':continue
        if row.get('record_type')=='event':
            from .imports import _assertion
            key,value=_assertion(world,row)
            if key:
                oid,field=key.split('::',1)
                row={**deepcopy(row),'record_type':'field_evidence','original_record_type':'event',
                     'payload':{'object_id':oid,'field':field,'value':deepcopy(value)}}
        payload=row.get('payload',{})
        if row.get('record_type')!='field_evidence' or payload.get('object_id')!=request['object_id'] or ALIASES.get(payload.get('field'),payload.get('field')) != request['field']: continue
        if row.get('observed_at_s',float('inf')) > world['as_of_s'] or row.get('effective_at_s',float('inf')) > world['as_of_s']: continue
        if row.get('valid_until_s') is not None and row['valid_until_s'] <= world['as_of_s']:
            expired.append(deepcopy(row)); continue
        if payload.get('value') is not None:
            try:
                stable_id(row.get('id'),'evidence_record.id');stable_id(row.get('source_id'),'evidence_record.source_id')
                number(row.get('observed_at_s'),'evidence_observed_at_s',minimum=0)
                number(row.get('effective_at_s'),'evidence_effective_at_s',minimum=0)
                event_for_field(world,row)
            except (TypeError,ValueError,KeyError) as exc:
                return {**result,'status':getattr(exc,'status','invalid_evidence'),'reason':str(exc)}
            selected.append(deepcopy(row))
    result['records']=selected; result['expired_records']=expired
    if not selected:return {**result,'status':'expired' if expired else 'unknown'}
    signatures={json.dumps(row['payload']['value'],sort_keys=True,allow_nan=False) for row in selected}
    if len(signatures)>1:return {**result,'status':'conflicting_evidence','reason':'Explicit reconciliation required; upload order is not authority.'}
    return {**result,'status':'evidence_available','answer':deepcopy(selected[0]['payload']['value']),
            'requires_explicit_import_acceptance':True}
