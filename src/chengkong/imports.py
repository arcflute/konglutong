"""Reviewable structured material import with explicit coordinates and provenance.

No parser updates plans implicitly. Construction records cross ``apply_event``;
new static objects/tasks use append-only validated import. Existing task changes
remain the candidate-adoption workflow, never this material endpoint.
"""
from __future__ import annotations
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
from .contracts import (ContractError, UnsupportedData, stable_id, number, vector,
                        validate_world, validate_geometry, validate_event)
from .world import apply_event, generate_constraints
from .field_evidence import SUPPORTED_FIELDS, event_for_field, field_value

SCHEMA = 'chengkong-material-v1'
_TYPES = ('facility','building','pad','task','resources','event','field_evidence')


def _json(text):
    return json.loads(text,parse_constant=lambda value: (_ for _ in ()).throw(ContractError('nonfinite_material_json')))


def _versions(world):
    return {**{key:world[key] for key in ('environment_version','scenario_id','rule_version','model_version')},
            'reservation_version':world.get('reservation_version',1),
            'plan_versions':{task['id']:task['plan_version'] for task in world['tasks']}}


def _check_metadata(world, metadata):
    if metadata.get('schema_version') != SCHEMA: raise UnsupportedData('unsupported_material_schema')
    if metadata.get('data_kind') not in ('synthetic','record_assertion','mixed_simulation'): raise ContractError('explicit_material_data_kind_required')
    if metadata.get('data_kind') != 'record_assertion' and metadata.get('is_real_case') is not False: raise ContractError('synthetic_or_mixed_material_is_not_real_case')
    coords=metadata.get('coordinate_system',{})
    for key in ('type','unit','axes','origin','height_datum'):
        if coords.get(key) != world['coordinate_system'].get(key): raise UnsupportedData('coordinate_reference_mismatch:'+key)
    if coords.get('unit')!='m': raise UnsupportedData('only_explicit_metres_supported')
    time=metadata.get('time_system',{})
    for key in ('type','origin_label','interval_convention'):
        if time.get(key) != world['time_system'].get(key): raise UnsupportedData('time_reference_mismatch:'+key)
    sources=metadata.get('sources')
    if not isinstance(sources,list) or not sources: raise ContractError('material_sources_required')
    seen=set()
    for source in sources:
        sid=stable_id(source.get('id'),'source.id')
        if sid in seen: raise ContractError('duplicate_source_id')
        seen.add(sid)
        if not all(isinstance(source.get(key),str) and source[key] for key in ('kind','description','license')): raise ContractError('source_kind_description_and_license_required')
    return {s['id']:s for s in sources}


