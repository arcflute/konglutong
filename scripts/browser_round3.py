"""Actual browser workflows, isolated SQLite, ephemeral loopback listeners."""
import argparse
import json
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright
from chengkong.web import SimulationApp,make_server
from chengkong.execution import frozen_prefix

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='runs/round3/browser');parser.add_argument('--model-dir',default='/root/autodl-tmp/chengkong-models/round3');args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    report={'checks':[],'requests':[],'page_errors':[],'scope':'synthetic local actual browser and computation'}
    with tempfile.TemporaryDirectory(prefix='chengkong-round3-browser-') as tmp:
        servers=[]
        for name in ['park_crane_synthetic','motion_crane_synthetic']:
            app=SimulationApp(ROOT/f'examples/{name}.json',Path(tmp)/name,args.model_dir,ROOT)
            server=make_server(app,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();servers.append((server,thread))
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1080})
                page.on('pageerror',lambda exc:report['page_errors'].append(str(exc)))
                page.on('request',lambda req:report['requests'].append({'method':req.method,'url':req.url}) if '/api/' in req.url else None)
                def idle():page.wait_for_function("document.getElementById('busy').hidden",timeout=30000)
                def post(selector,path):
                    with page.expect_response(lambda r:r.url.endswith(path) and r.request.method=='POST',timeout=30000) as response:page.locator(selector).click()
                    data=response.value.json();idle();assert response.value.ok,data;return data
                def scene(value):
                    with page.expect_response(lambda r:'/api/state?' in r.url) as response:page.select_option('#scenario',value)
                    idle();return response.value.json()
                page.goto(f'http://127.0.0.1:{servers[0][0].server_port}',wait_until='networkidle');idle()
                assert page.locator('#space [data-space-object]').count()>0
                # Demo 1: uploaded structured field record -> proposal -> acceptance -> stale rejection -> maintenance.
                state=scene('on_time_confirmed');old=state['result']['candidates'][0]['id'];before=state['result']['world']
                page.locator('[data-tab=imports]').click()
                with page.expect_response(lambda r:r.url.endswith('/api/template')):page.locator('#load-template').click()
                idle();proposed=post('#field-material-query','/api/field/query')
                assert proposed['outcome']['status']=='evidence_available',proposed
                assert proposed['state']['result']['world']==before
                proposal=proposed['import_review']['proposal_id']
                imported=post(f'[data-import-apply="{proposal}"]','/api/import/apply')
                assert imported['outcome']['world_updated'];assert imported['state']['result']['world']['activities'][0]['planned_interval_s'][1]==600
                page.locator('[data-tab=candidates]').click();stale=post(f'[data-adopt="{old}"]','/api/adopt');assert not stale['outcome']['adopted']
                fresh=next(c for c in imported['state']['result']['candidates'] if c['review']['feasible'])
                page.locator(f'[data-preview="{fresh["id"]}"]').click();assert page.locator('#space polyline').count()>0
                adopted=post(f'[data-adopt="{fresh["id"]}"]','/api/adopt');assert adopted['outcome']['adopted']
                assert any(r['kind']=='plan_adopted' for r in adopted['state']['timeline'])
                report['checks'].append('demo1: real uploaded field evidence, explicit import, stale rejection, route overlay, atomic adoption')
                page.screenshot(path=str(out/'01-delay-maintenance.png'),full_page=True)
                # Demo 2: no record remains unknown; a new explicit completion material changes decision, tower remains.
                scene('end_unconfirmed');page.fill('#time','360');before=post('#advance','/api/advance')
                page.locator('[data-tab=imports]').click();page.select_option('#field-name','completion')
                unknown=post('#field-query','/api/field/query');assert unknown['outcome']['status']=='unknown'
                material=json.loads((ROOT/'examples/imports/construction_delay.json').read_text());record=material['records'][0]
                record.update(id='browser_completion',observed_at_s=360,effective_at_s=360)
                record['payload']={'object_id':'lifting_1','field':'completion','value':{'status':'confirmed','confirmed_at_s':360}}
                page.fill('#import-content',json.dumps(material));review=post('#import-prepare','/api/import')
                accepted=post(f'[data-import-apply="{review["outcome"]["proposal_id"]}"]','/api/import/apply')
                w=accepted['state']['result']['world'];assert w['facilities'][0]['installed'];assert w['activities'][0]['completion_status']=='confirmed'
                assert before['result']['baseline_analysis']['task_2']['status']=='conflict'
                assert accepted['state']['result']['baseline_analysis']['task_2']['status']=='clear'
                with page.expect_download() as download:page.locator('#export-result').click()
                download.value.save_as(out/'export.json');export=json.loads((out/'export.json').read_text());assert export['versions']['environment_version']==w['environment_version']
                report['checks'].append('demo2: missing field stays unknown; reviewed completion changes task2; physical crane remains; actual export')
                page.screenshot(path=str(out/'02-field-completion.png'),full_page=True)
                # Demo 3: joint repair from current simulated position preserves every executed prefix.
                scene('delayed_confirmed');page.fill('#time','120');before=post('#advance','/api/advance');page.check('#inflight');page.check('#learned')
                repaired=post('#analyze','/api/analyze');assert repaired['result']['learning_enabled']
                c=next(c for c in repaired['result']['candidates'] if c['review']['feasible'])
                for t in c['tasks']:
                    original=next(x for x in before['result']['world']['tasks'] if x['id']==t['id'])
                    assert frozen_prefix(t,120)==frozen_prefix(original,120)
                assert repaired['result']['checking_schedule']['all_pairs_checked']
                page.locator('[data-tab=models]').click();comparison=post('#compare-modes','/api/compare');assert comparison['status']=='compared';assert not comparison['current_plan_mutated']
                assert comparison['comparison']['learned']['checking_schedule']['all_pairs_checked']
                report['checks'].append('demo3: multi-task actual learned ordering, frozen in-flight prefixes, rule vs learned backend comparison')
                page.screenshot(path=str(out/'03-inflight-comparison.png'),full_page=True)
                page.locator('[data-tab=history]').click()
                with page.expect_response(lambda r:'/api/replay?' in r.url) as response:page.locator('[data-replay]').first.click()
                idle();assert response.value.json()['result']['world']['as_of_s']==60
                assert page.locator('#field-material-query').is_disabled();assert page.locator('#export-result').is_disabled()
                report['checks'].append('read-only saved knowledge replay blocks edits and accidental live export')
                # New motion view is actual backend geometry, preview neither advances clock nor releases evidence.
                page.goto(f'http://127.0.0.1:{servers[1][0].server_port}',wait_until='networkidle');idle()
                page.fill('#view-time','180')
                with page.expect_response(lambda r:'/api/view?' in r.url) as response:page.locator('#view-refresh').click()
                view=response.value.json();idle();assert view['known_as_of_s']==60;assert view['view_time_s']==180
                assert len(view['motion_snapshot']['facilities'][0]['components'])==4
                assert page.locator('#clock').inner_text()=='T + 60 s';assert '规则预测' in page.locator('#space-label').inner_text()
                page.select_option('#space-focus','crane');page.locator('#azimuth').fill('35');page.locator('#space g[data-space-object]').filter(has_text='crane_1 / tower').first.locator('polygon').first.click(force=True);assert page.locator('#object-detail pre').count()==1
                page.screenshot(path=str(out/'04-motion-height.png'),full_page=True)
                page.locator('[data-tab=conflicts]').click();page.locator('[data-conflict]').first.click();assert 'constraint_id' in page.locator('#object-detail').inner_text()
                page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(out/'05-mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
                assert not report['page_errors'],report['page_errors'];report['checks'].append('versioned dynamic components, read-only future preview, height view, conflict trace and mobile layout')
                report['status']='passed';browser.close()
        finally:
            for server,thread in servers:server.shutdown();server.server_close();thread.join(timeout=5)
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'checks':len(report['checks']),'api_requests':len(report['requests']),'output':str(out)},ensure_ascii=False))

if __name__=='__main__':main()
