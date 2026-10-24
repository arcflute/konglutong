"""Single-instance checkpoints and non-overwriting SQLite backup/restore.

No credentials are persisted. A changed input, runtime, rule or loaded model is
not silently treated as the same simulation; use a new directory for new runs.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone

from .world import RULE_VERSION


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1_048_576),b''):digest.update(chunk)
    return digest.hexdigest()


def digest_json(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


class InstanceLock:
    def __init__(self,directory):
        self.path=Path(directory)/'.instance.lock'
        self.file=self.path.open('a+')
        try:fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close();raise ValueError('single_instance_directory_already_in_use') from None
    def close(self):
        if not self.file.closed:
            fcntl.flock(self.file,fcntl.LOCK_UN);self.file.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    def __del__(self):
        if getattr(self,'file',None) is not None:self.close()


def runtime_identity(example,model_dir=None,bundle=None):
    path=Path(example)
    if not path.is_file():raise ValueError('recovery_input_missing: supply the original input file or an exact copy')
    model={'version':'none','manifest_sha256':None,'artifacts':{}}
    if bundle is not None:
        directory=Path(model_dir);manifest_path=directory/'manifest.json'
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        artifacts={}
        for name,declared in manifest.get('artifacts',{}).items():
            child=Path(name)
            if child.is_absolute() or '..' in child.parts:raise ValueError('invalid_model_artifact_path')
            actual=sha256(directory/child)
            expected=declared.get('sha256') if isinstance(declared,dict) else declared
            if expected and actual!=expected:raise ValueError('model_artifact_checksum_mismatch:'+name)
            artifacts[name]=actual
        if not artifacts:raise ValueError('model_manifest_requires_artifact_checksums_for_recovery')
        model={'version':bundle.version,'manifest_sha256':sha256(manifest_path),'artifacts':artifacts}
    source=Path(__file__).parent
    runtime=digest_json({f.name:sha256(f) for f in sorted(source.glob('*.py'))})
    return {'schema_version':'single-instance-binding-v1','input_sha256':sha256(path),
            'rule_version':RULE_VERSION,'runtime_sha256':runtime,'model':model}


def assert_binding(saved,current):
    if saved!=current:
        keys=sorted(k for k in set(saved)|set(current) if saved.get(k)!=current.get(k))
        raise ValueError('recovery_binding_mismatch:'+','.join(keys)+
                         '; restore the matching input, runtime and model; use a new db-dir for a different run')


class RuntimeLedger:
    """Directory-level active scenario and global service budget; no secret values."""
    def __init__(self,path,binding):
        self.path=str(path)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS service_budget (identity TEXT PRIMARY KEY,calls_used INTEGER NOT NULL)')
            old=db.execute('SELECT payload FROM metadata WHERE key=?',('binding',)).fetchone()
            if old:assert_binding(json.loads(old[0]),binding)
            else:db.execute('INSERT INTO metadata VALUES(?,?)',('binding',json.dumps(binding,sort_keys=True)))
    def active_scenario(self):
        with sqlite3.connect(self.path) as db:
            row=db.execute('SELECT payload FROM metadata WHERE key=?',('active_scenario',)).fetchone()
            return json.loads(row[0]) if row else None
    def set_active(self,scenario):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('active_scenario',json.dumps(scenario)))
    def reserve_call(self,identity,maximum):
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT calls_used FROM service_budget WHERE identity=?',(identity,)).fetchone()
            used=row[0] if row else 0
            if used>=maximum:return False,used
            used+=1
            db.execute('INSERT OR REPLACE INTO service_budget VALUES(?,?)',(identity,used))
            return True,used


class PersistentServiceAdapter:
    """Reserve before a real HTTP attempt; restarting cannot replenish its budget."""
    def __init__(self,adapter,ledger):self.adapter=adapter;self.ledger=ledger
    def parse(self,records,context):
        config=getattr(self.adapter,'config',None)
        if not config:return self.adapter.parse(records,context)
        # Store a hash only; endpoint query parameters and credentials are absent.
        identity=digest_json({k:config.get(k) for k in ('provider','endpoint','model_id')})
        allowed,used=self.ledger.reserve_call(identity,config.get('max_calls',3))
        if not allowed:return {'status':'budget_exhausted','provider':'structured_http','calls_used':used,
                               'candidate_events':[],'real_service_connected':False}
        self.adapter.calls_used=used-1
        return self.adapter.parse(records,context)


def snapshot_session(session):
    data={k:copy.deepcopy(session[k]) for k in ('result','candidates','evidence_results','learned','inflight',
                                               'import_audit','field_audit','field_requests')}
    data['candidate_order']=list(session['candidates'])
    # Additional explicit execution options may be added by the planner.
    for key in ('planning_mode','strategy','planner_mode','action_order'):
        if key in session:data[key]=copy.deepcopy(session[key])
    data['agent']={'proposals':copy.deepcopy(session['agent'].proposals),'audit':copy.deepcopy(session['agent'].audit)}
    importer=session.get('importer')
    data['importer']=None if importer is None else {'proposals':copy.deepcopy(importer.proposals),'audit':copy.deepcopy(importer.audit)}
    return data


def restore_session(session,payload):
    for key in ('result','candidates','evidence_results','learned','inflight','import_audit','field_audit','field_requests',
                'planning_mode','strategy','planner_mode','action_order'):
        if key in payload:session[key]=copy.deepcopy(payload[key])
    # JSON checkpoint serialization sorts object keys; preserve chronology as
    # data rather than relying on a mapping's insertion order after restart.
    if 'candidate_order' in payload:
        order=payload['candidate_order']
        if len(order)!=len(set(order)) or set(order)!=set(session['candidates']):
            raise ValueError('invalid_checkpoint_candidate_order')
        session['candidates']={key:session['candidates'][key] for key in order}
    session['agent'].proposals=copy.deepcopy(payload.get('agent',{}).get('proposals',{}))
    session['agent'].audit=copy.deepcopy(payload.get('agent',{}).get('audit',[]))
    if payload.get('importer') is not None:
        from .imports import StructuredImporter
        session['importer']=StructuredImporter()
        session['importer'].proposals=copy.deepcopy(payload['importer'].get('proposals',{}))
        session['importer'].audit=copy.deepcopy(payload['importer'].get('audit',[]))


def _readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)


def _publish_new(stage,destination):
    """Never replace a pre-existing directory, including a concurrent creator."""
    try:destination.mkdir()
    except FileExistsError:raise ValueError('destination_must_not_exist') from None
    try:
        # Publish manifest last: an interrupted restore is not a valid backup.
        for item in sorted(stage.iterdir(),key=lambda p:p.name=='manifest.json'):
            item.rename(destination/item.name)
        stage.rmdir()
    except BaseException:
        shutil.rmtree(destination,ignore_errors=True)
        raise


def backup_directory(source,destination):
    source=Path(source);destination=Path(destination)
    if not source.is_dir():raise ValueError('backup_source_directory_missing')
    if destination.exists():raise ValueError('backup_destination_must_not_exist')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with InstanceLock(source):
        files=sorted(source.glob('*.sqlite3'))
        if not files:raise ValueError('backup_has_no_databases')
        stage=Path(tempfile.mkdtemp(prefix='.chengkong-backup-',dir=destination.parent))
        try:
            manifest={'schema_version':'chengkong-sqlite-backup-v1','created_at_utc':datetime.now(timezone.utc).isoformat(),
                      'method':'sqlite3.Connection.backup; service stopped under single-instance lock','databases':{}}
            for original in files:
                target=stage/original.name
                with _readonly(original) as src,sqlite3.connect(target) as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('backup_integrity_check_failed')
                manifest['databases'][original.name]={'sha256':sha256(target),'bytes':target.stat().st_size}
            (stage/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
            _publish_new(stage,destination)
            return manifest
        except BaseException:
            shutil.rmtree(stage,ignore_errors=True);raise


def restore_directory(source,destination):
    source=Path(source);destination=Path(destination)
    if destination.exists():raise ValueError('restore_destination_must_not_exist')
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version')!='chengkong-sqlite-backup-v1':raise ValueError('unsupported_backup_schema')
    if not manifest.get('databases'):raise ValueError('backup_has_no_databases')
    for name,info in manifest['databases'].items():
        if Path(name).name!=name or not name.endswith('.sqlite3'):raise ValueError('invalid_backup_database_path')
        if sha256(source/name)!=info['sha256']:raise ValueError('backup_checksum_mismatch:'+name)
        with _readonly(source/name) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('backup_integrity_check_failed')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.chengkong-restore-',dir=destination.parent))
    try:
        for name in manifest['databases']:shutil.copy2(source/name,stage/name)
        _publish_new(stage,destination)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True);raise
    return {'status':'restored','database_count':len(manifest['databases']),
            'binding_policy':'matching input, runtime and model required on startup','destination':str(destination)}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Stopped single-instance SQLite backup and non-overwriting restore')
    parser.add_argument('operation',choices=('backup','restore'));parser.add_argument('--source',required=True);parser.add_argument('--destination',required=True)
    args=parser.parse_args(argv)
    result=(backup_directory if args.operation=='backup' else restore_directory)(args.source,args.destination)
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':main()