def _parse(content, format, metadata):
    if format == 'json':
        payload = _json(content) if isinstance(content,str) else deepcopy(content)
        if not isinstance(payload,dict): raise ContractError('material_must_be_object')
        return payload,payload.get('records')
    if not isinstance(metadata,dict): raise ContractError('csv_geojson_require_explicit_metadata')
    if format == 'csv':
        if not isinstance(content,str): raise ContractError('csv_content_must_be_text')
        rows=[]
        for index,row in enumerate(csv.DictReader(io.StringIO(content)),2):
            try:
                item={key:row[key] for key in ('id','record_type','source_id')}
                item.update(payload=_json(row['payload_json']), observed_at_s=float(row['observed_at_s']),
                            effective_at_s=float(row['effective_at_s']),locator=f'csv:line:{index}')
                if row.get('valid_until_s'):item['valid_until_s']=float(row['valid_until_s'])
                rows.append(item)
            except (KeyError,ValueError,TypeError) as exc:raise ContractError(f'csv_line_{index}:{exc}') from exc
        return deepcopy(metadata),rows
    if format == 'geojson':
        geo=_json(content) if isinstance(content,str) else deepcopy(content)
        if not isinstance(geo,dict) or geo.get('type')!='FeatureCollection': raise UnsupportedData('geojson_requires_feature_collection')
        # RFC7946 positions are WGS84, not local metres. A surveyed point mapping
        # is a declared correspondence, not an inferred geographic projection.
        transform=metadata.get('geographic_point_mapping')
        if not isinstance(transform,dict) or transform.get('input_reference')!='EPSG:4326' or not transform.get('source_id'):
            raise UnsupportedData('geojson_wgs84_requires_explicit_surveyed_point_mapping')
        if transform.get('input_height_unit')!='m' or transform.get('input_height_datum')!='WGS84_ellipsoid':
            raise UnsupportedData('geojson_requires_explicit_wgs84_ellipsoid_height_metres')
        if transform['source_id'] not in {s.get('id') for s in metadata.get('sources',[])}:raise ContractError('coordinate_mapping_requires_source_reference')
        correspondences=transform.get('points',[]); rows=[]
        for index,feature in enumerate(geo.get('features',[])):
            geometry=feature.get('geometry',{})
            if geometry.get('type')!='Point':raise UnsupportedData('geojson_only_surveyed_point_features_supported')
            geographic=geometry.get('coordinates');vector(geographic,'geographic_xyz')
            if not -180<=geographic[0]<=180 or not -90<=geographic[1]<=90:raise ContractError('invalid_wgs84_longitude_latitude')
            matches=[p for p in correspondences if p.get('geographic')==geographic]
            if len(matches)!=1:raise ContractError('geojson_point_requires_unique_surveyed_correspondence')
            local=matches[0].get('local_m');vector(local,'surveyed_local_m')
            row=deepcopy(feature.get('properties',{}));row.setdefault('id',feature.get('id'))
            row['locator']=f'geojson:feature:{index}';payload=row.get('payload',{})
            if row.get('record_type')=='pad':payload['position_m']=deepcopy(local)
            elif row.get('record_type')=='facility':
                from .field_evidence import _translate
                payload['physical_geometry']=[_translate(g,local) for g in payload.get('geometry_relative_to_point',[])]
                payload.pop('geometry_relative_to_point',None)
            else:raise UnsupportedData('geojson_supports_pad_and_facility_points_only')
            row['payload']=payload;row['coordinate_mapping_source_id']=transform['source_id'];rows.append(row)
        return deepcopy(metadata),rows
    raise UnsupportedData('unsupported_material_format:'+str(format))


def _validate_record(world,record,sources):
    if not isinstance(record,dict):raise ContractError('record_must_be_object')
    stable_id(record.get('id'),'record.id')
    if record.get('record_type') not in _TYPES:raise UnsupportedData('unsupported_record_type')
    if record.get('source_id') not in sources:raise ContractError('record_source_not_found')
    number(record.get('effective_at_s'),'effective_at_s',minimum=world['horizon_s'][0])
    observed=number(record.get('observed_at_s'),'observed_at_s',minimum=0)
    if observed>world['as_of_s']:raise ContractError('future_material_not_visible')
    if record.get('effective_at_s')>world['horizon_s'][1]:raise UnsupportedData('effective_time_outside_horizon')
    if record.get('valid_until_s') is not None:
        if number(record['valid_until_s'],'valid_until_s') <= record['effective_at_s']:raise ContractError('expiry_must_follow_effective_time')
        if record['valid_until_s']<=world['as_of_s']:raise ContractError('expired_evidence_requires_fresh_record')
    if not isinstance(record.get('payload'),dict):raise ContractError('record_payload_must_be_object')


def _event(record):
    event=deepcopy(record['payload'])
    for key in ('effective_at_s','observed_at_s'):
        if key in event and event[key]!=record[key]:raise ContractError('event_time_differs_from_material_record')
        event[key]=record[key]
    event.setdefault('id','import:'+record['id']);event['source_kind']='reviewed_structured_record'
    event['evidence_refs']=[{'record_id':record['id'],'source_id':record['source_id'],'locator':record.get('locator','payload')}]
    event['material_record']=deepcopy(record)
    return event


def _assertion(world,record):
    """Normalize supported native event assertions for source conflict review."""
    payload=record['payload']
    if record['record_type']=='field_evidence':
        return f"{payload.get('object_id')}::{payload.get('field')}",payload.get('value')
    if record['record_type']!='event':return None,None
    kind=payload.get('kind');oid=payload.get('facility_id',payload.get('activity_id'))
    if kind=='planned_end_changed':
        obj=next((a for a in world['activities'] if a['id']==oid),None)
        return f'{oid}::planned_interval',[obj['planned_interval_s'][0] if obj else None,payload.get('new_planned_end_s')]
    if kind=='activity_completion_confirmed':return f'{oid}::completion',{'status':'confirmed','confirmed_at_s':record['effective_at_s']}
    field,value_key={'facility_geometry_changed':('physical_geometry','physical_geometry'),
                     'facility_pose_changed':('physical_geometry','physical_geometry'),
                     'facility_motion_changed':('motion_model','motion_model'),
                     'activity_envelope_changed':('activity.range','activity_envelope')}.get(kind,(None,None))
    if field:return f'{oid}::{field}',payload.get(value_key)
    return None,None


