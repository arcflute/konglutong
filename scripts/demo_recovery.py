"""Actual local server stop/restart/backup/restore; refuses existing output dir."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from chengkong.recovery import backup_directory,restore_directory


def run(output,model_dir=None):
    output=Path(output).resolve()
    if output.exists():raise ValueError('recovery_demo_output_must_not_exist')
    output.mkdir(parents=True)
    records=[];process=None;stream=None;base=None
    def start(db,label):
        nonlocal process,stream,base
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        command=[sys.executable,'-m','chengkong.web','--input',str(ROOT/'examples/park_crane_synthetic.json'),
                 '--db-dir',str(db),'--host','127.0.0.1','--port',str(port)]
        if model_dir:command+=['--model-dir',str(model_dir)]
        stream=(output/(label+'.log')).open('w')
        process=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if process.poll() is not None:raise AssertionError('server_exited:'+label)
            try:
                with urllib.request.urlopen(base+'/health',timeout=1) as response:
                    assert json.load(response)['status']=='ok'
                return
            except (OSError,urllib.error.URLError):time.sleep(.05)
        raise AssertionError('server_start_timeout:'+label)
    def stop():
        nonlocal process,stream
        if process is not None:
            process.send_signal(signal.SIGINT)
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait();raise AssertionError('normal_stop_timed_out')
            assert process.returncode==0,process.returncode
            process=None
        if stream:stream.close();stream=None
    def request(path,body=None):
        started=time.perf_counter();data=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=30) as response:reply=json.load(response)
        records.append({'path':path,'method':'POST' if body is not None else 'GET','elapsed_s':time.perf_counter()-started})
        return reply
    try:
        start(output/'original-db','first-start')
        first=request('/api/state');candidate=next(c for c in first['result']['candidates'] if c['review']['feasible'])
        assert request('/api/adopt',{'candidate_id':candidate['id']})['outcome']['adopted']
        request('/api/advance',{'time_s':360})
        unknown=request('/api/field/query',{'object_id':'lifting_1','field':'completion'})
        assert unknown['outcome']['status']=='unknown'
        material=json.loads((ROOT/'examples/imports/construction_delay.json').read_text())
        review=request('/api/import',{'content':material})
        before=request('/api/state');export_before=request('/api/export')
        (output/'before.json').write_text(json.dumps(export_before,ensure_ascii=False,indent=2))
        stop()
        start(output/'original-db','restart')
        assert request('/api/scenarios')['active_scenario']=='end_unconfirmed'
        restored=request('/api/state')
        assert restored['recovery_status']['status']=='restored'
        for key in ('result','history','field_audit','import_audit','saved_candidates'):
            assert restored[key]==before[key],key
        assert request('/api/field/query',{'object_id':'lifting_1','field':'completion'})['outcome']['status']=='budget_exhausted'
        accepted=request('/api/import/apply',{'proposal_id':review['outcome']['proposal_id']})
        assert accepted['outcome']['world_updated']
        assert not request('/api/adopt',{'candidate_id':candidate['id']})['outcome']['adopted']
        replay=request('/api/replay?id='+str(before['history'][0]['id']))
        assert replay['result']['world']==first['result']['world']
        after=request('/api/export');stop()
        backup_directory(output/'original-db',output/'backup')
        restore_directory(output/'backup',output/'restored-db')
        start(output/'restored-db','backup-restore')
        recovered=request('/api/export')
        for key in ('versions','visible_world','events','pending_workflows','history','plan_versions'):
            assert recovered[key]==after[key],key
        (output/'after.json').write_text(json.dumps(recovered,ensure_ascii=False,indent=2))
        stop()
        result={'status':'passed','server_processes':3,'api_calls':records,'request_count':len(records),
                'verified':['normal SIGINT stop/restart','active scenario','plan and reservation versions','pending material acceptance',
                            'field evidence budget','old candidate rejection','historical replay','SQLite backup and restored export'],
                'scope':'software synthetic simulation, same input/runtime/model identity; no real VLM call'}
        (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        return result
    finally:
        if process is not None:stop()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--model-dir')
    args=parser.parse_args();result=run(args.output,args.model_dir)
    print(json.dumps({'status':result['status'],'server_processes':result['server_processes'],'request_count':result['request_count']}))
