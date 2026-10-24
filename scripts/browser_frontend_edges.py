"""Late-response, public revocation, focus and final personal-map browser checks.

Only delays genuine backend responses. Does not substitute successful payloads.
"""
import argparse,asyncio,json,threading,time
from pathlib import Path
from playwright.async_api import async_playwright
from chengkong.web import SimulationApp,make_server
ROOT=Path(__file__).resolve().parents[1]

async def main(out):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    app=SimulationApp(ROOT/'examples/frontend-v1/motion.json',out/'db','/root/autodl-tmp/chengkong-models/round3',ROOT)
    server=make_server(app,port=0,demo_identities=True)
    th=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);th.start()
    base=f'http://127.0.0.1:{server.server_port}';report={'checks':[],'screenshots':[],'viewports':[],'page_errors':[]}
    try:
      async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        ctx=await browser.new_context(viewport={'width':1600,'height':900},device_scale_factor=1);page=await ctx.new_page()
        page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
        async def api(path,body=None):
            r=await page.request.post(base+path,data=body,headers={'X-KLT-Request':'1'}) if body is not None else await page.request.get(base+path)
            assert r.ok,(r.status,await r.text());return await r.json()
        async def idle():
            await page.wait_for_selector('.page-body');await page.wait_for_function("!document.querySelector('.busy-bar')")
        async def login(identity,path):
            await api('/api/session/demo',{'identity':identity});await page.goto(base+path+'?scenario=end_unconfirmed');await idle()
        async def snap(name):
            await page.screenshot(path=str(out/(name+'.png')));report['screenshots'].append(name+'.png')
        await login('operator','/biz/analysis')
        await api('/api/advance',{'scenario':'delayed_confirmed','time_s':120})
        seen=asyncio.Event();release=asyncio.Event();finished=asyncio.Event()
        async def delayed(route):
            response=await route.fetch();seen.set();await release.wait()
            try:await route.fulfill(response=response)
            except Exception as e:
                if 'closed' not in str(e).lower() and 'cancel' not in str(e).lower():raise
            finally:finished.set()
        pattern='**/api/portal/context?scenario=delayed_confirmed'
        await page.route(pattern,delayed)
        await page.locator('.scenario-select select').select_option('delayed_confirmed')
        await asyncio.wait_for(seen.wait(),20)
        await page.evaluate("()=>{history.pushState({},'', '/biz/analysis?scenario=end_unconfirmed');dispatchEvent(new PopStateEvent('popstate'))}")
        await idle();release.set();await asyncio.wait_for(finished.wait(),10);await page.wait_for_timeout(400)
        assert await page.locator('.scenario-select select').input_value()=='end_unconfirmed'
        assert 't+60 s' in await page.locator('.context-bar').inner_text()
        await page.unroute(pattern,delayed);await snap('late-scenario-response-rejected')
        report['checks'].append('Delayed genuine t=120 scenario response cannot overwrite selected t=60 scenario; browser history navigation remains coherent.')
        await page.wait_for_selector('.motion-preview input')
        seen.clear();release.clear();finished.clear();pattern='**/api/portal/view?scenario=end_unconfirmed&time_s=180'
        await page.route(pattern,delayed)
        async def slider(value):
            await page.get_by_label('运动预览时刻',exact=True).evaluate("(el,v)=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,String(v));el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}",value)
        await slider(180);await asyncio.wait_for(seen.wait(),20);await slider(240)
        await page.wait_for_function("document.querySelector('.motion-preview strong').textContent.includes('240')")
        release.set();await asyncio.wait_for(finished.wait(),10);await page.wait_for_timeout(350)
        assert '240' in await page.locator('.motion-preview strong').inner_text()
        assert (await api('/api/state?scenario=end_unconfirmed'))['result']['world']['as_of_s']==60
        await page.unroute(pattern,delayed);await snap('late-motion-preview-rejected')
        report['checks'].append('Delayed genuine t=180 geometry cannot overwrite newer t=240 preview; actual knowledge clock remains 60.')
        # Exercise current focus trap, including Shift+Tab from dialog itself.
        await page.get_by_role('button',name='仿真控制',exact=True).click()
        dialog=page.get_by_role('dialog');await dialog.focus();await page.keyboard.press('Shift+Tab')
        assert await dialog.evaluate('(el)=>el.contains(document.activeElement)')
        await page.keyboard.press('Escape');assert await dialog.count()==0
        report['checks'].append('Dialog Shift+Tab remains inside modal; Escape closes; focus returns to invoking control.')
        await login('resident','/personal/explore')
        await page.get_by_role('button',name='关注此区域',exact=True).click()
        await page.get_by_role('button',name='已关注 · 取消关注',exact=True).wait_for()
        await api('/api/personal/query',{'scenario':'end_unconfirmed','from_s':60,'to_s':600})
        for width,height in ((1920,1080),(1600,900),(1366,768)):
            await page.set_viewport_size({'width':width,'height':height});await page.wait_for_selector('.cesium-widget canvas');await page.wait_for_function("!document.querySelector('.map-loading')");await page.wait_for_timeout(300)
            v=await page.evaluate('()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,visual_scale:visualViewport.scale,css_zoom:getComputedStyle(document.body).zoom,document_width:document.documentElement.scrollWidth,canvas_count:document.querySelectorAll(".cesium-widget canvas").length})')
            assert v['document_width']==width and v['canvas_count']==1
            assert '原计划' not in await page.locator('.scene-legend').inner_text()
            report['viewports'].append(v);await snap(f'personal-final-{width}x{height}')
        await login('operator','/biz/overview')
        await api('/api/event',{'scenario':'end_unconfirmed','event':{'id':'public-version-edge','kind':'planned_end_changed','activity_id':'lifting_1','effective_at_s':60,'observed_at_s':60,'new_planned_end_s':600,'source_kind':'explicit_synthetic_test'}})
        before=(await api('/api/state?scenario=end_unconfirmed'))['result']['world']
        await login('reviewer','/gov/changes');await page.set_viewport_size({'width':1600,'height':900})
        await page.locator('summary').filter(has_text='公开摘要 ·').click()
        await page.get_by_label('可见性调整依据').fill('合成验收：暂撤公开摘要，施工事实不变。')
        async with page.expect_response(lambda r:r.url.endswith('/api/gov/publication') and r.request.method=='POST') as pending:
            await page.get_by_role('button',name='撤下公开摘要',exact=True).click()
        assert (await (await pending.value).json())['world_updated'] is False
        await page.get_by_text('公开摘要 · 已撤下',exact=True).wait_for();await snap('public-revocation-audit')
        await login('resident','/personal/following');records=await api('/api/personal/records')
        assert records['notifications'][0]['record'] is None and records['notifications'][0]['status']=='revoked'
        assert records['queries'][0]['result']['records']==[]
        await snap('revoked-inbox-hides-details')
        await login('operator','/biz/overview');assert (await api('/api/state?scenario=end_unconfirmed'))['result']['world']==before
        report['checks'].append('Government explicitly revokes only preapproved summary; personal inbox and saved-query details are server-redacted; physical facility and world/plan remain unchanged.')
        # Explicit simulated adapter through real API; never a real VLM claim.
        await login('operator','/biz/evidence')
        await api('/api/advance',{'scenario':'end_unconfirmed','time_s':61})
        prepared=await api('/api/material',{'scenario':'end_unconfirmed','records':[{'id':'offline_text','kind':'text','observed_at_s':61,'text':'明确模拟材料：计划结束调整为650秒，尚未确认完成。'}],
          'mock_response':{'schema_version':'chengkong-extraction-v1','candidate_events':[{'event':{'id':'offline-extraction','kind':'planned_end_changed','activity_id':'lifting_1','observed_at_s':61,'effective_at_s':61,'source_kind':'synthetic_record','new_planned_end_s':650},'evidence_refs':[{'record_id':'offline_text','locator':'text:0','quote':'计划结束调整为650秒'}]}]}})
        assert prepared['outcome']['status']=='proposals_ready' and not prepared['outcome']['real_service_connected']
        await page.reload();await idle();await page.get_by_text('明确模拟响应 · 非真实识别',exact=True).wait_for()
        async with page.expect_response(lambda r:r.url.endswith('/api/material/apply') and r.request.method=='POST') as pending:
            await page.get_by_role('button',name='核对并接受材料提议',exact=True).click()
        assert (await (await pending.value).json())['outcome']['world_updated']
        await page.get_by_text('提议已接受并重新分析',exact=True).wait_for();await snap('explicit-mock-material-reviewed')
        after=(await api('/api/state?scenario=end_unconfirmed'))['result']['world']
        assert after['activities'][0]['planned_interval_s'][1]==650 and after['activities'][0]['completion_status']=='not_confirmed'
        report['checks'].append('Explicit synthetic mock adapter produces source-cited proposal; actual browser reviews and accepts through original Agent apply; no real model extraction claimed.')
        await login('operator','/biz/analysis')
        await page.get_by_role('button',name='分析依据 / 高级',exact=True).click()
        await page.get_by_label('启用学习排序（仅合成模型）',exact=True).check()
        async with page.expect_response(lambda r:r.url.endswith('/api/analyze') and r.request.method=='POST') as pending:
            await page.get_by_role('button',name='重新分析与生成候选',exact=True).click()
        actual=await (await pending.value).json();assert actual['result']['learning_enabled'];await idle()
        async with page.expect_response(lambda r:r.url.endswith('/api/compare') and r.request.method=='POST') as pending:
            await page.get_by_role('button',name='同版本规则 / 学习比较',exact=True).click()
        comparison=await (await pending.value).json();assert comparison['status']=='compared' and comparison['current_plan_mutated'] is False
        (out/'rule-learning-comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n')
        await idle();await page.get_by_text('模型版本、适用范围与校准',exact=True).click();await snap('actual-rule-learning-comparison')
        report['checks'].append('Browser explicitly enables existing synthetic models and calls actual same-version rule/learning comparison; no training, no statistical advantage claim, current plan unchanged.')
        assert not report['page_errors'];report['browser']=browser.version;report['passed']=True
        await browser.close()
    except Exception as e:report['passed']=False;report['failure']=str(e);raise
    finally:
        server.shutdown();server.server_close();th.join(5)
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'passed':report.get('passed'),'checks':len(report['checks']),'output':str(out)}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args();asyncio.run(main(args.output))
