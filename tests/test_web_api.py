"""Real loopback HTTP round trips through calculation, evidence and SQLite.

Each case uses port 0 and an isolated database; never touches a running demo.
These checks exercise browser API endpoints, not a rendered-browser UI runner.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from chengkong.analysis import check_task
from chengkong.web import SimulationApp, make_server

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/park_crane_synthetic.json'


class WebAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = SimulationApp(EXAMPLE, Path(self.temp.name) / 'db',
                                 model_dir=Path(self.temp.name) / 'missing_models', project_root=self.temp.name)
        self.server = make_server(self.app, port=0, demo_identities=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.stop_server)
        self.cookie = ''
        self.request('/api/session/demo', {'identity': 'operator'})

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, body=None, expected=200):
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={'Content-Type': 'application/json', 'X-KLT-Request':'1', 'Cookie':self.cookie})
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            if response.headers.get('Set-Cookie'): self.cookie=response.headers['Set-Cookie'].split(';')[0]
            self.assertEqual(response.status, expected, response.read().decode() if response.status != expected else '')
            payload = response.read()
            return json.loads(payload) if 'application/json' in response.headers.get('Content-Type', '') else payload.decode()

    def test_static_ui_and_state_use_live_backend_without_optional_models(self):
        health = self.request('/health')
        self.assertEqual(health, {'status': 'ok', 'flight_control_connected': False})
        self.assertIn('空路通', self.request('/'))
        self.assertIn('/assets/', self.request('/biz/maintenance'))
        self.request('/api/missing-route', expected=404)
        scenarios = self.request('/api/scenarios')
        self.assertEqual(scenarios['data_kind'], 'synthetic')
        self.assertEqual(set(scenarios['scenarios']), {'on_time_confirmed', 'delayed_confirmed', 'end_unconfirmed'})
        state = self.request('/api/state')
        result = state['result']
        self.assertEqual(result['world']['as_of_s'], 60)
        self.assertNotIn('scenario_definitions_for_simulator_only', result['world'])
        for task in result['world']['tasks']:
            expected = check_task(task, result['constraints'], result['world'])
            self.assertEqual(result['baseline_analysis'][task['id']], expected)
        self.assertTrue(result['candidates'])
        self.assertEqual(state['model_load_status']['status'], 'not_loaded')
        learned = self.request('/api/analyze', {'learned': True})
        self.assertFalse(learned['result']['learning_enabled'])
        self.assertEqual(learned['result']['baseline_analysis'], result['baseline_analysis'])
        with self.assertRaisesRegex(ValueError, 'only_loopback'):
            make_server(self.app, host='0.0.0.0', port=0)

    def test_event_invalidates_old_candidate_and_new_candidate_requires_post_adoption(self):
        initial = self.request('/api/state')
        original = deepcopy(initial['result']['world'])
        old = next(c for c in initial['result']['candidates'] if c['review']['feasible'])
        event = {'id': 'http_extra_facility', 'kind': 'facility_added', 'observed_at_s': 60,
                 'effective_at_s': 60, 'source_kind': 'synthetic_web_test',
                 'facility': {'id': 'extra_facility', 'physical_geometry': [
                     {'type': 'aabb', 'min_m': [10, 10, 0], 'max_m': [12, 12, 3]}]}}
        changed = self.request('/api/event', {'event': event})
        changed_world = changed['result']['world']
        self.assertEqual(changed_world['environment_version'], original['environment_version'] + 1)
        self.assertEqual(changed_world['tasks'], original['tasks'])
        self.assertIn(old['id'], changed['result']['incremental']['invalidated_candidate_ids'])
        self.assertTrue(any(old['id'] in candidates for candidates in
                            changed['result']['incremental']['dependencies']['task_candidates'].values()))
        stale = self.request('/api/adopt', {'candidate_id': old['id']})
        self.assertFalse(stale['outcome']['adopted'])
        self.assertEqual(stale['outcome']['status'], 'needs_recheck')
        self.assertIn('base_environment_version', stale['outcome']['review']['version_mismatches'])
        self.assertEqual(stale['state']['result']['world'], changed_world)
        fresh = next(c for c in changed['result']['candidates'] if c['review']['feasible'])
        accepted = self.request('/api/adopt', {'candidate_id': fresh['id']})
        self.assertTrue(accepted['outcome']['adopted'])
        adopted_world = accepted['state']['result']['world']
        self.assertIn(fresh['id'], accepted['state']['result']['incremental']['invalidated_candidate_ids'])
        self.assertEqual(adopted_world['reservation_version'], changed_world['reservation_version'] + 1)
        self.assertNotEqual(adopted_world['tasks'], changed_world['tasks'])
        repeated = self.request('/api/adopt', {'candidate_id': fresh['id']})
        self.assertFalse(repeated['outcome']['adopted'])
        self.assertEqual(repeated['state']['result']['world'], adopted_world)

    def test_time_release_and_history_replay_preserve_original_knowledge(self):
        initial = self.request('/api/state?scenario=on_time_confirmed')
        scenario = 'on_time_confirmed'
        first_history_id = initial['history'][0]['id']
        decisions_before = deepcopy(self.app.sessions[scenario]['store'].decisions())
        advanced = self.request('/api/advance', {'scenario': scenario, 'time_s': 360})
        world = advanced['result']['world']
        self.assertEqual(world['activities'][0]['completion_confirmed_at_s'], 360)
        self.assertTrue(world['facilities'][0]['installed'])
        self.assertEqual(advanced['result']['baseline_analysis']['task_2']['status'], 'clear')
        replay = self.request(f'/api/replay?scenario={scenario}&id={first_history_id}')
        self.assertTrue(replay['read_only'])
        self.assertEqual(replay['result']['world']['as_of_s'], 60)
        self.assertEqual(replay['result']['world']['activities'][0]['completion_status'], 'not_confirmed')
        current = self.request('/api/state?scenario=on_time_confirmed')
        self.assertEqual(current['result']['world'], world)
        self.assertEqual(self.app.sessions[scenario]['store'].decisions()[0], decisions_before[0])
        self.assertEqual(initial['result']['world']['activities'][0]['completion_status'], 'not_confirmed')

    def test_evidence_feedback_and_server_budget_preserve_unknown_on_failure(self):
        state = self.request('/api/state')
        request_id = state['result']['evidence_requests'][0]['id']
        unknown = self.request('/api/evidence', {'request_id': request_id,
                               'source': {'id': 'empty_log', 'kind': 'available_records', 'records': []}})
        self.assertEqual(unknown['outcome']['status'], 'unknown')
        self.assertEqual(unknown['state']['result']['world'], state['result']['world'])
        exhausted = self.request('/api/evidence', {'request_id': request_id, 'queries_used': 0})
        self.assertEqual(exhausted['outcome']['status'], 'budget_exhausted')
        advanced = self.request('/api/advance', {'time_s': 360})
        # Same environment version alone is insufficient to reset a request's
        # ledger. Choose the other source-layer request, which has not been used.
        fresh_request = next(r for r in advanced['result']['evidence_requests'] if r['source_layer'] == 'maintenance_decision')
        source = {'id': 'explicit_site_log', 'kind': 'synthetic_evidence_source', 'records': [{
            'id': 'completion_received_now', 'kind': 'activity_completion_confirmed', 'activity_id': 'lifting_1',
            'observed_at_s': 360, 'effective_at_s': 360, 'source_kind': 'synthetic_confirmation'}]}
        result = self.request('/api/evidence', {'request_id': fresh_request['id'], 'source': source})
        self.assertEqual(result['outcome']['status'], 'resolved')
        updated = result['state']['result']
        self.assertEqual(updated['world']['environment_version'], advanced['result']['world']['environment_version'] + 1)
        self.assertEqual(updated['world']['activities'][0]['completion_confirmed_at_s'], 360)
        self.assertTrue(updated['world']['facilities'][0]['installed'])
        self.assertEqual(updated['baseline_analysis']['task_2']['status'], 'clear')
        self.assertEqual(updated['world']['tasks'], advanced['result']['world']['tasks'])
        self.assertEqual(updated['world']['event_log'][-1]['event']['evidence_refs'], ['explicit_site_log'])

    def test_mock_material_is_a_proposal_until_explicit_acceptance(self):
        initial = self.request('/api/state')
        text = '计划结束时间调整为600秒，不代表实际完成。'
        event = {'id': 'material_delay', 'kind': 'planned_end_changed', 'activity_id': 'lifting_1',
                 'new_planned_end_s': 600, 'effective_at_s': 60, 'observed_at_s': 60, 'source_kind': 'mock'}
        response = {'schema_version': 'chengkong-extraction-v1', 'candidate_events': [
            {'event': event, 'evidence_refs': [{'record_id': 'schedule_record', 'locator': 'line:1', 'quote': text}]}]}
        parsed = self.request('/api/material', {'records': [{'id': 'schedule_record', 'kind': 'text',
                              'observed_at_s': 60, 'text': text}], 'mock_response': response})
        self.assertEqual(parsed['outcome']['status'], 'proposals_ready')
        self.assertFalse(parsed['outcome']['real_service_connected'])
        self.assertEqual(parsed['state']['result']['world'], initial['result']['world'])
        proposal = parsed['outcome']['proposals'][0]
        accepted = self.request('/api/material/apply', {'proposal_id': proposal['id']})
        self.assertTrue(accepted['outcome']['world_updated'])
        self.assertEqual(accepted['state']['result']['world']['activities'][0]['planned_interval_s'][1], 600)
        self.assertEqual(accepted['state']['result']['world']['activities'][0]['completion_status'], 'not_confirmed')
        self.assertTrue(accepted['state']['agent_audit'])
        world = accepted['state']['result']['world']
        expected_refs = response['candidate_events'][0]['evidence_refs']
        self.assertEqual(world['event_log'][-1]['event']['evidence_refs'], expected_refs)
        self.assertEqual(world['known_sources'][-1]['evidence_refs'], expected_refs)

    def test_image_without_scale_cannot_introduce_nested_metric_geometry(self):
        initial = self.request('/api/state')
        envelope = {'type': 'vertical_cylinder', 'center_xy_m': [100, 100],
                    'radius_m': 10, 'z_min_m': 0, 'z_max_m': 50}
        events = [
            {'id': 'unscaled_new_activity', 'kind': 'activity_started',
             'activity': {'id': 'new_work', 'facility_id': 'crane_1', 'planned_interval_s': [60, 300],
                          'activity_envelope': envelope}},
            {'id': 'unscaled_pause_envelope', 'kind': 'activity_paused', 'activity_id': 'lifting_1',
             'activity_envelope': envelope, 'effective_at_s': 120},
        ]
        for event in events:
            event.update(observed_at_s=60, source_kind='mock')
            event.setdefault('effective_at_s', 60)
            response = {'schema_version': 'chengkong-extraction-v1', 'candidate_events': [
                {'event': event, 'evidence_refs': [{'record_id': 'unscaled_image', 'locator': 'pixel:0,0,100,100'}]}]}
            with self.subTest(kind=event['kind']):
                result = self.request('/api/material', {'records': [{'id': 'unscaled_image', 'kind': 'image',
                                      'observed_at_s': 60}], 'mock_response': response})
                self.assertEqual(result['outcome']['status'], 'no_valid_proposals')
                self.assertTrue(any('scale_or_coordinate_reference' in r['reason'] for r in result['outcome']['rejected']))
                self.assertEqual(result['state']['result']['world'], initial['result']['world'])

    def test_malformed_evidence_and_http_body_are_structurally_rejected(self):
        initial = self.request('/api/state')
        event = {'id': 'malformed_reference', 'kind': 'planned_end_changed', 'activity_id': 'lifting_1',
                 'new_planned_end_s': 600, 'effective_at_s': 60, 'observed_at_s': 60, 'source_kind': 'mock'}
        result = self.request('/api/material', {
            'records': [{'id': 'note', 'kind': 'text', 'observed_at_s': 60, 'text': 'planned change'}],
            'mock_response': {'schema_version': 'chengkong-extraction-v1', 'candidate_events': [
                {'event': event, 'evidence_refs': [42]}]}})
        self.assertEqual(result['outcome']['status'], 'no_valid_proposals')
        self.assertTrue(result['outcome']['rejected'])
        self.assertEqual(result['state']['result']['world'], initial['result']['world'])
        malformed = self.request('/api/event', [], expected=400)
        self.assertEqual(malformed['status'], 'request_rejected')
        self.assertEqual(self.request('/api/state')['result']['world'], initial['result']['world'])

    def test_material_agent_requests_share_budget_and_existing_feedback_endpoint(self):
        initial = self.request('/api/advance', {'time_s': 360})
        body = {'records': [{'id': 'incomplete_log', 'kind': 'text', 'observed_at_s': 360,
                             'text': '尚缺作业完成确认。'}],
                'mock_response': {'schema_version': 'chengkong-extraction-v1', 'candidate_events': [],
                                  'unknown_fields': ['lifting_1.completion_status', 'unidentified.height_m']}}
        prepared = self.request('/api/material', body)
        requests = prepared['outcome']['evidence_requests']
        self.assertTrue(requests)
        self.assertTrue(all(r['initiator'] == 'material_agent' for r in requests))
        self.assertEqual({r['id'] for r in requests}, {r['id'] for r in initial['result']['evidence_requests']})
        self.assertEqual(len(prepared['state']['result']['evidence_requests']), len(requests))
        self.assertEqual(prepared['state']['result']['world'], initial['result']['world'])
        first = next(r for r in requests if r['source_layer'] == 'conflict_analysis')
        unknown = self.request('/api/evidence', {'request_id': first['id']})
        self.assertEqual(unknown['outcome']['status'], 'unknown')
        self.request('/api/material', body)
        exhausted = self.request('/api/evidence', {'request_id': first['id']})
        self.assertEqual(exhausted['outcome']['status'], 'budget_exhausted')
        self.assertEqual(exhausted['state']['result']['world'], initial['result']['world'])
        second = next(r for r in requests if r['source_layer'] == 'maintenance_decision')
        source = {'id': 'agent_supplied_log', 'kind': 'synthetic_evidence_source', 'records': [{
            'id': 'agent_confirmation', 'kind': 'activity_completion_confirmed', 'activity_id': 'lifting_1',
            'effective_at_s': 360, 'observed_at_s': 360, 'source_kind': 'synthetic_confirmation'}]}
        resolved = self.request('/api/evidence', {'request_id': second['id'], 'source': source})
        self.assertEqual(resolved['outcome']['status'], 'resolved')
        world = resolved['state']['result']['world']
        self.assertEqual(world['environment_version'], initial['result']['world']['environment_version'] + 1)
        self.assertEqual(world['activities'][0]['completion_confirmed_at_s'], 360)
        self.assertTrue(world['facilities'][0]['installed'])
        self.assertEqual(world['tasks'], initial['result']['world']['tasks'])
        self.assertEqual(resolved['state']['result']['baseline_analysis']['task_2']['status'], 'clear')


if __name__ == '__main__':
    unittest.main()
