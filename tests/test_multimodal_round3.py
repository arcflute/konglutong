import copy
import unittest
from chengkong.world import load_example,visible_world
from chengkong.multimodal import MaterialAgent,MockAdapter,StructuredHTTPAdapter

class MultimodalRound3Tests(unittest.TestCase):
    def setUp(self):
        self.world=visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',60)

    def test_agent_supported_field_gap_routes_to_field_query(self):
        response={'schema_version':'chengkong-extraction-v1','candidate_events':[],
                  'unknown_fields':['crane_1.facility.height','crane_1.not_supported_field']}
        result=MaterialAgent().prepare(self.world,[],MockAdapter(response))
        request=next(row for row in result['evidence_requests'] if row['field']=='facility.height')
        self.assertEqual(request['source_layer'],'field_validation');self.assertIsNone(request['answer'])
        review={row['field']:row for row in result['field_review'] if row.get('field')}
        self.assertEqual(review['facility.height']['status'],'field_evidence_request_ready')
        self.assertEqual(review['not_supported_field']['status'],'unsupported_field')

    def test_motion_parameters_in_an_image_require_metric_reference(self):
        response={'schema_version':'chengkong-extraction-v1','candidate_events':[{'event':{
            'id':'motion_image','kind':'facility_motion_changed','facility_id':'crane_1',
            'observed_at_s':60,'effective_at_s':60,'source_kind':'mock','motion_model':{'tower_height_m':80}},
            'evidence_refs':[{'record_id':'photo','locator':'image:crane'}]}]}
        records=[{'id':'photo','kind':'image','observed_at_s':60,'image_ref':'provided_photo'}]
        result=MaterialAgent().prepare(self.world,records,MockAdapter(response))
        self.assertEqual(result['status'],'no_valid_proposals')
        self.assertIn('no_scale',result['rejected'][0]['reason'])

    def test_adapter_stops_at_declared_budget_without_network_call(self):
        adapter=StructuredHTTPAdapter({'endpoint':'http://127.0.0.1:1','timeout_s':.01,'max_calls':1})
        first=adapter.parse([],{});self.assertEqual(first['status'],'connection_error')
        second=adapter.parse([],{});self.assertEqual(second['status'],'budget_exhausted')
        self.assertEqual(adapter.calls_used,1)
        self.assertFalse(second['real_service_connected'])

    def test_material_proposal_freezes_plan_and_reservation_versions(self):
        records=[{'id':'notice','kind':'text','observed_at_s':60,'text':'planned end 600'}]
        response={'schema_version':'chengkong-extraction-v1','candidate_events':[{'event':{
            'id':'delay_proposal','kind':'planned_end_changed','activity_id':'lifting_1','new_planned_end_s':600,
            'observed_at_s':60,'effective_at_s':60,'source_kind':'synthetic'},
            'evidence_refs':[{'record_id':'notice','locator':'text:0','quote':'planned end 600'}]}]}
        agent=MaterialAgent();review=agent.prepare(self.world,records,MockAdapter(response));identifier=review['proposals'][0]['id']
        changed=copy.deepcopy(self.world);changed['reservation_version']=2
        outcome,unchanged=agent.apply(changed,identifier,True)
        self.assertEqual(outcome['status'],'needs_recheck');self.assertEqual(unchanged,changed)
        changed=copy.deepcopy(self.world);changed['tasks'][0]['plan_version']+=1
        self.assertEqual(agent.apply(changed,identifier,True)[0]['status'],'needs_recheck')

if __name__=='__main__':unittest.main()
