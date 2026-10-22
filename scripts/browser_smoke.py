"""Exercise real UI actions against an isolated real backend; requires Playwright.

Use PLAYWRIGHT_BROWSERS_PATH and, on this host, LD_LIBRARY_PATH as in VALIDATION.
The temporary SQLite database and HTTP listener are always closed on completion.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright
from chengkong.web import SimulationApp, make_server


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='runs/round2-browser')
    parser.add_argument('--model-dir',default='/root/autodl-tmp/chengkong-models/round2')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    report={'scope':'synthetic local UI and real backend','checks':[],'requests':[],'page_errors':[]}
    with tempfile.TemporaryDirectory(prefix='chengkong-browser-') as database:
        app=SimulationApp(root/'examples/park_crane_synthetic.json',database,args.model_dir,root)
        server=make_server(app,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
                page.on('pageerror',lambda exc:report['page_errors'].append(str(exc)))
                page.on('request',lambda req:report['requests'].append({'method':req.method,'path':req.url.split(f':{server.server_port}')[-1]}) if '/api/' in req.url else None)
                def idle():
                    page.wait_for_function("document.getElementById('busy').hidden === true")
                def click_post(selector,path):
                    with page.expect_response(lambda r:r.url.endswith(path) and r.request.method=='POST') as response:
                        page.locator(selector).click()
                    result=response.value.json();idle()
                    assert response.value.status==200,result
                    return result
                def scene(value):
                    with page.expect_response(lambda r:'/api/state?' in r.url and r.request.method=='GET') as response:
                        page.select_option('#scenario',value)
                    result=response.value.json();idle();return result
                page.goto(f'http://127.0.0.1:{server.server_port}',wait_until='networkidle');idle()
                assert page.locator('#connection').inner_text()=='本地后端已连接'
                assert page.locator('#map polyline').count()>=3
                assert page.locator('#tasks tbody tr').count()==3
                assert not report['page_errors'],report['page_errors']
                page.screenshot(path=str(out/'initial.png'),full_page=True)
                report['checks'].append('initial scene, SVG and task rows rendered from backend')

                state=scene('on_time_confirmed')
                old=state['result']['candidates'][0]['id']
                page.locator('[data-tab=candidates]').click()
                page.locator(f'[data-preview="{old}"]').click()
                assert page.locator('#map polyline').count()>=6
                before=state['result']['world']['environment_version']
                changed=click_post('#event','/api/event')
                assert changed['result']['world']['environment_version']>before
                rejected=click_post(f'[data-adopt="{old}"]','/api/adopt')
                assert not rejected['outcome']['adopted'],rejected['outcome']
                assert '采纳被拒绝' in page.locator('#notice').inner_text()
                candidate=next(c for c in changed['result']['candidates'] if c['status']=='conditionally_feasible')
                adopted=click_post(f'[data-adopt="{candidate["id"]}"]','/api/adopt')
                assert adopted['outcome']['adopted'],adopted['outcome']
                assert adopted['state']['result']['world']['reservation_version']==2
                report['checks'].append('preview, event backend update, stale rejection, explicit atomic adoption')
                page.screenshot(path=str(out/'adopted.png'),full_page=True)

                scene('end_unconfirmed')
                page.fill('#time','360')
                state=click_post('#advance','/api/advance')
                assert state['result']['world']['as_of_s']==360
                assert state['result']['world']['activities'][0]['completion_status']=='not_confirmed'
                page.locator('[data-tab=evidence]').click()
                requests=state['result']['evidence_requests']
                assert len(requests)>=2,requests
                unknown=click_post(f'[data-query="{requests[0]["id"]}"]','/api/evidence')
                assert unknown['outcome']['status']=='unknown',unknown['outcome']
                assert unknown['state']['result']['world']['activities'][0]['completion_status']=='not_confirmed'
                resolved=click_post(f'[data-sim-evidence="{requests[1]["id"]}"]','/api/evidence')
                assert resolved['outcome']['status']=='resolved',resolved['outcome']
                w=resolved['state']['result']['world']
                assert w['activities'][0]['completion_status']=='confirmed'
                assert w['facilities'][0]['installed'] is True
                report['checks'].append('advance clock; failed evidence stays unknown; explicit synthetic evidence validated; physical facility remains')
                page.screenshot(path=str(out/'evidence.png'),full_page=True)

                page.check('#inflight');page.check('#learned')
                learning=click_post('#analyze','/api/analyze')
                assert learning['result']['learning_enabled'] is True
                assert learning['result']['analysis_mode']=='inflight_simulation'
                assert 'end_error_interval' in learning['result']['learning_advice']
                report['checks'].append('UI opt-in calls trained models and in-flight backend; hard checks retained')
                page.locator('[data-tab=models]').click()
                material=click_post('#material-mock','/api/material')
                assert material['outcome']['status']=='proposals_ready',material['outcome']
                version=material['state']['result']['world']['environment_version']
                proposal=material['outcome']['proposals'][0]['id']
                applied=click_post(f'[data-material-apply="{proposal}"]','/api/material/apply')
                assert applied['outcome']['world_updated'] is True,applied['outcome']
                assert applied['state']['result']['world']['environment_version']>version
                report['checks'].append('mock material parsing followed by explicit validated update')
                page.locator('[data-tab=history]').click()
                with page.expect_response(lambda r:'/api/replay?' in r.url) as response:
                    page.locator('[data-replay]').first.click()
                replay=response.value.json();idle()
                assert replay['read_only'] is True
                assert replay['result']['world']['as_of_s']==60
                assert page.locator('#advance').is_disabled()
                with page.expect_response(lambda r:'/api/state?' in r.url) as response:
                    page.locator('#live').click()
                live=response.value.json();idle()
                assert live['result']['world']['as_of_s']==360
                assert not page.locator('#advance').is_disabled()
                report['checks'].append('read-only historical knowledge replay and return to unchanged live state')
                page.locator('[data-tab=models]').click()
                page.screenshot(path=str(out/'models.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(out/'mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
                assert not report['page_errors'],report['page_errors']
                report['status']='passed'
                browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'checks':len(report['checks']),'api_requests':len(report['requests']),'output':str(out)},ensure_ascii=False))

if __name__=='__main__':main()