def _apply_records(world, records, metadata):
    updated=deepcopy(world);task_additions=[];resource_additions=[];object_additions=[]
    sources={s['id']:s for s in metadata['sources']}
    for record in records:
        _validate_record(updated,record,sources)
        current_source=next((source for source in updated['known_sources'] if source['id']==record['source_id']),None)
        if current_source and any(current_source.get(key)!=sources[record['source_id']].get(key)
                                  for key in ('kind','description','license')):
            raise ContractError('source_id_reused_with_changed_provenance')
        kind=record['record_type'];payload=deepcopy(record['payload'])
        if kind in ('event','field_evidence','facility'):
            if kind=='event':event=_event(record)
            elif kind=='field_evidence':event=event_for_field(updated,record)
            else:
                event=_event({**record,'payload':{'kind':'facility_added','facility':payload}})
            validate_event(event);updated=apply_event(updated,event)
        else:
            # Static and task additions are effective now; backdating a building
            # or rewriting an in-progress task would require temporal geometry.
            if record['effective_at_s']!=world['as_of_s']:raise UnsupportedData('append_only_static_task_import_requires_effective_time_now')
            if kind=='resources':
                if set(payload)-{'waiting_areas','aircraft_turnaround_model'}:raise UnsupportedData('unsupported_resource_parameter')
                for area in payload.get('waiting_areas',[]):
                    stable_id(area.get('id'),'waiting_area.id');vector(area.get('position_m'),'waiting_area.position_m')
                    if isinstance(area.get('capacity'),bool) or not isinstance(area.get('capacity'),int) or area['capacity']<1:raise ContractError('waiting_area_capacity_required')
                    if area['id'] in {a['id'] for a in updated.get('waiting_areas',[])}:raise ContractError('existing_resource_cannot_be_overwritten')
                    updated.setdefault('waiting_areas',[]).append(area);resource_additions.append(area['id'])
                if 'aircraft_turnaround_model' in payload:
                    if updated.get('aircraft_turnaround_model') is not None:raise ContractError('existing_turnaround_model_cannot_be_overwritten')
                    model=payload['aircraft_turnaround_model']
                    for key in ('ground_transfer_speed_mps','ground_transfer_energy_per_m','turnaround_s','recharge_rate_per_s'):number(model.get(key),key,minimum=0)
                    if model['ground_transfer_speed_mps']<=0:raise ContractError('ground_transfer_speed_must_be_positive')
                    updated['aircraft_turnaround_model']=model;resource_additions.append('aircraft_turnaround_model')
            else:
                container={'building':'buildings','pad':'pads','task':'tasks'}[kind]
                identifier=stable_id(payload.get('id'),'entity.id')
                if any(identifier==item['id'] for key in ('buildings','facilities','activities','pads','tasks','known_sources') for item in updated[key]):raise ContractError('existing_entity_cannot_be_overwritten')
                if kind=='building':
                    if validate_geometry(payload.get('geometry'))!='supported':raise UnsupportedData('building_requires_supported_geometry')
                    payload['source_id']=record['source_id']
                    # Buildings were previously all-horizon static. A new building
                    # import is only a baseline declaration, not its construction.
                    payload['import_valid_from_s']=record['effective_at_s']
                if kind=='task':
                    if payload.get('departure_time_s',-1)<world['as_of_s']:raise ContractError('imported_task_cannot_start_in_executed_past')
                    task_additions.append(identifier)
                else:object_additions.append(identifier)
                updated[container].append(payload)
            updated['environment_version']+=1
        if record['source_id'] not in {s['id'] for s in updated['known_sources']}:
            source={**deepcopy(sources[record['source_id']]),'observed_at_s':record['observed_at_s'],
                    'effective_at_s':record['effective_at_s'],'material_record_id':record['id']}
            updated['known_sources'].append(source)
            if 'initial_state' in updated:updated['initial_state']['known_sources'].append(deepcopy(source))
        if kind=='field_evidence':updated.setdefault('field_evidence',[]).append({**deepcopy(record),'acceptance_status':'accepted'})
        updated.setdefault('imported_records',[]).append(deepcopy(record))
        validate_world(updated)
    if task_additions or resource_additions or any(row['record_type']=='pad' for row in records):
        updated['reservation_version']=world.get('reservation_version',1)+1
    if metadata['data_kind'] != 'synthetic' and world.get('data_kind') == 'synthetic':
        updated['data_kind']='mixed_simulation'
        updated['is_real_case']=False
    return updated,{'task_additions':task_additions,'resource_additions':resource_additions,'object_additions':object_additions}


