"""Independent temporary databases: stop/restart, budgets, binding and backup."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chengkong.web import SimulationApp,make_server
from chengkong.recovery import backup_directory,restore_directory,RuntimeLedger,PersistentServiceAdapter
from chengkong.world import advance_world

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'examples/park_crane_synthetic.json'


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/'db';self.scenario='end_unconfirmed'
    def app(self,**kwargs):
        app=SimulationApp(kwargs.pop('example',EXAMPLE),kwargs.pop('db_dir',self.db),project_root=ROOT,**kwargs)
        self.addCleanup(app.close);return app

    def test_normal_restart_preserves_adoption_reservations_events_pending_reviews_and_budgets(self):
        app=self.app();initial=app.state(self.scenario)
        candidate=next(c for c in initial['result']['candidates'] if c['review']['feasible'])
        self.assertTrue(app.adopt(self.scenario,candidate['id'])['outcome']['adopted'])
        app.advance(self.scenario,360)
        field=app.field_query(self.scenario,'lifting_1','completion')
        self.assertEqual(field['outcome']['status'],'unknown')
        material=app.import_material(self.scenario,app.template())
        proposal=material['outcome']['proposal_id'];self.assertTrue(proposal)
        records=[{'id':'restart_notice','kind':'text','observed_at_s':360,'text':'合成：计划结束调整为600秒。'}]
        response={'schema_version':'chengkong-extraction-v1','candidate_events':[{
            'event':{'id':'restart_parsed','kind':'planned_end_changed','activity_id':'lifting_1',
                     'observed_at_s':360,'effective_at_s':360,'source_kind':'synthetic_record','new_planned_end_s':600},
            'evidence_refs':[{'record_id':'restart_notice','locator':'text:0','quote':'计划结束调整为600秒'}]}]}
        prepared=app.material(self.scenario,records,response)
        self.assertTrue(prepared['outcome']['proposals'])
        before=app.state(self.scenario);reservations=app._session(self.scenario)['store'].current_reservations()
        history=app._session(self.scenario)['store'].history();export=app.export(self.scenario)
        app.close()
        restored=self.app();after=restored.state(self.scenario)
        self.assertEqual(restored.active_scenario,self.scenario)
        self.assertEqual(after['recovery_status']['status'],'restored')
        for key in ('result','saved_candidates','evidence_audit','agent_audit','import_audit','field_audit','field_requests','history'):
            self.assertEqual(after[key],before[key],key)
        self.assertEqual(restored._session(self.scenario)['store'].current_reservations(),reservations)
        self.assertEqual(restored._session(self.scenario)['store'].history(),history)
        self.assertEqual(restored.export(self.scenario)['pending_workflows'],export['pending_workflows'])
        self.assertEqual(restored.field_query(self.scenario,'lifting_1','completion')['outcome']['status'],'budget_exhausted')
        applied=restored.accept_import(self.scenario,proposal)
        self.assertTrue(applied['outcome']['world_updated'])
        stale=restored.accept_material(self.scenario,prepared['outcome']['proposals'][0]['id'])
        self.assertEqual(stale['outcome']['status'],'needs_recheck')
        self.assertFalse(restored.adopt(self.scenario,candidate['id'])['outcome']['adopted'])
        self.assertEqual(restored.replay(self.scenario,history[0]['id'])['result']['world'],initial['result']['world'])

    def test_active_branch_and_completion_query_budget_survive_restart(self):
        app=self.app();app.advance(self.scenario,360)
        state=app.state(self.scenario);request=state['result']['evidence_requests'][0]
        answer=app.evidence(self.scenario,request['id'])
        self.assertEqual(answer['outcome']['status'],'unknown')
        app.state('delayed_confirmed');app.close()
        restored=self.app();self.assertEqual(restored.active_scenario,'delayed_confirmed')
        answer=restored.evidence(self.scenario,request['id'])
        self.assertEqual(answer['outcome']['status'],'budget_exhausted')
        self.assertEqual(answer['state']['result']['world']['as_of_s'],360)

    def test_initial_time_and_policy_apply_only_to_new_session(self):
        raw=json.loads(EXAMPLE.read_text());raw['observed_snapshot']['as_of_s']=0
        source=self.root/'time-zero.json';source.write_text(json.dumps(raw))
        policy={'mode':'conservative','action_order':['retain','detour','delay']}
        app=self.app(example=source,initial_policy=policy)
        world=app._session(self.scenario)['store'].current_world()
        self.assertEqual(world['as_of_s'],0);self.assertEqual(world['planning_policy'],policy)
        app.close()
        restored=self.app(example=source,initial_policy={'mode':'dynamic_windows','action_order':['retain','delay','detour']})
        session=restored._session(self.scenario)
        self.assertEqual(session['store'].current_world(),world)
        self.assertEqual(session['recovery_status']['initial_policy_status'],'restored_policy_preserved')

    def test_checkpoint_failure_rolls_back_and_revision_mismatch_invalidates_cache(self):
        app=self.app();app.state(self.scenario);session=app._session(self.scenario);store=session['store']
        saved=store.load_checkpoint(app.binding)
        payload=copy.deepcopy(saved['payload']);payload['field_audit'].append({'status':'injected'})
        with patch.object(store,'_after_checkpoint_write',side_effect=RuntimeError('injected checkpoint failure')):
            with self.assertRaisesRegex(RuntimeError,'injected'):
                store.checkpoint(app.binding,payload,saved['state_revision'])
        self.assertEqual(store.load_checkpoint(app.binding),saved)
        world=store.current_world();updated=advance_world(world,61,[]);store.replace_world(updated,world['state_revision'])
        # Simulate termination between world commit and checkpoint: no normal close.
        app.closed=True;app.instance_lock.close()
        restored=self.app();state=restored.state(self.scenario)
        self.assertEqual(state['recovery_status']['status'],'restored_with_result_invalidated')
        self.assertEqual(state['result']['world']['as_of_s'],61)
        self.assertNotEqual(state['result']['world'],saved['payload']['result']['world'])

    def test_changed_or_missing_input_and_second_instance_are_rejected(self):
        local=self.root/'input.json';shutil.copy2(EXAMPLE,local)
        app=self.app(example=local);app.state(self.scenario)
        with self.assertRaisesRegex(ValueError,'already_in_use'):self.app(example=local)
        app.close();local.write_text(local.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'binding_mismatch:input_sha256'):self.app(example=local)
        local.unlink()
        with self.assertRaisesRegex(ValueError,'input_missing'):self.app(example=local)
        # An identical copy at another path is valid; identity is content-based.
        shutil.copy2(EXAMPLE,local)
        self.assertEqual(self.app(example=local).state(self.scenario)['recovery_status']['status'],'restored')

    def test_foreign_scene_checkpoint_rejects_repeated_http_requests_without_cache_bypass(self):
        original=self.app();original.state(self.scenario);original.close()
        other_input=self.root/'other-input.json';other_input.write_text(EXAMPLE.read_text()+'\n')
        foreign=self.app(example=other_input,db_dir=self.root/'foreign')
        foreign.state(self.scenario);foreign.close()
        shutil.copy2(self.root/'foreign'/(self.scenario+'.sqlite3'),self.db/(self.scenario+'.sqlite3'))
        app=self.app();server=make_server(app,port=0)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        try:
            for attempt in range(2):
                with self.subTest(attempt=attempt),self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/state',timeout=5)
                with raised.exception as response:
                    self.assertEqual(response.code,400)
                    self.assertIn('recovery_binding_mismatch:input_sha256',json.load(response)['reason'])
                self.assertNotIn(self.scenario,app.sessions)
        finally:server.shutdown();server.server_close();thread.join(timeout=5)

    def test_missing_or_changed_model_cannot_silently_become_rule_session(self):
        model=self.root/'model';model.mkdir();(model/'weights.bin').write_bytes(b'test-only-model-identity')
        manifest={'model_version':'test-only-v1','artifacts':{'weights.bin':{'sha256':hashlib.sha256((model/'weights.bin').read_bytes()).hexdigest()}}}
        (model/'manifest.json').write_text(json.dumps(manifest))
        with patch('chengkong.model_runtime.ModelBundle',return_value=SimpleNamespace(version='test-only-v1')):
            app=self.app(model_dir=model);app._session(self.scenario);app.close()
        with self.assertRaisesRegex(ValueError,'binding_mismatch:model'):self.app()
        with self.assertRaisesRegex(ValueError,'binding_mismatch:model'):self.app(model_dir=self.root/'missing')
        (model/'weights.bin').write_bytes(b'changed')
        with patch('chengkong.model_runtime.ModelBundle',return_value=SimpleNamespace(version='test-only-v1')):
            with self.assertRaisesRegex(ValueError,'checksum_mismatch'):self.app(model_dir=model)
        manifest['artifacts']['weights.bin']['sha256']=hashlib.sha256(b'changed').hexdigest()
        (model/'manifest.json').write_text(json.dumps(manifest))
        with patch('chengkong.model_runtime.ModelBundle',return_value=SimpleNamespace(version='test-only-v1')):
            with self.assertRaisesRegex(ValueError,'binding_mismatch:model'):self.app(model_dir=model)

    def test_backup_restore_integrity_no_overwrite_and_live_service_rejection(self):
        app=self.app();state=app.advance(self.scenario,120)
        backup=self.root/'backup';restored_dir=self.root/'restored'
        with self.assertRaisesRegex(ValueError,'already_in_use'):backup_directory(self.db,backup)
        app.close();manifest=backup_directory(self.db,backup)
        self.assertEqual(set(manifest['databases']),{'_runtime.sqlite3',self.scenario+'.sqlite3'})
        with self.assertRaisesRegex(ValueError,'must_not_exist'):backup_directory(self.db,backup)
        restore_directory(backup,restored_dir)
        with self.assertRaisesRegex(ValueError,'must_not_exist'):restore_directory(backup,restored_dir)
        restored=self.app(db_dir=restored_dir)
        self.assertEqual(restored.state(self.scenario)['result']['world'],state['result']['world'])
        restored.close()
        with (backup/(self.scenario+'.sqlite3')).open('ab') as stream:stream.write(b'corruption')
        with self.assertRaisesRegex(ValueError,'checksum_mismatch'):restore_directory(backup,self.root/'invalid')
        self.assertFalse((self.root/'invalid').exists())

    def test_external_service_budget_is_reserved_before_failure_and_never_stores_secret(self):
        ledger_path=self.root/'service.sqlite3';ledger=RuntimeLedger(ledger_path,{'test':'binding'})
        class FailingService:
            config={'provider':'structured_http','endpoint':'http://127.0.0.1/private?token=secret-test',
                    'model_id':'mock-local','api_key':'secret-test','max_calls':1}
            calls_used=0
            def parse(self,*_):raise TimeoutError('simulated process failure after reserved attempt')
        with self.assertRaises(TimeoutError):PersistentServiceAdapter(FailingService(),ledger).parse([], {})
        restored=RuntimeLedger(ledger_path,{'test':'binding'})
        self.assertEqual(PersistentServiceAdapter(FailingService(),restored).parse([], {})['status'],'budget_exhausted')
        self.assertNotIn(b'secret-test',ledger_path.read_bytes())


if __name__=='__main__':unittest.main()
