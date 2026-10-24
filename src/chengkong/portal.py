"""Local three-portal access layer. All projections are server-side allowlists.

SQLite records are independent of flight-plan transactions. Local demo identities
are explicitly opt-in; this is not an internet identity provider.
"""
from __future__ import annotations
import copy
import hashlib
import html
import json
import math
import secrets
import sqlite3
import time
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from .presentation import spatial_view, event_timeline


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


class AccessError(ValueError):
    def __init__(self, message, status=403):
        super().__init__(message); self.status=status


def bounded_text(value, name, limit=2000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise ValueError(f'{name}_required_max_{limit}')
    return value.strip()


def versions(w):
    return {**{k:w.get(k) for k in ('environment_version','reservation_version','state_revision','rule_version','model_version')},
            'plan_versions':{t['id']:t['plan_version'] for t in w['tasks']}}


def change_rows(w):
    rows=[]
    for obj in w['activities']:
        rows.append({'id':'initial:'+obj['id'],'object_id':obj['id'],'kind':'activity_registered',
            'effective_at_s':obj['planned_interval_s'][0], 'observed_at_s':next((s.get('observed_at_s',w['initial_state_as_of_s']) for s in w['known_sources'] if s['id']==obj.get('source_id')),w['initial_state_as_of_s']),
            'status_as_of_s':w['as_of_s'],
            'source_ids':[obj.get('source_id')], 'status':obj['completion_status'],
            'planned_interval_s':obj['planned_interval_s'], 'confirmed_at_s':obj.get('completion_confirmed_at_s'),
            'summary':'初始吊装作业记录（非实时上报）','record_type':'initial_snapshot'})
    for item in w.get('event_log',[]):
        ev=item.get('event',item)
        rows.append({'id':item['id'],'object_id':ev.get('activity_id',ev.get('facility_id',ev.get('facility',{}).get('id'))),
            'kind':ev['kind'],'effective_at_s':item['effective_at_s'],'observed_at_s':item['observed_at_s'],
            'source_ids':[ev.get('source_kind','unknown')],'status':'pending' if item['effective_at_s']>w['as_of_s'] else 'recorded',
            'summary':ev['kind'],'record_type':'event'})
    return sorted(rows,key=lambda row:(row['observed_at_s'],row['id']),reverse=True)


class PortalService:
    def __init__(self,app,demo=False,config_path=None):
        self.app=app; self.demo=demo
        path=Path(config_path) if config_path else Path(__file__).resolve().parents[2]/'configs/portal-demo.json'
        self.config=json.loads(path.read_text(encoding='utf-8'))
        self.identities={p['id']:p for p in self.config['identities']}
        self.path=app.db_dir/'_portal.sqlite3'
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS portal_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,subject TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,scenario TEXT NOT NULL,subject TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS follows(subject TEXT NOT NULL,scenario TEXT NOT NULL,PRIMARY KEY(subject,scenario));
            CREATE TABLE IF NOT EXISTS publications(scenario TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,digest TEXT NOT NULL,visible INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scenario,id));
            CREATE TABLE IF NOT EXISTS publication_controls(scenario TEXT NOT NULL,id TEXT NOT NULL,visible INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scenario,id));
            CREATE TABLE IF NOT EXISTS publication_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,subject TEXT NOT NULL,scenario TEXT NOT NULL,event_id TEXT NOT NULL,version INTEGER NOT NULL,is_read INTEGER NOT NULL DEFAULT 0,UNIQUE(subject,scenario,event_id,version));
            CREATE TABLE IF NOT EXISTS personal_records(id TEXT PRIMARY KEY,subject TEXT NOT NULL,kind TEXT NOT NULL,dedupe TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(subject,kind,dedupe));
            CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,subject TEXT NOT NULL,role TEXT NOT NULL,scenario TEXT NOT NULL,payload TEXT NOT NULL);
            ''')
            identity=fingerprint(self.config)
            old=db.execute("SELECT value FROM portal_meta WHERE key='access_config'").fetchone()
            if old and old[0]!=identity:raise ValueError('portal_access_config_changed_use_new_directory')
            db.execute("INSERT OR IGNORE INTO portal_meta VALUES('access_config',?)",(identity,))
            db.execute("INSERT OR IGNORE INTO portal_meta VALUES('schema_version','1')")
            if not demo:db.execute('DELETE FROM sessions')

    @contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=15);conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()

    def subject(self,cookie):
        jar=SimpleCookie()
        try:jar.load(cookie or '')
        except Exception:raise AccessError('invalid_session',401) from None
        token=jar.get('klt_session')
        if not token:return None
        with self.db() as db: row=db.execute('SELECT subject FROM sessions WHERE token_hash=? AND expires>?',
                         (hashlib.sha256(token.value.encode()).hexdigest(),time.time())).fetchone()
        return copy.deepcopy(self.identities.get(row['subject'])) if row else None

    def session(self,subject):
        return {'subject':subject,'demo_mode':self.demo,'identities':list(self.identities.values()) if self.demo else [],
                'authentication_status':'local_demo_only' if self.demo else 'identity_provider_not_configured'}

    def login(self,identity,cookie):
        if not self.demo:raise AccessError('demo_identity_switch_disabled')
        if identity not in self.identities:raise AccessError('unknown_demo_identity')
        self.logout(cookie);token=secrets.token_urlsafe(32)
        with self.db() as db:db.execute('INSERT INTO sessions VALUES(?,?,?)',
            (hashlib.sha256(token.encode()).hexdigest(),identity,time.time()+43200))
        return self.session(copy.deepcopy(self.identities[identity])),f'klt_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'

    def logout(self,cookie):
        jar=SimpleCookie();jar.load(cookie or '')
        if 'klt_session' in jar:
            with self.db() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(jar['klt_session'].value.encode()).hexdigest(),))

    def authorize(self,subject,scenario=None,roles=None):
        if subject is None:raise AccessError('session_required',401)
        if roles and subject['role'] not in roles:raise AccessError('operation_not_authorized_for_role')
        if scenario is not None:
            if scenario not in self.app.scenarios:raise AccessError('scenario_not_authorized_or_missing',404)
            if subject['role']=='biz' and subject.get('organization_id')!=self.config['organization']['id']:
                raise AccessError('organization_scope_denied')
            if subject['role']=='gov' and subject.get('regions')!='all_configured_scenarios':raise AccessError('region_scope_denied')
        return subject

    def scenarios(self,subject):
        self.authorize(subject)
        values=[]
        for sid in self.app.scenarios:
            try:self.authorize(subject,sid)
            except AccessError:continue
            values.append({'id':sid,'name':{'end_unconfirmed':'结束待确认','on_time_confirmed':'按期确认（仿真分支）','delayed_confirmed':'延期（仿真分支）'}.get(sid,sid),
                           'region_name':'示范园区','data_kind':'synthetic'})
        return {'scenarios':values,'active_scenario':self.app.active_scenario if any(s['id']==self.app.active_scenario for s in values) else (values[0]['id'] if values else None)}

    def public_projection(self,scenario):
        # Whitelist by pre-authorized seed entity. Never serialize world/tasks or
        # imported source descriptions into public responses.
        with self.app.lock:
            w=self.app._session(scenario)['store'].current_world();allowed=set(self.config['published_object_ids'])
            with self.db() as db:
                revoked={r[0] for r in db.execute('SELECT id FROM publication_controls WHERE scenario=? AND visible=0',(scenario,))}
            allowed-=revoked
            view=spatial_view(w)
            geometry=[{k:copy.deepcopy(o[k]) for k in ('id','component','geometry','geometry_status','interval_s') if k in o}
                      for o in view['objects'] if o['id'] in allowed]
            motion=[]
            for f in view['motion_snapshot']['facilities']:
                if f['facility_id'] in allowed:
                    motion.append({k:copy.deepcopy(f[k]) for k in ('facility_id','components','motion_status','at_s','pose','activity_clearance_m') if k in f})
            records=[]
            for a in w['activities']:
                if a['id'] not in allowed:continue
                records.append({'id':a['id'],'region_id':scenario,'category':'吊装施工','summary':'示范园区吊装活动环境提示',
                    'planned_interval_s':copy.deepcopy(a['planned_interval_s']), 'completion_status':a['completion_status'],
                    'confirmed_at_s':a.get('completion_confirmed_at_s'),'facility_still_present':any(f['id']==a['facility_id'] and f['installed'] for f in w['facilities']),
                    'source_summary':'预先获准公开的合成示例环境记录', 'data_kind':'synthetic',
                    'scope_limit':'计划结束不代表确认完成；环境信息不构成飞行许可。',
                    'geometry_revision':fingerprint({'facilities':[{k:f.get(k) for k in ('id','physical_geometry','motion_model','installed')} for f in w['facilities'] if f['id'] in allowed],
                        'buildings':[{k:b.get(k) for k in ('id','geometry')} for b in w['buildings'] if b['id'] in allowed],
                        'activity_envelope':a.get('activity_envelope')})[:16]})
            with self.db() as db:
                current=set()
                for record in records:
                    current.add(record['id']);digest=fingerprint(record)
                    old=db.execute('SELECT * FROM publications WHERE scenario=? AND id=?',(scenario,record['id'])).fetchone()
                    changed=old is None or old['digest']!=digest or not old['visible']
                    version=(old['version']+1 if old else 1) if changed else old['version']
                    record.update(public_version=version,updated_at_s=w['as_of_s'] if changed else json.loads(old['payload'])['updated_at_s'])
                    db.execute('INSERT OR REPLACE INTO publications VALUES(?,?,?,?,?,?)',(scenario,record['id'],version,digest,1,packed(record)))
                    if changed:
                        for follow in db.execute('SELECT subject FROM follows WHERE scenario=?',(scenario,)).fetchall():
                            key=fingerprint([follow['subject'],scenario,record['id'],version])[:24]
                            db.execute('INSERT OR IGNORE INTO notifications(id,subject,scenario,event_id,version) VALUES(?,?,?,?,?)',
                                       (key,follow['subject'],scenario,record['id'],version))
                for old in db.execute('SELECT id FROM publications WHERE scenario=? AND visible=1',(scenario,)).fetchall():
                    if old['id'] not in current:db.execute('UPDATE publications SET visible=0 WHERE scenario=? AND id=?',(scenario,old['id']))
            return {'region':{'id':scenario,'name':'示范园区','data_kind':'synthetic','publication_note':self.config['publication_note']},
                'records':records,'known_as_of_s':w['as_of_s'],'environment_version':w['environment_version'],
                'public_version':fingerprint(records)[:12], 'spatial_view':{'objects':geometry,'motion_snapshot':{'facilities':motion},
                    'bounds_m':w['bounds_m'],'coordinate_system':w['coordinate_system'],'view_time_s':w['as_of_s'],
                    'known_as_of_s':w['as_of_s'],'environment_version':w['environment_version']},
                'display_anchor':self.config['display_anchor'],'horizon_s':w['horizon_s'],
                'information_gaps':['实际结束尚未确认'] if any(a['completion_status']!='confirmed' for a in records) else [],
                'scope':'查看已发布的园区环境信息；资料缺口与结束状态以对应来源记录为准。'}

    def context(self,subject,scenario):
        self.authorize(subject,scenario)
        if subject['role']=='personal':return self.public_projection(scenario)
        with self.app.lock:
            state=self.app.state(scenario)
            if subject['role']=='biz':
                w=state['result']['world'];state.update(versions=versions(w),changes=change_rows(w),
                    reservations=self.app._session(scenario)['store'].current_reservations(),
                    organization=self.config['organization'],display_anchor=self.config['display_anchor'],
                    services={'multimodal':'configured_unverified' if (self.app.root/'config/multimodal.local.json').exists() else 'not_configured',
                              'flight_control':'not_connected'},region_name='示范园区')
                return state
            result=self.government(state,scenario)
            with self.db() as db:
                controls={r['id']:json.loads(r['payload']) for r in db.execute('SELECT * FROM publication_controls WHERE scenario=?',(scenario,))}
            result['publication_controls']=[controls.get(a['id'],{'object_id':a['id'],'visible':True,'reason':'预先获准公开的合成种子'})
                for a in state['result']['world']['activities'] if a['id'] in self.config['published_object_ids']]
            return result

    def government(self,state,scenario):
        w=state['result']['world'];shared=set(self.config['shared_task_ids'])
        analyses={k:copy.deepcopy(v) for k,v in state['result']['baseline_analysis'].items() if k in shared}
        # No full world, candidates, internal costs, raw materials or private task
        # paths can accidentally appear when a new backend field is introduced.
        tasks=[{k:copy.deepcopy(t[k]) for k in ('id','plan_version','trajectory') if k in t}
               for t in w['tasks'] if t['id'] in shared]
        return {'region_name':'示范园区','scenario_id':scenario,'as_of_s':w['as_of_s'],'versions':{
            **versions(w),'plan_versions':{t['id']:t['plan_version'] for t in tasks}},
            'spatial_view':self.shared_spatial(state['spatial_view']),'display_anchor':self.config['display_anchor'],
            'changes':change_rows(w),'timeline':copy.deepcopy(state['timeline']),
            'shared_tasks':tasks,'analysis':analyses,'history':copy.deepcopy(state.get('history',[])),
            'information_gaps':copy.deepcopy(w.get('information_gaps',[])),
            'notes':self.notes(scenario),'data_kind':'synthetic','horizon_s':w['horizon_s'],
            'scope':'仅示范园区；任务共享白名单 '+', '.join(sorted(shared)),
            'facilities':[{k:copy.deepcopy(f.get(k)) for k in ('id','installed','removal_confirmed')} for f in w['facilities']]}

    def shared_spatial(self,view):
        result=copy.deepcopy(view)
        result['executed_prefixes']={k:v for k,v in result.get('executed_prefixes',{}).items() if k in self.config['shared_task_ids']}
        return result

    def view(self,subject,scenario,at_s):
        self.authorize(subject,scenario,('gov','biz'))
        view=self.app.view(scenario,at_s)
        return self.shared_spatial(view) if subject['role']=='gov' else view

    def replay(self,subject,scenario,identifier,view_time_s=None):
        self.authorize(subject,scenario,('gov','biz'))
        with self.app.lock:
            raw=self.app.replay(scenario,identifier,view_time_s)
            raw['history_id']=identifier
            if subject['role']=='biz':return raw
            projected=self.government({**raw,'history':[]},scenario)
            projected['read_only']=True;projected['history_id']=identifier;projected['notes']=[] # later notes are not past knowledge
            return projected

    def notes(self,scenario):
        with self.db() as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM notes WHERE scenario=? ORDER BY rowid DESC',(scenario,))]

    def set_publication(self,subject,body):
        sid=body['scenario'];self.authorize(subject,sid,('gov',))
        obj=body.get('object_id');visible=body.get('visible')
        if not isinstance(visible,bool):raise ValueError('visible_boolean_required')
        reason=bounded_text(body.get('reason'),'publication_reason',500)
        with self.app.lock:
            w=self.app._session(sid)['store'].current_world()
            if obj not in self.config['published_object_ids'] or obj not in {a['id'] for a in w['activities']}:
                raise ValueError('only_preapproved_activity_publication_can_be_controlled')
            row={'object_id':obj,'scenario':sid,'visible':visible,'reason':reason,'subject':subject['id'],
                 'at_s':w['as_of_s'],'environment_version':w['environment_version'],'world_updated':False}
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO publication_controls VALUES(?,?,?,?)',(sid,obj,int(visible),packed(row)))
                db.execute('INSERT INTO publication_audit(payload) VALUES(?)',(packed(row),))
                if not visible:db.execute('UPDATE publications SET visible=0 WHERE scenario=? AND id=?',(sid,obj))
            # Synchronous refresh prevents a stale inbox even after unfollowing.
            self.public_projection(sid)
        return row

    def add_note(self,subject,body):
        sid=body['scenario'];self.authorize(subject,sid,('gov',))
        content=bounded_text(body.get('text'),'note',1500);status=body.get('status','pending')
        if status not in ('pending','reviewing','reviewed'):raise ValueError('unsupported_verification_status')
        with self.app.lock:
            w=self.app._session(sid)['store'].current_world();ids={r['id'] for r in change_rows(w)}
            if body.get('record_id') not in ids:raise ValueError('verification_record_not_found')
            row={'id':'note-'+secrets.token_hex(8),'scenario':sid,'record_id':body['record_id'],'text':content,
                 'status':status,'author':subject['name'],'created_at_s':w['as_of_s'],'environment_version':w['environment_version'],
                 'fact_updated':False}
        with self.db() as db:db.execute('INSERT INTO notes VALUES(?,?,?,?)',(row['id'],sid,subject['id'],packed(row)))
        return row

    def personal_records(self,subject):
        self.authorize(subject,roles=('personal',))
        # Update publications for followed regions before reading inbox; duplicates
        # are prevented by the unique key, including across process restarts.
        with self.db() as db:follows=[r[0] for r in db.execute('SELECT scenario FROM follows WHERE subject=?',(subject['id'],))]
        for sid in follows:self.public_projection(sid)
        with self.db() as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM personal_records WHERE subject=? ORDER BY rowid DESC',(subject['id'],))]
            for row in rows:
                if row['kind']!='query':continue
                saved=row['result']['records'];visible=[]
                for record in saved:
                    publication=db.execute('SELECT visible FROM publications WHERE scenario=? AND id=?',(row['scenario'],record['id'])).fetchone()
                    if publication and publication['visible']:visible.append(record)
                row['result']['records']=visible
                row['result']['revoked_record_count']=len(saved)-len(visible)
            notifications=[]
            for n in db.execute('SELECT * FROM notifications WHERE subject=? ORDER BY rowid DESC',(subject['id'],)):
                p=db.execute('SELECT payload,visible FROM publications WHERE scenario=? AND id=?',(n['scenario'],n['event_id'])).fetchone()
                notifications.append({**dict(n),'record':json.loads(p['payload']) if p and p['visible'] else None,
                                      'status':'published' if p and p['visible'] else 'revoked'})
        return {'follows':follows,'notifications':notifications,'queries':[r for r in rows if r['kind']=='query'],
                'feedback':[r for r in rows if r['kind']=='feedback']}

    def personal_action(self,subject,action,body):
        self.authorize(subject,roles=('personal',));uid=subject['id']
        if action=='read':
            with self.db() as db:
                changed=db.execute('UPDATE notifications SET is_read=1 WHERE id=? AND subject=?',(body['id'],uid)).rowcount
                if not changed:raise AccessError('personal_record_not_found',404)
            return self.personal_records(subject)
        sid=body.get('scenario');self.authorize(subject,sid)
        if sid is None:raise ValueError('scenario_required')
        projection=self.public_projection(sid)
        if action=='follow':
            if not isinstance(body.get('following'),bool):raise ValueError('following_boolean_required')
            with self.db() as db:
                if body['following']:db.execute('INSERT OR IGNORE INTO follows VALUES(?,?)',(uid,sid))
                else:db.execute('DELETE FROM follows WHERE subject=? AND scenario=?',(uid,sid))
            return self.personal_records(subject)
        if action=='query':
            start=body.get('from_s');end=body.get('to_s')
            if any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in (start,end)) or not 0<=start<end:
                raise ValueError('invalid_query_interval')
            horizon=projection['horizon_s'];covered=horizon[0]<=start and end<=horizon[1]
            result={'coverage':'covered' if covered else 'outside_published_horizon','records':projection['records'] if covered else [],
                    'information_gaps':projection['information_gaps'] if covered else ['所选时段没有发布数据覆盖'],
                    'notice':'环境信息不构成飞行许可；没有数据不代表可飞。'}
            data={'from_s':start,'to_s':end,'public_version':projection['public_version'],'result':result}
        elif action=='feedback':
            data={'text':bounded_text(body.get('text'),'feedback',2000),'status':'received_pending_review','world_updated':False}
        else:raise ValueError('unsupported_personal_action')
        dedupe=fingerprint([sid,data]);row={'id':action+'-'+secrets.token_hex(8),'kind':action,'scenario':sid,
            'created_at_s':projection['known_as_of_s'],'received_at_unix_s':time.time(),**data}
        with self.db() as db:
            old=db.execute('SELECT payload FROM personal_records WHERE subject=? AND kind=? AND dedupe=?',(uid,action,dedupe)).fetchone()
            if old:return json.loads(old[0])
            db.execute('INSERT INTO personal_records VALUES(?,?,?,?,?)',(row['id'],uid,action,dedupe,packed(row)))
        return row

    def reports(self,subject,scenario):
        self.authorize(subject,scenario,('gov','biz'))
        with self.db() as db:
            return [{k:r[k] for k in ('id','title','scenario','versions','parameters','created_at_s')} for r in
                    (json.loads(row[0]) for row in db.execute('SELECT payload FROM reports WHERE subject=? AND scenario=? ORDER BY rowid DESC',(subject['id'],scenario)))]

    def create_report(self,subject,body):
        sid=body['scenario'];self.authorize(subject,sid,('gov','biz'))
        context=self.context(subject,sid);title=bounded_text(body.get('title'),'title',120)
        selected=body.get('record_id') or None
        rows=context['changes']
        if selected and selected not in {r['id'] for r in rows}:raise ValueError('report_record_not_found')
        params={'record_id':selected,'from_s':body.get('from_s'),'to_s':body.get('to_s')}
        start,end=params['from_s'],params['to_s']
        if (start is None)!=(end is None):raise ValueError('report_interval_requires_both_bounds')
        if start is not None:
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (start,end)) or start>end:raise ValueError('invalid_report_interval')
        changes=[r for r in rows if (not selected or r['id']==selected) and (start is None or start<=r['effective_at_s']<=end)]
        if subject['role']=='biz':
            r=context['result'];analysis=r['baseline_analysis'];now=r['world']['as_of_s']
            details={'analysis':analysis,'candidates':r['candidates'],'evidence_requests':r['evidence_requests'],
                'limits':r['limits'],'search_diagnostics':r['search_diagnostics'],'learning_status':r['learning_status']}
        else:
            analysis=context['analysis'];now=context['as_of_s'];details={'analysis':analysis,'notes':context['notes'],
                'information_gaps':context['information_gaps'],'scope':context['scope']}
        report={'id':'report-'+secrets.token_hex(8),'title':title,'scenario':sid,'role':subject['role'],'data_kind':'synthetic',
            'created_at_s':now,'versions':context['versions'],'parameters':params,'changes':changes,
            'analysis_scope':'全部授权任务的当前快照；事件筛选只筛选变化记录，不虚构事件因果归属',
            'summary':{'changes':len(changes),'checked_tasks':len(analysis),'conflict_tasks':sum(v['status']=='conflict' for v in analysis.values())},
            'details':details,'disclaimer':'合成示范，条件化分析；不构成飞行许可或现实效果证明。'}
        with self.db() as db:db.execute('INSERT INTO reports VALUES(?,?,?,?,?)',(report['id'],subject['id'],subject['role'],sid,packed(report)))
        return report

    def report(self,subject,identifier):
        self.authorize(subject,roles=('gov','biz'))
        with self.db() as db:row=db.execute('SELECT * FROM reports WHERE id=? AND subject=?',(identifier,subject['id'])).fetchone()
        if not row:raise AccessError('report_not_found',404)
        self.authorize(subject,row['scenario']);return json.loads(row['payload'])

    @staticmethod
    def report_html(report):
        esc=lambda v:html.escape(str(v))
        def table(headers,rows):
            return '<table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'
        statuses={'conflict':'发现冲突','clear':'本次范围未发现冲突','unknown':'未知','incomplete':'检查未完成'}
        summary=report['summary'];analysis=report['details']['analysis']
        body='<h2>范围与统计</h2><p>'+esc(report['analysis_scope'])+'</p>'+table(['变化记录','检查任务','冲突任务'],[[summary['changes'],summary['checked_tasks'],summary['conflict_tasks']]])
        body+='<h2>变化记录与来源</h2>'+table(['对象','变化','生效（相对秒）','信息到达（相对秒）','来源'],[[r['object_id'],r['kind'],r['effective_at_s'],r['observed_at_s'],', '.join(str(s) for s in r['source_ids'])] for r in report['changes']])
        body+='<h2>任务影响与检查范围</h2>'+table(['任务','结论','冲突项','未检查项'],[[k,statuses.get(v['status'],v['status']),len(v.get('conflicts',[])),len(v.get('unchecked',[]))] for k,v in analysis.items()])
        if report['details'].get('notes'):
            body+='<h2>协同核查记录</h2>'+table(['记录','流程状态','核查意见'],[[n['record_id'],n['status'],n['text']] for n in report['details']['notes']])
        if report['details'].get('candidates'):
            body+='<h2>当前维护候选（非自动采纳）</h2>'+table(['候选','类型','复核','实际稳定性代价'],[[c['id'],c['kind'],'通过' if c['review']['feasible'] else '未通过',c.get('stability_cost',{}).get('values','未计算')] for c in report['details']['candidates']])
        body+='<h2>适用条件与未解决信息</h2><p>'+esc(report['disclaimer'])+'</p><pre>'+esc(json.dumps({k:report['details'].get(k) for k in ('limits','information_gaps','evidence_requests','scope') if k in report['details']},ensure_ascii=False,indent=2))+'</pre>'
        body+='<details><summary>展开完整结构化快照（与JSON下载一致）</summary><pre>'+esc(json.dumps(report,ensure_ascii=False,indent=2))+'</pre></details>'
        return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+esc(report['title'])+'</title>'
            '<style>body{font:14px/1.7 system-ui;max-width:1000px;margin:40px auto;padding:0 20px;color:#142c3d}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}h1{font-size:28px}h2{font-size:18px;margin-top:28px}table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #d7e0e7;padding:9px;text-align:left;overflow-wrap:anywhere}th{background:#f2f6f8}tr{break-inside:avoid}thead{display:table-header-group}details{margin:24px 0}@media print{body{margin:0;padding:0}}</style>'
            '<p>空路通 · 合成园区 / 固定版本报告</p><h1>'+esc(report['title'])+'</h1><p>记录 '+esc(report['id'])+' · t+'+esc(report['created_at_s'])+' s</p><p>版本：'+esc(packed(report['versions']))+'</p>'+body+'</html>')
