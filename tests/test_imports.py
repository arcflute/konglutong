import copy
import json
from pathlib import Path
import unittest
from chengkong.world import load_example, visible_world, apply_event, advance_world, generate_constraints
from chengkong.imports import StructuredImporter
from chengkong.field_evidence import make_field_request, query_field_evidence
from chengkong.pipeline import analyze_world


class StructuredImportTests(unittest.TestCase):
    def setUp(self):
        self.world=visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',60)
        self.material=json.loads(Path('examples/imports/construction_delay.json').read_text())

    def apply(self,material=None):
        service=StructuredImporter();review=service.prepare(self.world,material or self.material)
        self.assertEqual(review['status'],'ready_for_review',review)
        audit,updated=service.accept(self.world,review['proposal_id'],True)
        self.assertEqual(audit['status'],'accepted',audit)
        return service,review,audit,updated

    def test_review_accept_reanalysis_and_no_automatic_plan_change(self):
        before=copy.deepcopy(self.world);service=StructuredImporter();review=service.prepare(self.world,self.material)
        self.assertEqual(self.world,before)
        self.assertEqual(service.accept(self.world,review['proposal_id'])[0]['status'],'explicit_acceptance_required')
        audit,updated=service.accept(self.world,review['proposal_id'],True)
        self.assertEqual(updated['activities'][0]['planned_interval_s'][1],600)
        self.assertEqual(updated['activities'][0]['completion_status'],'not_confirmed')
        self.assertEqual(updated['tasks'],before['tasks'])
        result=analyze_world(updated)
        self.assertEqual(result['world']['environment_version'],updated['environment_version'])
        self.assertTrue(result['baseline_analysis']);self.assertTrue(audit['changed_constraint_ids'])
        self.assertEqual(service.accept(updated,review['proposal_id'],True)[0]['status'],'needs_recheck')

    def test_units_origins_height_time_and_future_cannot_be_guessed(self):
        for section,key,value in [('coordinate_system','unit','ft'),('coordinate_system','origin','other'),
                                  ('coordinate_system','height_datum','ellipsoid'),('time_system','origin_label','other')]:
            payload=copy.deepcopy(self.material);payload[section][key]=value
            self.assertEqual(StructuredImporter().prepare(self.world,payload)['status'],'unsupported')
        payload=copy.deepcopy(self.material);payload['records'][0]['observed_at_s']=61
        self.assertEqual(StructuredImporter().prepare(self.world,payload)['status'],'invalid_input')
        payload=copy.deepcopy(self.material);payload['records'][0]['valid_until_s']=60
        self.assertIn('expiry',StructuredImporter().prepare(self.world,payload)['issues'][0]['reason'])

    def test_json_csv_geojson_use_same_reference_no_automatic_projection(self):
        csv_text=Path('examples/imports/construction_delay.csv').read_text();metadata=json.loads(Path('examples/imports/metadata.json').read_text())
        service=StructuredImporter();review=service.prepare(self.world,csv_text,'csv',metadata)
        self.assertEqual(review['status'],'ready_for_review',review)
        geo=json.loads(Path('examples/imports/surveyed_pad.geojson').read_text())
        self.assertEqual(service.prepare(self.world,geo,'geojson',metadata)['status'],'unsupported')
        metadata=json.loads(Path('examples/imports/geojson_metadata.json').read_text());review=service.prepare(self.world,geo,'geojson',metadata)
        self.assertEqual(review['status'],'ready_for_review',review)
        audit,updated=service.accept(self.world,review['proposal_id'],True)
        self.assertEqual(updated['pads'][-1]['position_m'],[900,700,0]);self.assertTrue(audit['world_updated'])
        geo['features'][0]['geometry']['coordinates'][2]=99
        self.assertEqual(service.prepare(self.world,geo,'geojson',metadata)['status'],'invalid_input')

    def test_append_tasks_sources_resources_survive_future_construction_replay(self):
        material=json.loads(Path('examples/imports/park_tasks_resources.json').read_text())
        service,review,audit,updated=self.apply(material)
        self.assertEqual(audit['task_additions'],['import_task'])
        self.assertEqual(len(updated['tasks']),len(self.world['tasks'])+1)
        updated=apply_event(updated,{'id':'after_import','kind':'planned_end_changed','activity_id':'lifting_1',
                            'effective_at_s':60,'observed_at_s':60,'new_planned_end_s':650,'source_kind':'synthetic'})
        updated=advance_world(updated,90)
        self.assertIn('import_crane',{row['id'] for row in updated['facilities']})
        self.assertIn('import_building',{row['id'] for row in updated['buildings']})
        self.assertIn('structured_demo',{row['id'] for row in updated['known_sources']})
        self.assertIn('import_task',{row['id'] for row in updated['tasks']})
        self.assertEqual(updated['waiting_areas'][0]['id'],'import_wait')
        self.assertEqual(updated['reservation_version'],2)
        material['records'][3]['payload']['id']=self.world['tasks'][0]['id']
        self.assertEqual(StructuredImporter().prepare(self.world,material)['status'],'invalid_input')

    def test_field_conflict_requires_explicit_selection_and_keeps_discarded_source(self):
        payload=copy.deepcopy(self.material);second=copy.deepcopy(payload['records'][0]);second['id']='different_delay';second['payload']['value'][1]=700;payload['records'].append(second)
        service=StructuredImporter();review=service.prepare(self.world,payload)
        self.assertEqual(review['status'],'conflicting_evidence')
        audit,unchanged=service.accept(self.world,review['proposal_id'],True)
        self.assertEqual(audit['status'],'conflict_requires_explicit_reconciliation');self.assertEqual(unchanged,self.world)
        audit,updated=service.accept(self.world,review['proposal_id'],True,{'lifting_1::planned_interval':{'record_id':'delay_record','reason':'operator verified the first signed synthetic record'}})
        self.assertEqual(updated['activities'][0]['planned_interval_s'][1],600)
        self.assertEqual(len(updated['field_evidence']),2)
        request=make_field_request(updated,'lifting_1','planned_interval');result=query_field_evidence(updated,request)
        self.assertEqual(result['status'],'evidence_available');self.assertEqual(result['answer'][1],600)
        self.assertIn('different_delay',audit['rejected_record_ids'])

    def test_later_upload_does_not_automatically_overwrite_existing_evidence(self):
        _,_,_,updated=self.apply();payload=copy.deepcopy(self.material);payload['records'][0]['id']='newer_claim';payload['records'][0]['payload']['value'][1]=900
        service=StructuredImporter();review=service.prepare(updated,payload)
        self.assertEqual(review['status'],'conflicting_evidence');self.assertEqual(updated['activities'][0]['planned_interval_s'][1],600)
        audit,reconciled=service.accept(updated,review['proposal_id'],True,{'lifting_1::planned_interval':{'record_id':'delay_record','reason':'retain validated existing evidence'}})
        self.assertTrue(audit['world_updated']);self.assertGreater(reconciled['environment_version'],updated['environment_version'])
        self.assertEqual(reconciled['activities'][0]['planned_interval_s'][1],600)

    def test_fields_have_explicit_scope_and_geometric_numeric_effect(self):
        cases=[('geometry.position',{'from_anchor_m':[500,400,0],'to_anchor_m':[520,400,0]}),
               ('facility.height',{'component_index':0,'top_z_m':70}),
               ('parking_pose',{'physical_geometry':[{'type':'aabb','min_m':[490,390,0],'max_m':[510,410,60]}],'pose':{'angle_deg':0}})]
        for field,value in cases:
            material=copy.deepcopy(self.material);material['records'][0]['payload']={'object_id':'crane_1','field':field,'value':value}
            _,_,audit,updated=self.apply(material);self.assertNotEqual(updated['facilities'][0]['physical_geometry'],self.world['facilities'][0]['physical_geometry']);self.assertTrue(audit['changed_constraint_ids'])
        material=copy.deepcopy(self.material);material['records'][0]['payload']['field']='arbitrary_diameter'
        self.assertEqual(StructuredImporter().prepare(self.world,material)['status'],'unsupported')
        self.assertEqual(make_field_request(self.world,'crane_1','not_a_field')['status'],'unsupported')

    def test_completion_keeps_facility_and_expiry_timeout_unknown(self):
        self.world=advance_world(self.world,360)
        material=copy.deepcopy(self.material);material['records'][0].update(effective_at_s=360,observed_at_s=360)
        material['records'][0]['payload']={'object_id':'lifting_1','field':'completion','value':{'status':'confirmed','confirmed_at_s':360}}
        _,_,_,updated=self.apply(material)
        self.assertEqual(updated['activities'][0]['completion_status'],'confirmed');self.assertTrue(updated['facilities'][0]['installed'])
        self.assertTrue(any(c['object_id']=='crane_1' for c in generate_constraints(updated)))
        request=make_field_request(self.world,'crane_1','facility.height')
        self.assertEqual(query_field_evidence(self.world,request)['status'],'unknown')
        self.assertEqual(query_field_evidence(self.world,request,simulated_delay_s=8,source_kind='synthetic_evidence_source')['status'],'timeout')
        self.assertEqual(query_field_evidence(self.world,request,queries_used=1)['status'],'budget_exhausted')
        expired=copy.deepcopy(material['records'][0]);expired['payload']={'object_id':'crane_1','field':'facility.height','value':{'component_index':0,'top_z_m':70}};expired['effective_at_s']=0;expired['valid_until_s']=59
        self.assertEqual(query_field_evidence(self.world,request,[expired])['status'],'expired')

    def test_reservation_version_rejects_stale_import_and_failure_is_atomic(self):
        service=StructuredImporter();review=service.prepare(self.world,self.material);changed=copy.deepcopy(self.world);changed['reservation_version']=2
        self.assertEqual(service.accept(changed,review['proposal_id'],True)[0]['status'],'needs_recheck')
        material=copy.deepcopy(self.material);invalid=copy.deepcopy(material['records'][0]);invalid['id']='bad';invalid['payload']['object_id']='missing';material['records'].append(invalid)
        before=copy.deepcopy(self.world);self.assertEqual(service.prepare(self.world,material)['status'],'invalid_input');self.assertEqual(self.world,before)

    def test_public_geojson_without_surveyed_control_is_explicitly_unsupported(self):
        manifest=json.loads(Path('examples/imports/public/manifest.json').read_text())
        self.assertEqual(manifest['status'],'retrieved')
        data=json.loads(Path('examples/imports/public/usgs_one_event.geojson').read_text())
        self.assertEqual(len(data['features']),1)
        metadata=json.loads(Path('examples/imports/metadata.json').read_text())
        review=StructuredImporter().prepare(self.world,data,'geojson',metadata)
        self.assertEqual(review['status'],'unsupported')
        self.assertIn('surveyed_point_mapping',review['issues'][0]['reason'])
        self.assertFalse(review['world_updated'])

    def test_range_change_preserves_field_and_constraint_provenance(self):
        material=copy.deepcopy(self.material)
        material['records'][0]['payload']={'object_id':'lifting_1','field':'activity.range',
            'value':{'type':'vertical_cylinder','center_xy_m':[500,400],'radius_m':45,'z_min_m':0,'z_max_m':80}}
        material['records'][0]['effective_at_s']=120
        _,_,_,pending=self.apply(material)
        self.assertNotEqual(pending['activities'][0]['activity_envelope']['radius_m'],45)
        updated=advance_world(pending,120)
        self.assertEqual(updated['activities'][0]['activity_envelope']['radius_m'],45)
        self.assertEqual(updated['field_evidence'][0]['source_id'],'structured_demo')
        self.assertEqual(updated['observed_events'][-1]['evidence_refs'][0]['record_id'],'delay_record')

    def test_native_event_rows_cannot_bypass_field_conflict_review(self):
        material=copy.deepcopy(self.material)
        row=copy.deepcopy(material['records'][0]);row.update(id='native_delay',record_type='event')
        row['payload']={'kind':'planned_end_changed','activity_id':'lifting_1','new_planned_end_s':700}
        material['records'].append(row)
        service=StructuredImporter();review=service.prepare(self.world,material)
        self.assertEqual(review['status'],'conflicting_evidence',review)
        self.assertEqual(service.accept(self.world,review['proposal_id'],True)[0]['status'],'conflict_requires_explicit_reconciliation')
        audit,updated=service.accept(self.world,review['proposal_id'],True,{'lifting_1::planned_interval':{'record_id':'native_delay','reason':'explicit operator source reconciliation'}})
        self.assertEqual(audit['status'],'accepted');self.assertEqual(updated['activities'][0]['planned_interval_s'][1],700)
        self.assertEqual(updated['import_record_decisions']['delay_record'],'rejected_by_explicit_resolution')
        request=make_field_request(updated,'lifting_1','planned_interval')
        result=query_field_evidence(updated,request)
        self.assertEqual(result['status'],'evidence_available');self.assertEqual(result['answer'][1],700)

if __name__=='__main__':unittest.main()
