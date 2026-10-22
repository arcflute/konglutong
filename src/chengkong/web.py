"""Loopback-only HTTP application over the same computation and SQLite state.

No third party web framework, map token, remote hosting, or flight-control API.
"""
from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

from .world import load_example,visible_world,advance_world,apply_event
from .storage import PlanStore
from .pipeline import analyze_world
from .incremental import DependencyEngine
from .evidence import query_evidence,apply_evidence_result
from .multimodal import MaterialAgent,MockAdapter,configured_adapter
from .presentation import spatial_view,event_timeline,export_result


class SimulationApp:
    def __init__(self,example,db_dir,model_dir=None,project_root=None):
        self.raw=load_example(example)
        self.db_dir=Path(db_dir);self.db_dir.mkdir(parents=True,exist_ok=True)
        self.root=Path(project_root or Path(example).resolve().parent.parent)
        self.lock=threading.RLock();self.sessions={}
        self.service_adapter=None
        self.model_bundle=None;self.model_load_status={'status':'not_loaded'}
        if model_dir:
            try:
                from .model_runtime import ModelBundle
                self.model_bundle=ModelBundle(model_dir)
                self.model_load_status={'status':'loaded_synthetic','artifact_dir':str(model_dir)}
            except (OSError,ValueError,ImportError,KeyError) as exc:
                self.model_load_status={'status':'not_loaded','reason':str(exc)}
        self.scenarios={s['id']:s for s in self.raw['scenario_definitions_for_simulator_only']}

    def _session(self,scenario):
        if scenario not in self.scenarios:raise ValueError('unknown_scenario')
        if scenario not in self.sessions:
            store=PlanStore(self.db_dir/(scenario+'.sqlite3'))
            try:store.current_world()
            except ValueError:
                initial=visible_world(self.raw,scenario,60)
                initial['model_version']=self.model_bundle.version if self.model_bundle else 'none'
                store.initialize(initial)
            current=store.current_world()
            active_version=self.model_bundle.version if self.model_bundle else 'none'
            if current['model_version']!=active_version:
                updated=copy.deepcopy(current);updated['model_version']=active_version
                updated['environment_version']+=1
                store.replace_world(updated,current.get('state_revision',1))
            self.sessions[scenario]={'store':store,'engine':DependencyEngine(),'agent':MaterialAgent(),
                                     'result':None,'candidates':{},'evidence_results':[],'learned':False,'inflight':False,
                                     'importer':None,'import_audit':[],'field_audit':[],'field_requests':{}}
        return self.sessions[scenario]

    def _analyze(self,session):
        world=session['store'].current_world()
        result=analyze_world(world,engine=session['engine'],inflight=session['inflight'],
                             learned=session['learned'],model_bundle=self.model_bundle,
                             existing_candidates=list(session['candidates'].values()),
                             planning_budget={'max_orders':12,'max_searches':96,'max_expansions':10000,'timeout_s':5})
        for candidate in result['candidates']:
            session['store'].save_candidate(candidate)
            session['candidates'][candidate['id']]=copy.deepcopy(candidate)
        session['result']=result;session['store'].save_decision(result)
        return result

    def state(self,scenario,refresh=False):
        with self.lock:
            session=self._session(scenario)
            if refresh or session['result'] is None:self._analyze(session)
            # Only simulator-released events ever reach result.world. No raw
            # scenario definitions or future evidence are returned to the client.
            return {'result':session['result'],'saved_candidates':list(session['candidates'].values())[-20:],
                    'evidence_audit':session['evidence_results'],'agent_audit':session['agent'].audit,
                    'import_audit':session['import_audit'],'field_audit':session['field_audit'],
                    'field_requests':list(session['field_requests'].values()),
                    'spatial_view':spatial_view(session['result']['world']),
                    'timeline':event_timeline(session['result']['world'],session['store'].history()),
                    'model_load_status':self.model_load_status,
                    'history':[{'id':h['id'],'kind':h['kind'],'as_of_s':h.get('after',{}).get('as_of_s'),
                                'environment_version':h.get('after',{}).get('environment_version'),
                                'reservation_version':h.get('after',{}).get('reservation_version')}
                               for h in session['store'].history()]}

    def advance(self,scenario,time_s):
        with self.lock:
            session=self._session(scenario);world=session['store'].current_world()
            known={e['id'] for e in world['event_log']}
            events=[copy.deepcopy(e) for e in self.scenarios[scenario]['events']
                    if e['observed_at_s']<=time_s and e['id'] not in known]
            updated=advance_world(world,time_s,events)
            session['store'].replace_world(updated,world.get('state_revision',1))
            return self.state(scenario,True)

    def event(self,scenario,event):
        with self.lock:
            session=self._session(scenario);world=session['store'].current_world()
            updated=apply_event(world,event)
            session['store'].replace_world(updated,world.get('state_revision',1))
            return self.state(scenario,True)

    def adopt(self,scenario,identifier):
        with self.lock:
            session=self._session(scenario)
            outcome=session['store'].adopt(session['store'].get_candidate(identifier),explicit=True)
            return {'outcome':outcome,'state':self.state(scenario,outcome['adopted'])}

    def evidence(self,scenario,request_id,source=None,queries_used=0):
        with self.lock:
            session=self._session(scenario)
            if session['result'] is None:self._analyze(session)
            request=next((r for r in session['result']['evidence_requests'] if r['id']==request_id),None)
            if request is None:raise ValueError('evidence_request_not_found')
            world=session['store'].current_world()
            # The source is explicitly supplied records, not hidden branch truth.
            # Query counts are server-owned and cannot be reset by the client.
            used=sum(r.get('query_cost',0) for r in session['evidence_results'] if r.get('request_id')==request_id)
            result=query_evidence(world,request,evidence_source=source,queries_used=max(used,queries_used))
            session['evidence_results'].append(copy.deepcopy(result))
            updated=apply_evidence_result(world,result)
            changed=updated['environment_version']!=world['environment_version']
            if changed:session['store'].replace_world(updated,world.get('state_revision',1))
            return {'outcome':result,'state':self.state(scenario,changed)}

    def material(self,scenario,records,response=None):
        with self.lock:
            session=self._session(scenario)
            if response is not None:adapter=MockAdapter(response)
            else:
                if self.service_adapter is None:self.service_adapter=configured_adapter(self.root)
                adapter=self.service_adapter
            outcome=session['agent'].prepare(session['store'].current_world(),records,adapter)
            if session['result'] is None:self._analyze(session)
            existing={request['id']:request for request in session['result']['evidence_requests']}
            for request in outcome['evidence_requests']:
                if request.get('source_layer')=='field_validation':
                    session['field_requests'].setdefault(request['id'],copy.deepcopy(request))
                    continue
                previous=existing.get(request['id'])
                if previous is None:
                    existing[request['id']]=copy.deepcopy(request)
                else:
                    merged={**previous,**copy.deepcopy(request)}
                    # An Agent request reuses the same ledger, never replenishes
                    # its query budget or extends an existing request deadline.
                    merged['max_queries']=min(previous['max_queries'],request['max_queries'])
                    merged['cost_budget']=min(previous.get('cost_budget',previous['max_queries']),request['cost_budget'])
                    merged['deadline_s']=min(previous['deadline_s'],request['deadline_s'])
                    existing[request['id']]=merged
            session['result']['evidence_requests']=list(existing.values())
            session['store'].save_decision(session['result'])
            return {'outcome':outcome,'state':self.state(scenario)}

    def accept_material(self,scenario,proposal_id):
        with self.lock:
            session=self._session(scenario);world=session['store'].current_world()
            outcome,updated=session['agent'].apply(world,proposal_id,True)
            if outcome['world_updated']:session['store'].replace_world(updated,world.get('state_revision',1))
            return {'outcome':outcome,'state':self.state(scenario,outcome['world_updated'])}

    def replay(self,scenario,history_id):
        with self.lock:
            session=self._session(scenario)
            entry=next((h for h in session['store'].history() if h['id']==history_id),None)
            if entry is None:raise ValueError('history_not_found')
            world=entry['after']
            # Reproduce the recorded state; never write it over current state.
            return {'read_only':True,'history_entry':entry,
                    'spatial_view':spatial_view(world),
                    'timeline':event_timeline(world,[h for h in session['store'].history() if h['id']<=history_id]),
                    'result':analyze_world(world,planning_budget={'max_orders':0,'max_searches':0,'timeout_s':0})}

    def view(self,scenario,time_s=None):
        with self.lock:return spatial_view(self._session(scenario)['store'].current_world(),time_s)

    def export(self,scenario):
        with self.lock:
            session=self._session(scenario)
            if session['result'] is None:self._analyze(session)
            return export_result(session['result'],session['store'].history(),
                                 material_audit=session['import_audit'],field_audit=session['field_audit'])

    def compare(self,scenario):
        with self.lock:
            session=self._session(scenario);world=session['store'].current_world()
            if self.model_bundle is None:return {'status':'model_not_loaded','comparison':None}
            results={};budget={'max_orders':12,'max_searches':96,'max_expansions':10000,'timeout_s':5}
            for mode,learned in [('rule',False),('learned',True)]:
                started=time.perf_counter()
                result=analyze_world(world,inflight=session['inflight'],learned=learned,
                                     model_bundle=self.model_bundle if learned else None,planning_budget=budget)
                feasible=[c for c in result['candidates'] if c['review']['feasible']]
                results[mode]={'elapsed_s':time.perf_counter()-started,'planning_status':result['planning_status'],
                    'feasible_candidates':len(feasible),'learning_enabled':result['learning_enabled'],
                    'best_stability_cost':feasible[0]['stability_cost'] if feasible else None,
                    'search_diagnostics':result['search_diagnostics'],
                    'checking_schedule':result.get('checking_schedule'),'candidate_ids':[c['id'] for c in feasible]}
            return {'status':'compared','comparison':results,'budget':budget,'model_version':self.model_bundle.version,
                    'environment_version':world['environment_version'],'default_policy':'rule',
                    'scope':'one scene sequential wall-clock measurement; not a statistical speedup claim',
                    'current_plan_mutated':False}

    def import_material(self,scenario,content,format='json',metadata=None):
        from .imports import StructuredImporter
        with self.lock:
            session=self._session(scenario)
            if session['importer'] is None:session['importer']=StructuredImporter()
            outcome=session['importer'].prepare(session['store'].current_world(),content,format=format,metadata=metadata)
            session['import_audit'].append(copy.deepcopy(outcome))
            return {'outcome':outcome,'state':self.state(scenario)}

    def accept_import(self,scenario,proposal_id,resolutions=None):
        with self.lock:
            session=self._session(scenario);world=session['store'].current_world()
            if session['importer'] is None:raise ValueError('no_prepared_import')
            outcome,updated=session['importer'].accept(world,proposal_id,explicit=True,resolutions=resolutions)
            if outcome.get('world_updated'):
                session['store'].replace_world(updated,world.get('state_revision',1),task_additions=outcome.get('task_additions'))
            session['import_audit'].append(copy.deepcopy(outcome))
            return {'outcome':outcome,'state':self.state(scenario,bool(outcome.get('world_updated')))}

    def field_query(self,scenario,object_id=None,field=None,request_id=None,content=None,format='json',metadata=None):
        from .field_evidence import make_field_request,query_field_evidence
        with self.lock:
            session=self._session(scenario);world=session['store'].current_world()
            if request_id:
                request=session['field_requests'].get(request_id)
                if request is None:raise ValueError('field_request_not_found')
            else:
                request=make_field_request(world,object_id,field)
                if request['status']=='unsupported':return {'outcome':request,'state':self.state(scenario)}
                request=session['field_requests'].setdefault(request['id'],request)
            used=max([r.get('queries_used',0) for r in session['field_audit'] if r.get('request_id')==request['id']] or [0])
            review=None;records=None
            if content is not None:
                from .imports import StructuredImporter
                if session['importer'] is None:session['importer']=StructuredImporter()
                review=session['importer'].prepare(world,content,format=format,metadata=metadata)
                session['import_audit'].append(copy.deepcopy(review))
                if not review.get('proposal_id'):
                    return {'request':request,'outcome':{'status':'material_validation_failed','world_updated':False},'import_review':review,'state':self.state(scenario)}
                records=review['records']
            outcome=query_field_evidence(world,request,records=records,queries_used=used)
            session['field_audit'].append(copy.deepcopy(outcome))
            return {'request':request,'outcome':outcome,'import_review':review,'state':self.state(scenario)}

    def template(self):
        # A fixed, identified synthetic sample; never arbitrary filesystem reads.
        return json.loads((self.root/'examples/imports/construction_delay.json').read_text(encoding='utf-8'))


