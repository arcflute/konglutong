import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from chengkong.world import load_example,visible_world
from chengkong.contracts import ContractError
from chengkong.evidence import query_evidence,apply_evidence_result
from chengkong.multimodal import MaterialAgent,MockAdapter,DisabledAdapter,StructuredHTTPAdapter,project_config


class MultimodalTests(unittest.TestCase):
    def setUp(self):
        self.world=visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',60)
        self.records=[{'id':'provided_notice','kind':'text','observed_at_s':60,'text':'合成记录：吊装计划结束调整为600秒，尚未确认完成。'}]
        self.response={'schema_version':'chengkong-extraction-v1','candidate_events':[{
            'event':{'id':'parsed_delay','kind':'planned_end_changed','activity_id':'lifting_1','observed_at_s':60,'effective_at_s':60,'source_kind':'synthetic_record','new_planned_end_s':600},
            'evidence_refs':[{'record_id':'provided_notice','locator':'text:0','quote':'计划结束调整为600秒'}]}]}

    def test_mock_prepare_then_controlled_apply_preserves_unknown(self):
        agent=MaterialAgent();before=copy.deepcopy(self.world)
        result=agent.prepare(self.world,self.records,MockAdapter(self.response))
        self.assertEqual(result['status'],'proposals_ready')
        self.assertFalse(result['real_service_connected'])
        proposal=result['proposals'][0]
        self.assertEqual(self.world,before)
        denied,_=agent.apply(self.world,proposal['id'])
        self.assertEqual(denied['status'],'explicit_acceptance_required')
        accepted,updated=agent.apply(self.world,proposal['id'],True)
        self.assertTrue(accepted['world_updated'])
        self.assertEqual(updated['activities'][0]['planned_interval_s'][1],600)
        self.assertEqual(updated['activities'][0]['completion_status'],'not_confirmed')
        self.assertEqual(agent.apply(updated,proposal['id'],True)[0]['status'],'needs_recheck')

    def test_image_without_scale_cannot_supply_metric_geometry(self):
        response=copy.deepcopy(self.response);row=response['candidate_events'][0]
        row['event']={'id':'image_size','kind':'facility_geometry_changed','facility_id':'crane_1','effective_at_s':60,'observed_at_s':60,'source_kind':'mock',
                      'physical_geometry':[{'type':'aabb','min_m':[490,390,0],'max_m':[510,410,90]}]}
        row['evidence_refs']=[{'record_id':'photo','locator':'image:box:0'}]
        record=[{'id':'photo','kind':'image','observed_at_s':60,'image_ref':'user_supplied_photo'}]
        result=MaterialAgent().prepare(self.world,record,MockAdapter(response))
        self.assertEqual(result['status'],'no_valid_proposals')
        self.assertIn('no_scale',result['rejected'][0]['reason'])

    def test_invalid_evidence_timeout_and_disabled_do_not_update(self):
        response=copy.deepcopy(self.response)
        response['candidate_events'][0]['evidence_refs'][0]['quote']='不存在的测量'
        result=MaterialAgent().prepare(self.world,self.records,MockAdapter(response))
        self.assertEqual(result['status'],'no_valid_proposals')
        for adapter,status in [(DisabledAdapter(),'not_configured'),(MockAdapter({},'timeout'),'timeout')]:
            result=MaterialAgent().prepare(self.world,self.records,adapter)
            self.assertEqual(result['status'],status);self.assertFalse(result['world_updated'])

    def test_no_project_config_is_not_configured(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(project_config(root)['status'],'not_configured')

    def test_malformed_model_rows_references_and_response_are_rejected(self):
        original=copy.deepcopy(self.world)
        valid=self.response['candidate_events'][0]
        cases=[None, {'event':[], 'evidence_refs':valid['evidence_refs']},
               {'event':valid['event'], 'evidence_refs':'not_an_array'},
               {'event':valid['event'], 'evidence_refs':[42]},
               {'event':valid['event'], 'evidence_refs':[{'record_id':[], 'locator':'line:1'}]},
               {'event':valid['event'], 'evidence_refs':[{'record_id':'provided_notice', 'locator':[]} ]},
               {'event':valid['event'], 'evidence_refs':[{'record_id':'provided_notice', 'locator':'line:1', 'quote':42}]}]
        for row in cases:
            with self.subTest(row=row):
                response={'schema_version':'chengkong-extraction-v1','candidate_events':[row]}
                result=MaterialAgent().prepare(self.world,self.records,MockAdapter(response))
                self.assertEqual(result['status'],'no_valid_proposals')
                self.assertEqual(result['proposals'],[])
                self.assertTrue(result['rejected'])
                self.assertFalse(result['world_updated'])
        for response in (None,[],42,{'schema_version':'chengkong-extraction-v1','candidate_events':[],'unknown_fields':'bad'}):
            with self.subTest(response=response):
                result=MaterialAgent().prepare(self.world,self.records,MockAdapter(response))
                self.assertEqual(result['status'],'invalid_response')
        class BadResponseAdapter:
            def parse(self,records,context):return []
        self.assertEqual(MaterialAgent().prepare(self.world,self.records,BadResponseAdapter())['status'],'invalid_response')
        self.assertEqual(self.world,original)

    def test_malformed_material_shape_is_an_explicit_contract_error(self):
        original=copy.deepcopy(self.world)
        for records in ({},[42],[{'id':['unhashable']}],
                        [{'id':'bad_text','kind':'text','observed_at_s':60,'text':42}]):
            with self.subTest(records=records),self.assertRaises(ContractError):
                MaterialAgent().prepare(self.world,records,MockAdapter(self.response))
        self.assertEqual(self.world,original)
        with tempfile.TemporaryDirectory() as root:
            config=Path(root)/'config';config.mkdir()
            (config/'multimodal.local.json').write_text('[]',encoding='utf-8')
            with self.assertRaisesRegex(ContractError,'config_must_be_object'):
                project_config(root)

    def test_agent_gaps_reuse_budgeted_evidence_without_inventing_entities(self):
        response={'schema_version':'chengkong-extraction-v1','candidate_events':[],
                  'unknown_fields':['lifting_1.completion_status','unidentified_object.height_m']}
        before=copy.deepcopy(self.world)
        result=MaterialAgent().prepare(self.world,self.records,MockAdapter(response))
        self.assertEqual(result['status'],'no_valid_proposals')
        self.assertTrue(result['evidence_requests'])
        for request in result['evidence_requests']:
            self.assertEqual(request['initiator'],'material_agent')
            self.assertEqual(request['object_id'],'lifting_1')
            self.assertEqual(request['max_queries'],1)
            self.assertEqual(request['deadline_s'],120)
            self.assertEqual(request['material_record_ids'],['provided_notice'])
            self.assertIsNone(request['answer'])
        unresolved=next(row for row in result['field_review'] if row['input_field'].startswith('unidentified'))
        self.assertEqual(unresolved['status'],'manual_object_identification_required')
        self.assertIsNone(unresolved['object_id'])
        request=result['evidence_requests'][0]
        answer=query_evidence(self.world,request)
        self.assertEqual(answer['status'],'unknown')
        self.assertEqual(apply_evidence_result(self.world,answer),before)
        self.assertEqual(query_evidence(self.world,request,queries_used=1)['status'],'budget_exhausted')

    def test_agent_request_visible_evidence_returns_through_first_layer(self):
        world=visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',360)
        response={'schema_version':'chengkong-extraction-v1','candidate_events':[],
                  'unknown_fields':['lifting_1.completion_status']}
        result=MaterialAgent().prepare(world,self.records,MockAdapter(response))
        request=result['evidence_requests'][0]
        source={'id':'agent_visible_log','kind':'synthetic_evidence_source','records':[{
            'id':'agent_confirmation','kind':'activity_completion_confirmed','activity_id':'lifting_1',
            'effective_at_s':360,'observed_at_s':360,'source_kind':'synthetic_confirmation'}]}
        answer=query_evidence(world,request,evidence_source=source)
        self.assertEqual(answer['status'],'resolved')
        self.assertFalse(answer['world_updated'])
        updated=apply_evidence_result(world,answer)
        self.assertEqual(updated['environment_version'],world['environment_version']+1)
        self.assertEqual(updated['activities'][0]['completion_confirmed_at_s'],360)
        self.assertTrue(updated['facilities'][0]['installed'])
        self.assertEqual(updated['event_log'][-1]['event']['evidence_refs'],['agent_visible_log'])
        self.assertEqual(world['activities'][0]['completion_status'],'not_confirmed')

    def test_real_http_transport_against_explicit_local_mock(self):
        response=self.response
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if request['context'].get('slow'):time.sleep(.15)
                payload=json.dumps(response).encode()
                try:
                    self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(payload)
                except (BrokenPipeError,ConnectionResetError):pass
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            adapter=StructuredHTTPAdapter({'endpoint':f'http://127.0.0.1:{server.server_port}/parse','timeout_s':1})
            self.assertEqual(adapter.parse(self.records,{})['status'],'service_response')
            adapter.config['timeout_s']=.03
            self.assertEqual(adapter.parse(self.records,{'slow':True})['status'],'timeout')
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
