"""Three fixed browser demos over actual backend; isolated local databases."""
import argparse,json,tempfile,threading
from pathlib import Path
from playwright.sync_api import sync_playwright
from chengkong.web import SimulationApp,make_server
from chengkong.execution import frozen_prefix

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='runs/round4/browser');parser.add_argument('--model-dir',default='/root/autodl-tmp/chengkong-models/round3');args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    report={'checks':[],'requests':[],'page_errors':[],'scope':'actual browser, deterministic synthetic input, explicit adoption, local listeners'}
    with tempfile.TemporaryDirectory(prefix='chengkong-round4-browser-') as tmp:
        servers=[]
        def start(name,filename):
            app=SimulationApp(ROOT/filename,Path(tmp)/name,args.model_dir,ROOT)
            server=make_server(app,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();servers.append((server,thread));return server
        dynamic=start('dynamic','examples/round4/dynamic_window.json');park=start('park','examples/park_crane_synthetic.json')
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1080})
                page.on('pageerror',lambda exc:report['page_errors'].append(str(exc)))
                page.on('request',lambda req:report['requests'].append({'method':req.method,'url':req.url}) if '/api/' in req.url else None)
                def idle():page.wait_for_function("document.getElementById('busy').hidden",timeout=60000)
                def post(selector,path):
                    with page.expect_response(lambda r:r.url.endswith(path) and r.request.method=='POST',timeout=60000) as response:page.locator(selector).click()
                    data=response.value.json();idle();assert response.value.ok,data;return data
                def scene(value):
                    with page.expect_response(lambda r:'/api/state?' in r.url,timeout=60000) as response:page.select_option('#scenario',value)
                    idle();return response.value.json()
                def snapshot(name,state):
                    (out/(name+'.json')).write_text(json.dumps(state,ensure_ascii=False,indent=2));page.screenshot(path=str(out/(name+'.png')),full_page=True)
                page.goto(f'http://127.0.0.1:{dynamic.server_port}',wait_until='networkidle');idle()
                assert page.locator('#scenario').input_value()=='dynamic_window';assert page.locator('#clock').inner_text()=='T + 0 s'
                dynamic_state=post('#analyze','/api/analyze');cs=dynamic_state['result']['candidates']
                waiting=next(c for c in cs if c['kind']=='delay' and c['review']['feasible']);detour=next(c for c in cs if c['kind']=='detour' and c['review']['feasible'])
                assert waiting['changes'][0]['departure_shift_s']>0 and detour['changes'][0]['distance_delta_m']>0
                page.locator('[data-tab=candidates]').click();page.locator(f'[data-preview="{waiting["id"]}"]').click()
                with page.expect_response(lambda r:'/api/operations?' in r.url) as resp:page.locator('#align-timeline').click()
                idle();assert any(x['kind']=='ground_wait' for x in resp.value.json()['tracks'])
                assert '时变占用' in page.locator('#decision-explanation').inner_text();snapshot('A1-dynamic-wait-and-detour',dynamic_state)
                page.select_option('#planning-mode','conservative');conservative=post('#apply-policy','/api/policy')
                assert conservative['result']['world']['environment_version']>dynamic_state['result']['world']['environment_version']
                stale=post(f'[data-adopt="{waiting["id"]}"]','/api/adopt');assert not stale['outcome']['adopted']
                snapshot('A2-conservative-stale-rejected',conservative)
                report['checks'].append('A: actual dynamic wait/detour and conservative strategies, version invalidation, aligned motion/wait timeline')
                page.goto(f'http://127.0.0.1:{park.server_port}',wait_until='networkidle');idle()
                state=scene('on_time_confirmed');old=state['result']['candidates'][0]['id'];before=state['result']['world']
                page.locator('[data-tab=imports]').click()
                with page.expect_response(lambda r:r.url.endswith('/api/template')):page.locator('#load-template').click()
                idle();proposed=post('#field-material-query','/api/field/query')
                assert proposed['outcome']['status']=='evidence_available';assert proposed['state']['result']['world']==before
                accepted=post(f'[data-import-apply="{proposed["import_review"]["proposal_id"]}"]','/api/import/apply')
                assert accepted['outcome']['world_updated'];assert accepted['state']['result']['world']['facilities'][0]['installed']
                page.locator('[data-tab=candidates]').click();stale=post(f'[data-adopt="{old}"]','/api/adopt');assert not stale['outcome']['adopted']
                fresh=next(c for c in accepted['state']['result']['candidates'] if c['review']['feasible']);adopted=post(f'[data-adopt="{fresh["id"]}"]','/api/adopt');assert adopted['outcome']['adopted']
                snapshot('B-evidence-arrival-and-explicit-adoption',adopted)
                report['checks'].append('B: field evidence comes from uploaded synthetic record; accept update, keep tower, reject old candidate, explicitly adopt new plan')
                state=scene('delayed_confirmed');page.fill('#time','120');before=post('#advance','/api/advance');page.check('#inflight');page.check('#learned')
                repaired=post('#analyze','/api/analyze');assert repaired['result']['learning_enabled']
                c=next(c for c in repaired['result']['candidates'] if c['review']['feasible'])
                for task in c['tasks']:
                    original=next(x for x in before['result']['world']['tasks'] if x['id']==task['id'])
                    assert frozen_prefix(task,120)==frozen_prefix(original,120)
                assert repaired['result']['checking_schedule']['all_pairs_checked']
                assert repaired['result']['learning_applicability']['new_domain_coverage_validated'] is False
                page.locator('[data-tab=candidates]').click();page.locator(f'[data-preview="{c["id"]}"]').click();snapshot('C-cooperative-inflight',repaired)
                with page.expect_download() as download:page.locator('#export-result').click()
                download.value.save_as(out/'export.json');export=json.loads((out/'export.json').read_text());assert export['versions']['environment_version']==repaired['result']['world']['environment_version'];assert export['maintenance_explanations']
                report['checks'].append('C: actual multi-task inflight suffix repair, unchanged executed prefixes, complete checks, learned-scope warning and result export')
                page.locator('[data-tab=history]').click()
                with page.expect_response(lambda r:'/api/replay?' in r.url):page.locator('[data-replay]').first.click()
                idle();assert page.locator('#apply-policy').is_disabled();assert page.locator('#export-result').is_disabled()
                report['checks'].append('read-only history blocks policy mutation and accidental live export')
                page.locator('#live').click();idle()
                # Process-level restart and SQLite backup are separately exercised by demo_recovery.py.
                park.shutdown();park.server_close();servers[1][1].join(timeout=5)
                restarted=start('park','examples/park_crane_synthetic.json')
                page.goto(f'http://127.0.0.1:{restarted.server_port}',wait_until='networkidle');idle()
                assert page.locator('#scenario').input_value()=='delayed_confirmed';assert page.locator('#clock').inner_text()=='T + 120 s'
                assert 'restored' in page.locator('#recovery-status').inner_text();snapshot('D-restart-restores-active-scene',page.request.get(f'http://127.0.0.1:{restarted.server_port}/api/state?scenario=delayed_confirmed').json())
                report['checks'].append('restart restores active scene, time and checkpoints without silently replacing state')
                page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(out/'mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
                assert not report['page_errors'],report['page_errors'];report['status']='passed';browser.close()
        finally:
            for server,thread in servers:
                if thread.is_alive():server.shutdown()
                server.server_close();thread.join(timeout=5)
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'checks':len(report['checks']),'api_requests':len(report['requests']),'output':str(out)},ensure_ascii=False))

if __name__=='__main__':main()
