"""Actual HTTP sessions, scope isolation, whitelist projections and persistence."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from chengkong.web import SimulationApp,make_server
ROOT=Path(__file__).resolve().parents[1]
class PortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=SimulationApp(ROOT/'examples/park_crane_synthetic.json',Path(self.tmp.name)/'db')
        self.server=make_server(self.app,port=0,demo_identities=True)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}';self.cookie='';self.sid='end_unconfirmed'
        self.addCleanup(self.stop)
    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join(5)
    def call(self,path,body=None,status=200,headers=None):
        h={'Content-Type':'application/json','X-KLT-Request':'1','Cookie':self.cookie};h.update(headers or {})
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:r=urllib.request.urlopen(req,timeout=20)
        except urllib.error.HTTPError as e:r=e
        with r:
            b=r.read();self.assertEqual(r.status,status,b.decode()[:500])
            if r.headers.get('Set-Cookie'):self.cookie=r.headers['Set-Cookie'].split(';')[0]
            return json.loads(b) if 'json' in r.headers.get('Content-Type','') else b.decode()
    def login(self,id):return self.call('/api/session/demo',{'identity':id})
    def context(self):return self.call('/api/portal/context?scenario='+self.sid)
    def test_private_api_and_exports_require_server_identity_and_organization(self):
        self.call('/api/state',status=401);self.call('/api/export?role=biz&organization_id=park_operator',status=401)
        self.login('resident');public=self.context()
        for p in ('/api/state','/api/export','/api/template','/api/view','/api/operations','/api/replay?id=1'):
            self.call(p,status=403)
        self.call('/api/adopt',{'candidate_id':'x','role':'biz','organization_id':'park_operator'},403)
        self.assertNotIn('result',public);self.assertNotIn('tasks',json.dumps(public));self.assertNotIn('candidate',json.dumps(public))
        self.login('other_operator');self.assertEqual(self.call('/api/portal/scenarios')['scenarios'],[]);self.call('/api/state',status=403)
        self.login('reviewer');gov=self.context();self.assertEqual({t['id'] for t in gov['shared_tasks']},{'task_1','task_2'})
        self.assertNotIn('task_3',json.dumps(gov));self.assertNotIn('stability_cost',json.dumps(gov))
        self.call('/api/adopt',{'candidate_id':'x'},403);self.call('/api/event',{'event':{}},403)
    def test_publication_updates_dedupe_and_personal_records_are_private(self):
        self.login('resident');initial=self.context();self.call('/api/personal/follow',{'scenario':self.sid,'following':True})
        resident=self.cookie
        self.login('operator')
        self.call('/api/event',{'scenario':self.sid,'event':{'id':'private_notice','kind':'planned_end_changed','activity_id':'lifting_1','observed_at_s':60,'effective_at_s':60,'new_planned_end_s':610,'source_kind':'PRIVATE_SOURCE_DO_NOT_PUBLISH'}})
        self.login('resident');records=self.call('/api/personal/records');self.assertEqual(len(records['notifications']),1)
        self.assertNotIn('PRIVATE_SOURCE',json.dumps(self.context()))
        self.assertGreater(self.context()['records'][0]['public_version'],initial['records'][0]['public_version'])
        self.assertEqual(len(self.call('/api/personal/records')['notifications']),1)
        n=records['notifications'][0]['id'];self.call('/api/personal/read',{'id':n})
        feedback=self.call('/api/personal/feedback',{'scenario':self.sid,'text':'仅本人可见的线索'})
        duplicate=self.call('/api/personal/feedback',{'scenario':self.sid,'text':'仅本人可见的线索'})
        self.assertEqual(feedback['id'],duplicate['id']);self.assertFalse(feedback['world_updated'])
        self.call('/api/personal/query',{'scenario':self.sid,'from_s':900,'to_s':1000})
        me=self.call('/api/personal/records');self.assertTrue(me['notifications'][0]['is_read']);self.assertEqual(me['queries'][0]['result']['coverage'],'outside_published_horizon')
        self.login('resident_b');other=self.call('/api/personal/records');self.assertEqual(other['feedback'],[]);self.assertEqual(other['notifications'],[])
        self.call('/api/personal/read',{'id':n},404)
        # Old session token was invalidated when identity switched.
        self.call('/api/personal/records',status=401,headers={'Cookie':resident})
    def test_government_note_is_not_a_fact_and_report_is_scoped_escaped_snapshot(self):
        self.login('reviewer');c=self.context();before=copy.deepcopy(self.app._session(self.sid)['store'].current_world())
        note=self.call('/api/gov/notes',{'scenario':self.sid,'record_id':c['changes'][0]['id'],'text':'已核对来源，但未确认结束','status':'reviewed'})
        self.assertFalse(note['fact_updated']);self.assertEqual(before,self.app._session(self.sid)['store'].current_world())
        self.assertEqual(self.context()['notes'][0]['id'],note['id'])
        report=self.call('/api/reports',{'scenario':self.sid,'title':'<script>bad()</script>'})
        document=self.call('/api/report?id='+report['id']+'&format=html');self.assertNotIn('<script>',document);self.assertIn('&lt;script&gt;',document)
        self.assertEqual(self.call('/api/report?id='+report['id']),report)
        self.login('operator');self.call('/api/report?id='+report['id'],status=404)
        self.login('resident');self.call('/api/report?id='+report['id'],status=403)
    def test_cookie_rotation_csrf_and_normal_mode_demo_disabled(self):
        self.call('/api/session/demo',{'identity':'operator'},403,{'Origin':'http://attacker.invalid'})
        self.call('/api/session/demo',{'identity':'operator'},403,{'X-KLT-Request':''})
        self.login('operator');self.call('/api/state',status=403,headers={'Host':'attacker.invalid'})
        self.server.portal.demo=False;self.call('/api/session/demo',{'identity':'reviewer'},403)
    def test_import_stale_adoption_and_successful_plan_reservation_transaction(self):
        self.login('operator');initial=self.context();candidate=initial['result']['candidates'][0];oldres=initial['reservations']['reservation_version']
        review=self.call('/api/import',{'content':self.app.template()});self.assertTrue(review['outcome']['proposal_id'])
        result=self.call('/api/import/apply',{'proposal_id':review['outcome']['proposal_id']});self.assertTrue(result['outcome']['world_updated'])
        rejected=self.call('/api/adopt',{'candidate_id':candidate['id']});self.assertFalse(rejected['outcome']['adopted'])
        current=self.context();candidate=current['result']['candidates'][0]
        adopted=self.call('/api/adopt',{'candidate_id':candidate['id']});self.assertTrue(adopted['outcome']['adopted'])
        current=self.context();self.assertEqual(current['reservations']['reservation_version'],current['result']['world']['reservation_version']);self.assertGreater(current['reservations']['reservation_version'],oldres)
        for t in current['result']['world']['tasks']:self.assertEqual(t['plan_version'],current['versions']['plan_versions'][t['id']])
        before=copy.deepcopy(current['result']['world']);h=current['history'][0]['id'];self.call('/api/portal/replay?id='+str(h));self.assertEqual(self.context()['result']['world'],before)
    def test_public_data_and_personal_state_survive_actual_server_restart(self):
        self.login('resident');self.call('/api/personal/follow',{'scenario':self.sid,'following':True})
        f=self.call('/api/personal/feedback',{'scenario':self.sid,'text':'重启后保留'})
        self.stop();self.app=SimulationApp(ROOT/'examples/park_crane_synthetic.json',Path(self.tmp.name)/'db')
        self.server=make_server(self.app,port=0,demo_identities=True);self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start();self.base=f'http://127.0.0.1:{self.server.server_port}'
        records=self.call('/api/personal/records');self.assertEqual(records['feedback'][0]['id'],f['id']);self.assertEqual(records['follows'],[self.sid])
        self.login('operator');self.assertEqual(self.context()['result']['world']['activities'][0]['completion_status'],'not_confirmed')

    def test_supplied_candidate_versions_are_checked_and_cannot_mutate_plan(self):
        self.login('operator');before=self.context();candidate=before['result']['candidates'][0]
        refused=self.call('/api/adopt',{'candidate_id':candidate['id'],'base_environment_version':999,
            'base_reservation_version':candidate['base_reservation_version'],'base_plan_versions':candidate['base_plan_versions']})
        self.assertEqual(refused['outcome']['status'],'client_version_mismatch');self.assertFalse(refused['outcome']['adopted'])
        self.assertEqual(before['result']['world'],self.context()['result']['world'])

    def test_private_new_object_is_not_published_and_shared_prefix_does_not_leak(self):
        self.login('operator');self.call('/api/advance',{'scenario':self.sid,'time_s':120})
        self.call('/api/event',{'scenario':self.sid,'event':{'id':'private-new','kind':'facility_added','effective_at_s':120,'observed_at_s':120,'source_kind':'private-source',
            'facility':{'id':'PRIVATE_FACILITY','physical_geometry':[{'type':'aabb','min_m':[1,1,0],'max_m':[3,3,1]}]}}})
        self.login('reviewer');gov=self.call('/api/portal/view?scenario=end_unconfirmed&time_s=180');self.assertNotIn('task_3',json.dumps(gov));self.assertIn('task_1',gov['executed_prefixes'])
        self.login('resident');public=self.context();self.assertNotIn('PRIVATE_FACILITY',json.dumps(public));self.assertNotIn('executed_prefixes',json.dumps(public))
        self.call('/api/portal/view?scenario=end_unconfirmed&time_s=180',status=403)

    def test_closed_demo_mode_rejects_previously_valid_cookie_after_restart(self):
        self.login('operator');self.context();self.stop()
        self.app=SimulationApp(ROOT/'examples/park_crane_synthetic.json',Path(self.tmp.name)/'db')
        self.server=make_server(self.app,port=0,demo_identities=False);self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start();self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.call('/api/state',status=401);self.call('/api/session/demo',{'identity':'operator'},403)
        self.assertEqual(self.call('/api/session')['identities'],[])

    def test_presentation_moving_protection_has_hand_calculable_pose_and_preview_is_read_only(self):
        from chengkong.world import load_example,visible_world
        from chengkong.presentation import spatial_view
        w=visible_world(load_example(ROOT/'examples/motion_crane_synthetic.json'),'end_unconfirmed',60)
        before=copy.deepcopy(w);view=spatial_view(w,180)
        boom=next(o for o in view['objects'] if o['component']=='activity_protection' and ':boom:' in o['constraint_id'])
        # Program linearly rotates -90 to +90 over [120,240]: at180 it points east.
        self.assertEqual(boom['geometry']['start_m'],[500,400,56])
        self.assertEqual(boom['geometry']['end_m'],[560,400,56])
        self.assertEqual(boom['geometry']['radius_m'],7.5) # radius1.5 + clearance6
        self.assertEqual(w,before);self.assertEqual(view['known_as_of_s'],60)
        self.assertTrue(all(not p for p in view['executed_prefixes'].values()))

    def test_historical_effective_time_preview_uses_then_known_world(self):
        self.login('operator');before=self.context();history_id=before['history'][0]['id']
        self.call('/api/advance',{'scenario':self.sid,'time_s':360})
        current=self.app._session(self.sid)['store'].current_world()
        viewed=self.call('/api/portal/replay?scenario=end_unconfirmed&id='+str(history_id)+'&time_s=400')
        self.assertEqual(viewed['spatial_view']['known_as_of_s'],60)
        self.assertEqual(viewed['spatial_view']['view_time_s'],400)
        self.assertEqual(self.app._session(self.sid)['store'].current_world(),current)

    def test_revocation_survives_projection_refresh_and_redacts_inbox_and_saved_queries(self):
        self.login('resident');self.context()
        self.call('/api/personal/follow',{'scenario':self.sid,'following':True})
        self.call('/api/personal/query',{'scenario':self.sid,'from_s':60,'to_s':600})
        control={'scenario':self.sid,'object_id':'lifting_1','visible':False,'reason':'撤下示例摘要，待重新核对'}
        self.call('/api/gov/publication',control,403)
        self.login('operator')
        self.call('/api/event',{'scenario':self.sid,'event':{'id':'notice','kind':'planned_end_changed','activity_id':'lifting_1','observed_at_s':60,'effective_at_s':60,'new_planned_end_s':610,'source_kind':'test'}})
        self.call('/api/gov/publication',control,403)
        before=copy.deepcopy(self.context()['result']['world'])
        self.login('reviewer');self.call('/api/gov/publication',{**control,'object_id':'new-private-object','visible':True},400)
        self.assertFalse(self.call('/api/gov/publication',control)['world_updated'])
        self.assertEqual(self.app._session(self.sid)['store'].current_world(),before)
        self.login('resident')
        for _ in range(2):self.assertEqual(self.context()['records'],[])
        public=self.context();self.assertNotIn('lifting_1',json.dumps(public['spatial_view']['objects']))
        self.assertTrue(any(o['id']=='crane_1' for o in public['spatial_view']['objects']))
        records=self.call('/api/personal/records');self.assertEqual(records['notifications'][0]['status'],'revoked')
        self.assertIsNone(records['notifications'][0]['record']);self.assertEqual(records['queries'][0]['result']['records'],[])
        self.assertEqual(records['queries'][0]['result']['revoked_record_count'],1)
        self.stop();self.app=SimulationApp(ROOT/'examples/park_crane_synthetic.json',Path(self.tmp.name)/'db')
        self.server=make_server(self.app,port=0,demo_identities=True);self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start();self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.context()['records'],[])
        self.login('reviewer');self.call('/api/gov/publication',{**control,'visible':True,'reason':'恢复原已获准范围'})
        self.login('resident');self.assertEqual(len(self.context()['records']),1)
        records=self.call('/api/personal/records');self.assertEqual(len(records['notifications']),2)