def make_server(app,host='127.0.0.1',port=8765):
    if host not in ('127.0.0.1','localhost','::1'):raise ValueError('only_loopback_listening_is_supported')
    static=Path(__file__).with_name('static')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def _json(self,value,status=200):
            data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def _origin_ok(self):
            origin=self.headers.get('Origin')
            if not origin:return True
            parsed=urllib.parse.urlparse(origin)
            return parsed.netloc==self.headers.get('Host') and parsed.scheme=='http'
        def do_GET(self):
            try:
                parsed=urllib.parse.urlparse(self.path);query=urllib.parse.parse_qs(parsed.query)
                scenario=query.get('scenario',['end_unconfirmed'])[0]
                if parsed.path=='/api/scenarios':return self._json({'scenarios':list(app.scenarios),'data_kind':'synthetic'})
                if parsed.path=='/api/state':return self._json(app.state(scenario))
                if parsed.path=='/api/replay':return self._json(app.replay(scenario,int(query['id'][0])))
                if parsed.path=='/api/view':return self._json(app.view(scenario,float(query['time_s'][0]) if 'time_s' in query else None))
                if parsed.path=='/api/export':return self._json(app.export(scenario))
                if parsed.path=='/api/template':return self._json(app.template())
                if parsed.path=='/health':return self._json({'status':'ok','flight_control_connected':False})
                filename={'/':'index.html','/app.js':'app.js','/round3.js':'round3.js','/style.css':'style.css'}.get(parsed.path)
                if filename is None:return self._json({'status':'not_found'},404)
                data=(static/filename).read_bytes()
                self.send_response(200);self.send_header('Content-Type',(mimetypes.guess_type(filename)[0] or 'text/plain')+'; charset=utf-8')
                self.send_header('Content-Length',str(len(data)));self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
            except (ValueError,KeyError,TypeError,OSError) as exc:self._json({'status':'request_rejected','reason':str(exc)},400)
        def do_POST(self):
            try:
                if not self._origin_ok():return self._json({'status':'cross_origin_rejected'},403)
                length=int(self.headers.get('Content-Length','0'))
                if length<1 or length>1_048_576:return self._json({'status':'invalid_body_length'},413)
                body=json.loads(self.rfile.read(length),parse_constant=lambda x:(_ for _ in ()).throw(ValueError('nonfinite_json')))
                if not isinstance(body,dict):raise ValueError('json_body_must_be_object')
                scenario=body.get('scenario','end_unconfirmed')
                if self.path=='/api/analyze':
                    with app.lock:
                        session=app._session(scenario);session['learned']=body.get('learned') is True;session['inflight']=body.get('inflight') is True
                    return self._json(app.state(scenario,True))
                if self.path=='/api/advance':return self._json(app.advance(scenario,float(body['time_s'])))
                if self.path=='/api/event':return self._json(app.event(scenario,body['event']))
                if self.path=='/api/adopt':return self._json(app.adopt(scenario,body['candidate_id']))
                if self.path=='/api/evidence':return self._json(app.evidence(scenario,body['request_id'],body.get('source')))
                if self.path=='/api/material':return self._json(app.material(scenario,body['records'],body.get('mock_response')))
                if self.path=='/api/material/apply':return self._json(app.accept_material(scenario,body['proposal_id']))
                if self.path=='/api/compare':return self._json(app.compare(scenario))
                if self.path=='/api/field/query':return self._json(app.field_query(scenario,body.get('object_id'),body.get('field'),body.get('request_id'),body.get('content'),body.get('format','json'),body.get('metadata')))
                if self.path=='/api/import':return self._json(app.import_material(scenario,body['content'],body.get('format','json'),body.get('metadata')))
                if self.path=='/api/import/apply':return self._json(app.accept_import(scenario,body['proposal_id'],body.get('resolutions')))
                return self._json({'status':'not_found'},404)
            except (ValueError,KeyError,TypeError,OSError) as exc:self._json({'status':'request_rejected','reason':str(exc)},400)
    return ThreadingHTTPServer((host,port),Handler)


def main(argv=None):
    parser=argparse.ArgumentParser(description='城空智护本地仿真台（只监听loopback）')
    parser.add_argument('--input',default='examples/park_crane_synthetic.json')
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--db-dir',default='runs/web')
    parser.add_argument('--model-dir',default=None)
    args=parser.parse_args(argv)
    app=SimulationApp(args.input,args.db_dir,args.model_dir)
    server=make_server(app,args.host,args.port)
    print(f'城空智护 local: http://{args.host}:{server.server_port}  synthetic simulation only',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
