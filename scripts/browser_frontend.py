"""Real desktop browser acceptance. Isolated DBs; no mocked successful compute.

Use --output a new directory. Fault injection is restricted to explicit error
handling checks, never used for conflict/candidate/adoption conclusions.
"""
import argparse,copy,json,os,threading,time
from pathlib import Path
from playwright.sync_api import sync_playwright
from chengkong.web import SimulationApp,make_server
from chengkong.execution import frozen_prefix
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default='runs/frontend-v1/browser');p.add_argument('--model-dir',default='/root/autodl-tmp/chengkong-models/round3');args=p.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    if (out/'report.json').exists():raise SystemExit('use a new output directory to retain previous evidence')
    report={'checks':[],'screenshots':[],'requests':[],'page_errors':[],'console_errors':[], 'external_requests':[], 'viewport_checks':[], 'source':'actual Python HTTP backend and Chromium browser; synthetic data; no external flight control'}
    servers=[]
    def start(name,file):
        app=SimulationApp(ROOT/file,out/('db-'+name),args.model_dir,ROOT,{'action_order':['retain','detour','delay']})
        server=make_server(app,port=0,demo_identities=True);th=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);th.start();servers.append((server,th));return server
    motion=start('motion','examples/frontend-v1/motion.json');park=start('park','examples/frontend-v1/park.json');completion=start('completion','examples/frontend-v1/park.json')
    try:
      with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        report['browser']=browser.version;report['rendering']='headless Chromium, software WebGL via SwiftShader, DPR 1, default browser zoom 100% unless explicitly marked'
        ctx=browser.new_context(viewport={'width':1920,'height':1080},device_scale_factor=1);page=ctx.new_page();base=''
        page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
        page.on('console',lambda e:report['console_errors'].append(e.text) if e.type=='error' else None)
        def log_request(r):
            if '/api/' in r.url:report['requests'].append({'method':r.method,'url':r.url})
            if not r.url.startswith(('http://127.0.0.1:','blob:','data:')):report['external_requests'].append(r.url)
        page.on('request',log_request)
        def api(path,body=None,status=200):
            r=page.request.post(base+path,data=body,headers={'X-KLT-Request':'1'}) if body is not None else page.request.get(base+path)
            assert r.status==status,(r.status,r.text()[:400]);return r.json()
        def idle():
            page.wait_for_selector('.page-body',timeout=30000)
            page.wait_for_function("!document.querySelector('.busy-bar')",timeout=30000)
        def login(server,role,person=None):
            nonlocal base;base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/',wait_until='networkidle');page.locator('.portal-card.'+role).click()
            modal=page.get_by_role('dialog');target=person or {'biz':'林工','gov':'周老师','personal':'陈同学'}[role]
            modal.get_by_role('button',name=target).click();idle()
        def nav(path):
            page.locator(f'.sidebar a[href="{path}"]').click();idle()
        def scene(sid):
            if page.locator('.scenario-select select').input_value()==sid:return
            with page.expect_response(lambda r:'/api/portal/context?' in r.url and 'scenario='+sid in r.url,timeout=30000):page.locator('.scenario-select select').select_option(sid)
            idle()
        def snap(name,full=False):
            page.wait_for_timeout(350);page.screenshot(path=str(out/(name+'.png')),full_page=full);report['screenshots'].append(name+'.png')
        def check_layout(name):
            v=page.evaluate('''()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,visual_scale:visualViewport.scale,document_width:document.documentElement.scrollWidth,css_zoom:getComputedStyle(document.body).zoom,canvas_count:document.querySelectorAll('.cesium-widget canvas').length,map:(()=>{const e=document.querySelector('.spatial');if(!e)return null;const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,width:r.width,height:r.height,renderer:e.dataset.renderer}})()})''')
            assert v['document_width']<=v['width'],(name,v);assert v['canvas_count']<=1,(name,v)
            if v['map'] and v['width']>=1366:assert v['map']['top']<v['height']-180,(name,'map entirely below fold',v)
            report['viewport_checks'].append({'name':name,**v});return v
        def click_response(name,path):
            with page.expect_response(lambda r:('/api/'+path)==r.url.split('?')[0].replace(base,'') and r.request.method=='POST',timeout=30000) as response:page.get_by_role('button',name=name,exact=True).click()
            result=response.value.json();assert response.value.ok,result;idle();return result
        # Full local 3D, three desktop roles, nine primary viewport screenshots.
        for role in ('biz','gov','personal'):
            login_started=time.perf_counter()
            login(motion,role);scene('end_unconfirmed')
            page.wait_for_selector('.cesium-widget canvas',timeout=30000)
            page.wait_for_function("!document.querySelector('.map-loading')",timeout=30000)
            report.setdefault('performance',[]).append({'role':role,'login_to_3d_ready_s':time.perf_counter()-login_started,'method':'same browser session, local same-origin files, cached assets possible, not an FPS claim'})
            for width,height in ((1920,1080),(1600,900),(1366,768)):
                page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(600)
                v=check_layout(f'{role}-{width}x{height}');assert v['map']['renderer']=='3d';snap(f'{role}-{width}x{height}')
            for width,height in ((1440,900),(1024,768),(1920,947)):
                page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(200);check_layout(f'{role}-compat-{width}x{height}')
            if role=='personal':
                for width,height in ((390,844),(360,800)):
                    page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(200);check_layout(f'personal-{width}x{height}');snap(f'personal-{width}x{height}')
            page.set_viewport_size({'width':1600,'height':900})
            routes={'biz':['overview','evidence','tasks','analysis','maintenance','history'],'gov':['overview','changes','assessment','history','reports'],'personal':['explore','preflight','following','records']}[role]
            for route in routes:
                nav('/'+role+'/'+route);page.wait_for_timeout(300);assert page.locator('.page-heading h1').inner_text();assert not page.get_by_text('页面暂时无法显示',exact=True).count();check_layout(role+'-'+route);snap(role+'-'+route+'-page',True)
                page.reload();idle();assert page.url.split('?')[0].endswith('/'+role+'/'+route)
            report['checks'].append(role+': all routes, navigation and direct refresh; nine desktop captures/compatibility; one or zero WebGL contexts')
        page.set_viewport_size({'width':1920,'height':1080});page.goto(base+'/',wait_until='networkidle');snap('portal-1920-full',True)
        # A/C: public subscription, same event, private import, stale rejection,
        # explicit adoption, government annotations, reports and personal inbox.
        page.set_viewport_size({'width':1600,'height':900});login(park,'personal');scene('end_unconfirmed')
        page.get_by_role('button',name='关注此区域',exact=True).click();page.get_by_role('button',name='已关注 · 取消关注',exact=True).wait_for()
        login(park,'gov');scene('end_unconfirmed');nav('/gov/changes')
        page.get_by_label('备注与依据').fill('已核对合成登记来源；结束状态仍未确认。')
        note=click_response('保存核查记录','gov/notes');assert not note['fact_updated'];snap('A-government-verification')
        login(park,'biz');scene('end_unconfirmed');state=api('/api/portal/context?scenario=end_unconfirmed');original=copy.deepcopy(state['result']['world']);old=state['result']['candidates'][0]['id']
        nav('/biz/evidence');page.get_by_role('button',name='导入结构化材料',exact=True).click();page.get_by_role('button',name='载入合成延期模板').click();page.wait_for_function("document.querySelector('[data-testid=material-content]').value.includes('delay_record')")
        click_response('解析与校验','import');snap('A-import-review')
        accepted=click_response('明确接受有效更新','import/apply');assert accepted['outcome']['world_updated'];assert accepted['state']['result']['world']['tasks']==original['tasks'];page.get_by_role('button',name='关闭',exact=True).click()
        stale=api('/api/adopt',{'scenario':'end_unconfirmed','candidate_id':old});assert not stale['outcome']['adopted'];(out/'stale-rejection.json').write_text(json.dumps(stale['outcome'],ensure_ascii=False,indent=2))
        nav('/biz/maintenance');page.get_by_label('含历史候选').check();page.locator('.table-wrap tbody tr').first.click();assert page.get_by_role('button',name='查看并确认采纳').is_disabled();snap('A-stale-candidate-disabled');page.get_by_label('含历史候选').uncheck()
        page.get_by_role('button',name='查看并确认采纳').click();page.set_viewport_size({'width':1366,'height':768});snap('A-adoption-dialog-1366')
        adopted=click_response('确认采纳并更新计划','adopt');assert adopted['outcome']['adopted'];state=api('/api/portal/context?scenario=end_unconfirmed');assert state['reservations']['reservation_version']==state['result']['world']['reservation_version'];assert state['result']['world']['tasks']!=original['tasks'];snap('A-adopted-plan')
        nav('/biz/overview');assert f"预约 V{state['reservations']['reservation_version']}" in page.locator('body').inner_text();nav('/biz/history')
        before=api('/api/state?scenario=end_unconfirmed')['result']['world'];page.locator('.history-row button').first.click();page.wait_for_function("document.body.innerText.includes('历史只读快照')");after=api('/api/state?scenario=end_unconfirmed')['result']['world'];assert before==after
        report_data=click_response('生成版本报告','reports');assert report_data['versions']['environment_version']==after['environment_version'];snap('A-report-preview')
        with page.expect_download() as dl:page.get_by_role('button',name='下载 JSON').click()
        dl.value.save_as(str(out/'enterprise-report.json'));assert json.loads((out/'enterprise-report.json').read_text())==report_data
        with page.expect_popup() as popup:page.get_by_role('link',name='打印 HTML / 另存 PDF').click()
        popup.value.wait_for_load_state();assert report_data['title'] in popup.value.locator('body').inner_text();popup.value.pdf(path=str(out/'enterprise-report.pdf'),format='A4',print_background=True);popup.value.close();page.get_by_role('button',name='关闭',exact=True).click()
        report['checks'].append('A: server-backed JSON import review/accept, old candidate rejected, disabled UI, explicit adoption, all plan/reservation versions refreshed, read-only replay, matching JSON/print report')
        login(park,'personal');scene('end_unconfirmed');public=api('/api/portal/context?scenario=end_unconfirmed');assert 'tasks' not in json.dumps(public);assert 'candidate' not in json.dumps(public)
        nav('/personal/following');page.get_by_role('button',name='标为已读').first.click();page.wait_for_timeout(300);records=api('/api/personal/records');assert len(records['notifications'])==1;assert records['notifications'][0]['is_read'];snap('C-public-notification')
        nav('/personal/preflight');page.get_by_label('起点（相对秒）').fill('100');page.get_by_label('终点（相对秒）').fill('700');query_result=click_response('查询并保存记录','personal/query');assert query_result['result']['coverage']=='covered';nav('/personal/records');page.get_by_role('button',name='提交信息线索').click();page.get_by_label('反馈内容').fill('合成演示：请核对结束记录，不据此释放设施占用。');saved=click_response('提交并获取单号','personal/feedback');assert not saved['world_updated'];page.reload();idle();page.get_by_role('button',name='我的反馈',exact=True).click();page.get_by_role('button',name='详情',exact=True).wait_for();snap('C-feedback-persisted')
        personal_before=api('/api/personal/records');cookie=ctx.cookies(base)
        # Real stop/start with same inputs and model binding; server sessions and
        # personal state are recovered, and adopted plan remains committed.
        park.shutdown();park.server_close();servers[1][1].join(5)
        park=start('park','examples/frontend-v1/park.json');base=f'http://127.0.0.1:{park.server_port}'
        page.goto(base+'/personal/records?scenario=end_unconfirmed');idle();assert api('/api/personal/records')==personal_before
        login(park,'biz');scene('end_unconfirmed');assert api('/api/state?scenario=end_unconfirmed')['result']['world']==after
        report['checks'].append('C: whitelist public projection, follow/update/one deduplicated notification/read, saved query and feedback, actual stop/start session and adopted plan recovery')
        # B: missing completion evidence remains unknown, then explicit existing
        # synthetic material changes task 2 judgment while tower is retained.
        login(completion,'biz');scene('end_unconfirmed');page.get_by_role('button',name='仿真控制').click();page.get_by_label('目标相对时间（秒）').fill('360');advanced=click_response('推进并重新分析','advance');assert advanced['result']['baseline_analysis']['task_2']['status']=='conflict'
        nav('/biz/evidence');unknown=click_response('查询已有证据','field/query');assert unknown['outcome']['status']=='unknown';snap('B-unknown-preserved')
        page.get_by_role('button',name='导入结构化材料',exact=True).click();page.locator('input[type=file]').set_input_files(str(ROOT/'examples/frontend-v1/completion.json'))
        click_response('解析与校验','import');done=click_response('明确接受有效更新','import/apply');assert done['outcome']['world_updated'];assert done['state']['result']['baseline_analysis']['task_2']['status']=='clear';assert done['state']['result']['world']['facilities'][0]['installed'];page.get_by_role('button',name='关闭',exact=True).click();snap('B-completion-keeps-facility')
        report['checks'].append('B: missing field evidence stays unknown; user-imported explicitly synthetic confirmation enters layer 1; task 2 conflict becomes clear while crane physical occupancy stays')
        # Detailed map, layers, drawer, preview and in-flight protections.
        login(motion,'biz');scene('delayed_confirmed');nav('/biz/analysis');page.get_by_role('button',name='查看依据',exact=True).first.click();snap('conflict-evidence-drawer');page.keyboard.press('Escape');assert not page.get_by_role('dialog').count()
        page.get_by_role('button',name='切换图层').click();page.get_by_label('园区建筑',exact=True).uncheck();page.get_by_role('button',name='切换图层').click()
        page.locator('input[aria-label="运动预览时刻"]').evaluate("e=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'180');e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))}")
        page.wait_for_function("document.querySelector('.motion-preview').innerText.includes('t+180 s')",timeout=15000);assert api('/api/state?scenario=delayed_confirmed')['result']['world']['as_of_s']==60;snap('motion-preview-180')
        page.get_by_role('button',name='仿真控制').click();page.get_by_label('目标相对时间（秒）').fill('120');before=click_response('推进并重新分析','advance')['result']['world']
        page.get_by_role('button',name='分析依据 / 高级').click();page.get_by_label('仿真在途维护 · 冻结执行前缀').check();repaired=click_response('重新分析与生成候选','analyze')['result']
        for candidate in repaired['candidates']:
            for t in candidate['tasks']:assert frozen_prefix(t,120)==frozen_prefix(next(v for v in before['tasks'] if v['id']==t['id']),120)
        snap('inflight-frozen-prefix');report['checks'].append('actual motion preview at 180 does not advance knowledge; layer toggle/drawer keyboard close; in-flight analysis freezes prefixes')
        # HTTP fault display only (never success data), empty authorized scope.
        page.route('**/api/portal/context?*',lambda route:route.fulfill(status=503,content_type='application/json',body='{"status":"qa_injected_network_outage"}'))
        page.get_by_role('button',name='刷新当前版本',exact=True).click();page.get_by_text('数据未能加载',exact=True).wait_for();snap('error-state');page.unroute('**/api/portal/context?*');page.get_by_role('button',name='重试',exact=True).click();idle()
        # Login through visible local identity selector; other organization stays empty.
        page.locator('.identity-button').click();page.get_by_role('dialog').get_by_role('button',name='其他机构').click();page.get_by_text('当前机构没有授权场景',exact=True).wait_for();snap('organization-empty-state')
        report['checks'].append('injected failed GET has explicit retry; unauthorized organization has genuine empty scope')
        # Forced WebGL unavailability uses the same backend 2D projection.
        login(motion,'personal');fallback=ctx.new_page();fallback.add_init_script("const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){if(type.includes('webgl'))return null;return get.call(this,type,...args)}")
        fallback.on('console',lambda e:report.setdefault('forced_fallback_console',[]).append(e.text) if e.type=='error' else None)
        fallback.goto(base+'/personal/explore?scenario=end_unconfirmed');fallback.wait_for_selector('.map-2d',timeout=30000);fallback.screenshot(path=str(out/'webgl-unavailable-fallback.png'));report['screenshots'].append('webgl-unavailable-fallback.png');fallback.close()
        report['checks'].append('forced WebGL failure displays genuine same-data 2D fallback; no remote basemap required')
        assert not report['page_errors'],report['page_errors'];assert not report['external_requests'],report['external_requests']
        report['passed']=True;browser.close()
    except BaseException as exc:
        report['passed']=False;report['failure']=repr(exc);raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        for server,thread in servers:
            server.shutdown();server.server_close();thread.join(5)
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'screenshots':len(report['screenshots']),'viewport_checks':len(report['viewport_checks']),'page_errors':report['page_errors'],'output':str(out)},ensure_ascii=False))
if __name__=='__main__':main()
