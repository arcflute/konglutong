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
