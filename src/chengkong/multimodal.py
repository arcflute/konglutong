"""Configurable structured HTTP multimodal boundary and an explicit mock adapter.

No provider is contacted unless this project supplies an explicit configuration.
A parsed model response is a proposal, not an authoritative world update.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .contracts import ContractError,number,stable_id,validate_event,integer
from .world import apply_event
from .evidence import generate_requests


def project_config(root):
    path=Path(root)/'config'/'multimodal.local.json'
    if not path.is_file():return {'provider':'disabled','status':'not_configured'}
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict):raise ContractError('multimodal_config_must_be_object')
    if data.get('provider')!='structured_http':raise ContractError('unsupported_multimodal_provider')
    endpoint=data.get('endpoint','')
    parsed=urllib.parse.urlparse(endpoint)
    if parsed.scheme not in ('http','https') or not parsed.hostname:raise ContractError('invalid_multimodal_endpoint')
    if parsed.username or parsed.password:raise ContractError('credentials_must_not_be_in_endpoint')
    key_name=data.get('api_key_env','CHENGKONG_VLM_API_KEY')
    if key_name!='CHENGKONG_VLM_API_KEY':raise ContractError('only_project_scoped_api_key_variable_allowed')
    # Only this project's dedicated variable; no credential discovery elsewhere.
    data['api_key']=os.environ.get(key_name,'')
    data['timeout_s']=number(data.get('timeout_s',10),'timeout_s',minimum=0.1)
    if data['timeout_s']>60:raise ContractError('multimodal_timeout_must_not_exceed_60_seconds')
    data['max_calls']=integer(data.get('max_calls',3),'multimodal.max_calls',1)
    if data['max_calls']>100:raise ContractError('multimodal_session_call_budget_must_not_exceed_100')
    return data


class DisabledAdapter:
    def parse(self,records,context):
        return {'status':'not_configured','provider':'disabled','candidate_events':[],
                'unknown_fields':['input_not_parsed'],'real_service_connected':False}


class StructuredHTTPAdapter:
    """POST normalized records/context; expects the documented JSON event schema."""
    def __init__(self,config):self.config=config;self.calls_used=0

    def parse(self,records,context):
        if self.calls_used>=self.config.get('max_calls',3):
            return {'status':'budget_exhausted','provider':'structured_http','candidate_events':[],
                    'calls_used':self.calls_used,'real_service_connected':False}
        self.calls_used+=1
        body=json.dumps({'records':records,'context':context,'response_schema':'chengkong-extraction-v1',
                         'model_id':self.config.get('model_id')},allow_nan=False).encode()
        headers={'Content-Type':'application/json','Accept':'application/json'}
        if self.config.get('api_key'):headers['Authorization']='Bearer '+self.config['api_key']
        request=urllib.request.Request(self.config['endpoint'],data=body,headers=headers,method='POST')
        try:
            with urllib.request.urlopen(request,timeout=self.config['timeout_s']) as response:
                payload=response.read(1_048_577)
                if len(payload)>1_048_576:return {'status':'invalid_response','reason':'response_too_large','candidate_events':[]}
                parsed=json.loads(payload)
                if not isinstance(parsed,dict):raise ValueError('response_not_object')
                return {**parsed,'status':'service_response','provider':'structured_http','real_service_connected':True,
                        'calls_used':self.calls_used,'model_id':self.config.get('model_id')}
        except (TimeoutError,socket.timeout):
            return {'status':'timeout','candidate_events':[],'real_service_connected':False}
        except urllib.error.HTTPError as exc:
            return {'status':'service_error','http_status':exc.code,'candidate_events':[],'real_service_connected':False}
        except urllib.error.URLError as exc:
            return {'status':'timeout' if isinstance(exc.reason,(TimeoutError,socket.timeout)) else 'connection_error',
                    'candidate_events':[],'real_service_connected':False}
        except (ValueError,UnicodeError):
            return {'status':'invalid_response','candidate_events':[],'real_service_connected':False}


class MockAdapter:
    def __init__(self,response,failure=None):self.response=copy.deepcopy(response);self.failure=failure
    def parse(self,records,context):
        if self.failure:return {'status':self.failure,'candidate_events':[],'real_service_connected':False,'provider':'explicit_mock'}
        if not isinstance(self.response,dict):
            return {'status':'invalid_response','candidate_events':[],'real_service_connected':False,'provider':'explicit_mock'}
        return {**copy.deepcopy(self.response),'status':'simulated_response','provider':'explicit_mock','real_service_connected':False}


def configured_adapter(root):
    config=project_config(root)
    return StructuredHTTPAdapter(config) if config.get('provider')=='structured_http' else DisabledAdapter()


class MaterialAgent:
    """Query supplied materials, validate fields/evidence, then apply explicit acceptance."""
    def __init__(self):self.proposals={};self.audit=[]

    def _finish_preparation(self,world,visible,result):
        """Route grounded gaps to the same budgeted evidence service as planning.

        Unknown field strings may be ``object_id.field`` or a field matching one
        unique visible gap. Ambiguous references are kept for human identification;
        no guessed entity ID or invented source record enters a query.
        """
        objects={obj['id'] for key in ('facilities','activities','buildings') for obj in world[key]}
        gaps={(gap['object_id'],gap['field']) for gap in world.get('information_gaps',[])
              if gap.get('object_id') in objects and isinstance(gap.get('field'),str)}
        field_review=[]
        unknown=result.get('unknown_fields',[])
        for field in unknown if isinstance(unknown,list) else []:
            if not isinstance(field,str):continue
            matches=[(oid,field[len(oid)+1:]) for oid in objects if field.startswith(oid+'.')]
            if not matches:matches=[gap for gap in gaps if gap[1]==field]
            if len(matches)!=1:
                field_review.append({'input_field':field,'object_id':None,
                    'status':'manual_object_identification_required',
                    'reason':'No unique object/field reference in currently visible materials and world gaps'})
                continue
            oid,name=matches[0]
            field_review.append({'input_field':field,'object_id':oid,'field':name,
                'status':'mapped_visible_information_gap' if (oid,name) in gaps else 'field_evidence_adapter_not_connected'})
        requests=[]
        for layer in ('conflict_analysis','maintenance_decision'):
            for request in generate_requests(world,source_layer=layer,max_queries=1,
                    deadline_s=min(world['horizon_s'][1],world['as_of_s']+60)):
                request.update(initiator='material_agent',material_record_ids=sorted(visible),
                    mapped_unknown_fields=[row['input_field'] for row in field_review
                        if row.get('object_id')==request['object_id'] and row.get('field')==request['field']],
                    request_basis='visible_world_gap_and_computed_conflict_or_decision_change',
                    query_status='not_queried',answer=None)
                requests.append(request)
        triggered={(r['object_id'],r['field']) for r in requests}
        from .field_evidence import make_field_request, SUPPORTED_FIELDS
        for row in field_review:
            if row['status']=='mapped_visible_information_gap':
                row['status']='budgeted_evidence_request_ready' if (row['object_id'],row['field']) in triggered else 'no_current_decision_value_trigger'
            elif row.get('object_id') and row.get('field') in SUPPORTED_FIELDS:
                request=make_field_request(world,row['object_id'],row['field'],reason='material_agent_missing_supported_field',
                    max_queries=1,deadline_s=min(world['horizon_s'][1],world['as_of_s']+60))
                request.update(initiator='material_agent',material_record_ids=sorted(visible),query_status='not_queried')
                requests.append(request);row['status']='field_evidence_request_ready'
            elif row.get('object_id') and row.get('field')!='completion_status':
                row['status']='unsupported_field'
        result['field_review']=field_review
        result['evidence_requests']=requests
        result['material_query_scope']={'visible_record_ids':sorted(visible),
            'future_or_external_sources_queried':False,
            'answer_policy':'No answer is generated from missing fields; use the ordinary evidence query and first-layer update path.'}
        self.audit.append(copy.deepcopy(result))
        return result

    def prepare(self,world,records,adapter=None):
        adapter=adapter or DisabledAdapter()
        if not isinstance(records,list):raise ContractError('materials_must_be_array')
        visible={}
        for record in records:
            if not isinstance(record,dict) or not record.get('id'):raise ContractError('material_requires_id')
            stable_id(record['id'],'material.id')
            if record['id'] in visible:raise ContractError('duplicate_material_id')
            if number(record.get('observed_at_s'),'material.observed_at_s',minimum=0)>world['as_of_s']:
                raise ContractError('future_material_not_visible')
            if record.get('kind') not in ('text','image','video_frame'):raise ContractError('unsupported_material_kind')
            if record['kind']=='text' and not isinstance(record.get('text'),str):
                raise ContractError('text_material_requires_text_string')
            visible[record['id']]=record
        context={key:world[key] for key in ('as_of_s','environment_version','scenario_id','coordinate_system','time_system')}
        parsed=adapter.parse(list(visible.values()),context)
        if not isinstance(parsed,dict):
            parsed={'status':'invalid_response','reason':'response_must_be_object','candidate_events':[]}
        result={'status':parsed.get('status','invalid_response'),'provider':parsed.get('provider'),
                'real_service_connected':parsed.get('real_service_connected',False),'proposals':[],
                'rejected':[],'unknown_fields':parsed.get('unknown_fields',[]),'world_updated':False}
        if result['status'] not in ('service_response','simulated_response'):
            return self._finish_preparation(world,visible,result)
        if not isinstance(result['unknown_fields'],list) or any(not isinstance(item,str) for item in result['unknown_fields']):
            result['status']='invalid_response';result['rejected'].append({'reason':'unknown_fields_must_be_string_array'})
            return self._finish_preparation(world,visible,result)
        if parsed.get('schema_version')!='chengkong-extraction-v1' or not isinstance(parsed.get('candidate_events'),list):
            result['status']='invalid_response';result['rejected'].append({'reason':'invalid_response_schema'})
            return self._finish_preparation(world,visible,result)
        for row in parsed['candidate_events']:
            try:
                if not isinstance(row,dict):raise ContractError('candidate_event_row_must_be_object')
                event=copy.deepcopy(row['event']);refs=row['evidence_refs']
                if not isinstance(event,dict):raise ContractError('candidate_event_must_be_object')
                if not isinstance(refs,list):raise ContractError('evidence_references_must_be_array')
                if not refs:raise ContractError('evidence_reference_required')
                for ref in refs:
                    if not isinstance(ref,dict):raise ContractError('evidence_reference_must_be_object')
                    if not isinstance(ref.get('record_id'),str):raise ContractError('evidence_record_id_must_be_string')
                    record=visible.get(ref.get('record_id'))
                    if not record or not isinstance(ref.get('locator'),str) or not ref['locator']:
                        raise ContractError('evidence_reference_not_found')
                    if record['kind']=='text' and (not isinstance(ref.get('quote'),str) or not ref['quote'] or ref['quote'] not in record['text']):
                        raise ContractError('evidence_quote_not_present')
                def contains_geometry(value):
                    if isinstance(value,dict):
                        return any(key in ('physical_geometry','activity_envelope','geometry','motion_model','parameters','parking_pose') or contains_geometry(item)
                                   for key,item in value.items())
                    return isinstance(value,list) and any(contains_geometry(item) for item in value)
                if contains_geometry(event):
                    for ref in refs:
                        record=visible[ref['record_id']]
                        if record['kind'] in ('image','video_frame'):
                            metric=record.get('metric_reference')
                            if not isinstance(metric,dict):
                                raise ContractError('image_has_no_scale_or_coordinate_reference')
                            if (metric.get('unit')!='m' or metric.get('height_datum')!=world['coordinate_system']['height_datum']
                                or metric.get('axes')!=world['coordinate_system']['axes']
                                or not isinstance(metric.get('source_record_id'),str)
                                or metric['source_record_id'] not in visible):
                                raise ContractError('image_metric_reference_is_incomplete')
                            if number(metric.get('scale_m_per_pixel'),'metric_scale',minimum=0)<=0:
                                raise ContractError('image_metric_reference_scale_must_be_positive')
                            origin=metric.get('coordinate_origin_m')
                            if not isinstance(origin,list) or len(origin)!=3:
                                raise ContractError('image_metric_reference_requires_coordinate_origin')
                            for coordinate in origin:number(coordinate,'metric_origin')
                event['evidence_refs']=copy.deepcopy(refs)
                event['material_records']=[{key:copy.deepcopy(value) for key,value in visible[identifier].items()
                                            if key in ('id','kind','observed_at_s','text','metric_reference','image_ref','frame_ref')}
                                           for identifier in sorted({ref['record_id'] for ref in refs})]
                event['source_kind']='synthetic_multimodal_mock' if result['provider']=='explicit_mock' else 'reviewed_multimodal_record'
                validate_event(event,world)
                if event['observed_at_s']>world['as_of_s']:raise ContractError('event_observation_in_future')
                # Validate the complete proposed transition without committing it.
                apply_event(world,event)
                digest=hashlib.sha256(json.dumps([event,refs,context],sort_keys=True).encode()).hexdigest()[:20]
                proposal={'id':'material-'+digest,'event':event,'evidence_refs':copy.deepcopy(refs),
                          'base_environment_version':world['environment_version'],'scenario_id':world['scenario_id'],
                          'base_plan_versions':{task['id']:task['plan_version'] for task in world['tasks']},
                          'base_reservation_version':world.get('reservation_version',1),
                          'rule_version':world['rule_version'],'model_version':world['model_version'],
                          'data_kind':'synthetic_mock' if result['provider']=='explicit_mock' else 'record_assertion',
                          'status':'awaiting_explicit_acceptance','model_self_confidence_is_not_calibrated':True}
                self.proposals[proposal['id']]=copy.deepcopy(proposal);result['proposals'].append(proposal)
            except (KeyError,TypeError,ValueError) as exc:
                result['rejected'].append({'reason':str(exc)})
        result['status']='proposals_ready' if result['proposals'] else 'no_valid_proposals'
        return self._finish_preparation(world,visible,result)

    def apply(self,world,proposal_id,explicit=False):
        if explicit is not True:return {'status':'explicit_acceptance_required','world_updated':False},world
        proposal=self.proposals.get(proposal_id)
        if proposal is None:return {'status':'proposal_not_found','world_updated':False},world
        if proposal['base_environment_version']!=world['environment_version'] or proposal['scenario_id']!=world['scenario_id']:
            return {'status':'needs_recheck','world_updated':False},world
        if (proposal.get('base_plan_versions')!={task['id']:task['plan_version'] for task in world['tasks']} or
            proposal.get('base_reservation_version')!=world.get('reservation_version',1) or
            any(proposal.get(key)!=world[key] for key in ('rule_version','model_version'))):
            return {'status':'needs_recheck','reason':'plan_reservation_or_method_version_changed','world_updated':False},world
        updated=apply_event(world,proposal['event'])
        audit={'status':'applied','proposal_id':proposal_id,'world_updated':True,
               'before_environment_version':world['environment_version'],'after_environment_version':updated['environment_version'],
               'evidence_refs':proposal['evidence_refs'],'data_kind':proposal['data_kind']}
        self.audit.append(copy.deepcopy(audit));return audit,updated