class StructuredImporter:
    def __init__(self):self.proposals={};self.audit=[]

    def prepare(self,world,content,format='json',metadata=None):
        validate_world(world)
        result={'status':'invalid_input','world_updated':False,'format':format,'issues':[],'field_review':[],
                'supported_fields':list(SUPPORTED_FIELDS),'base_versions':_versions(world),'proposal_id':None}
        try:
            meta,records=_parse(content,format,metadata);sources=_check_metadata(world,meta)
            if not isinstance(records,list) or not records:raise ContractError('material_records_required')
            if len(records)>1000:raise ContractError('material_maximum_1000_records')
            seen=set();existing={row['id']:row for row in world.get('imported_records',[])}
            for record in records:
                _validate_record(world,record,sources)
                if record['id'] in seen:raise ContractError('duplicate_material_record_id')
                seen.add(record['id'])
                if record['id'] in existing:
                    if existing[record['id']]!=record:raise ContractError('material_record_id_reused_with_different_content')
                    raise ContractError('material_record_already_accepted')
            conflicts={}
            for record in records:
                key,value=_assertion(world,record)
                if key is None:continue
                payload=record['payload']
                # All visible, unexpired assertions for the same field compete;
                # newest arrival is never automatically a source of truth.
                competing=[row for row in [*world.get('imported_records',[]),*records] if
                           world.get('import_record_decisions',{}).get(row['id'],'accepted')=='accepted' and
                           _assertion(world,row)[0]==key and
                           (row.get('valid_until_s') is None or row['valid_until_s']>world['as_of_s'])]
                signatures={json.dumps(_assertion(world,row)[1],sort_keys=True) for row in competing}
                if len(signatures)>1:conflicts[key]=[r['id'] for r in competing]
                oid,field=key.split('::',1)
                result['field_review'].append({'record_id':record['id'],'object_id':oid,
                    'field':field,'extracted_value':deepcopy(value),
                    'source_id':record['source_id'],'source_location':record.get('locator','payload.value'),
                    'before_value':field_value(world,oid,field) if field in SUPPORTED_FIELDS else None,
                    'status':'conflicting_evidence' if key in conflicts else 'requires_explicit_acceptance','adopted':False})
            # Validate each conflicting branch separately; no winner selected.
            if conflicts:
                for record in records:
                    _apply_records(world,[record],meta)
            else:_apply_records(world,records,meta)
            digest=hashlib.sha256(json.dumps([meta,records,_versions(world)],sort_keys=True,allow_nan=False).encode()).hexdigest()[:20]
            identifier='import-'+digest
            proposal={'id':identifier,'metadata':deepcopy(meta),'records':deepcopy(records),'conflicts':conflicts,
                      'base_versions':_versions(world),'field_review':deepcopy(result['field_review'])}
            self.proposals[identifier]=proposal
            result.update(status='conflicting_evidence' if conflicts else 'ready_for_review',proposal_id=identifier,
                          record_count=len(records),records=deepcopy(records),conflicts=conflicts,
                          data_kind=meta['data_kind'],coordinate_conversion='explicit_surveyed_correspondence' if format=='geojson' else 'none_same_reference')
        except (KeyError,TypeError,ValueError) as exc:
            result['status']=getattr(exc,'status','invalid_input');result['issues'].append({'reason':str(exc)})
        self.audit.append(deepcopy(result));return result

    def accept(self,world,proposal_id,explicit=False,resolutions=None):
        if explicit is not True:return {'status':'explicit_acceptance_required','world_updated':False},deepcopy(world)
        proposal=self.proposals.get(proposal_id)
        if not proposal:return {'status':'proposal_not_found','world_updated':False},deepcopy(world)
        if proposal['base_versions']!=_versions(world):return {'status':'needs_recheck','reason':'input_plan_or_reservation_version_changed','world_updated':False},deepcopy(world)
        records=deepcopy(proposal['records']);resolutions=resolutions or {}
        for key,identifiers in proposal['conflicts'].items():
            resolution=resolutions.get(key,{})
            if resolution.get('record_id') not in identifiers or not isinstance(resolution.get('reason'),str) or not resolution['reason'].strip():
                return {'status':'conflict_requires_explicit_reconciliation','field_key':key,'world_updated':False},deepcopy(world)
            selected=resolution['record_id']
            records=[row for row in records if _assertion(world,row)[0]!=key or row['id']==selected]
        try:
            updated,additions=_apply_records(world,records,proposal['metadata'])
            if updated['environment_version']==world['environment_version']:
                updated['environment_version']+=1  # explicit reconciliation is a knowledge update
            for key,resolution in resolutions.items():
                if key not in proposal['conflicts']:continue
                for identifier in proposal['conflicts'][key]:
                    updated.setdefault('import_record_decisions',{})[identifier]='accepted' if identifier==resolution['record_id'] else 'rejected_by_explicit_resolution'
                for row in updated.get('field_evidence',[]):
                    if row['id'] in proposal['conflicts'][key]:
                        row['acceptance_status']='accepted' if row['id']==resolution['record_id'] else 'superseded_by_explicit_resolution'
            before={row['id']:row for row in generate_constraints(world)};after={row['id']:row for row in generate_constraints(updated)}
            affected=sorted({row.get('payload',{}).get('object_id') or row.get('payload',{}).get('facility_id') or row.get('payload',{}).get('activity_id') or row.get('payload',{}).get('id') for row in records}-{None})
            audit={'status':'accepted','proposal_id':proposal_id,'world_updated':True,'before_versions':_versions(world),
                   'after_versions':_versions(updated),'accepted_record_ids':[r['id'] for r in records],
                   'rejected_record_ids':[r['id'] for r in proposal['records'] if r not in records],
                   'resolutions':deepcopy(resolutions),'data_kind':proposal['metadata']['data_kind'],
                   'affected_object_ids':affected,'changed_constraint_ids':sorted(key for key in before.keys()|after.keys() if before.get(key)!=after.get(key)),
                   'requires_reanalysis':True,'candidates_invalidated':True,**additions}
            # Retain discarded assertions as disputed evidence; a recorded human
            # resolution is explicit, and does not erase conflicting source data.
            for record in proposal['records']:
                if record not in records and record['id'] not in {r['id'] for r in updated.get('imported_records',[])}:
                    updated.setdefault('imported_records',[]).append(deepcopy(record))
                if record['record_type']=='field_evidence' and record not in records:
                    updated.setdefault('field_evidence',[]).append({**deepcopy(record),'acceptance_status':'rejected_by_explicit_resolution'})
            updated.setdefault('import_audit',[]).append(deepcopy(audit));validate_world(updated)
            self.audit.append(deepcopy(audit));return audit,updated
        except (KeyError,TypeError,ValueError) as exc:
            return {'status':getattr(exc,'status','invalid_input'),'reason':str(exc),'world_updated':False},deepcopy(world)


def main(argv=None):
    import argparse
    from .world import load_example,visible_world
    parser=argparse.ArgumentParser(description='Prepare and optionally explicitly accept structured materials into a disposable simulation world.')
    parser.add_argument('file');parser.add_argument('--format',choices=['json','csv','geojson'],default='json');parser.add_argument('--metadata')
    parser.add_argument('--example',default='examples/park_crane_synthetic.json');parser.add_argument('--scenario',default='end_unconfirmed');parser.add_argument('--as-of',type=float,default=60)
    parser.add_argument('--accept',action='store_true');parser.add_argument('--output',default='runs/import-review.json')
    args=parser.parse_args(argv);world=visible_world(load_example(args.example),args.scenario,args.as_of);service=StructuredImporter()
    metadata=_json(Path(args.metadata).read_text()) if args.metadata else None
    review=service.prepare(world,Path(args.file).read_text(encoding='utf-8'),args.format,metadata);result={'review':review}
    if args.accept and review['proposal_id']:
        audit,world=service.accept(world,review['proposal_id'],True);result['acceptance']=audit
        if audit['world_updated']:
            from .pipeline import analyze_world
            result['analysis']=analyze_world(world)
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'status':review['status'],'output':str(target),'accepted':result.get('acceptance',{}).get('world_updated',False)},ensure_ascii=False))

if __name__=='__main__':main()
