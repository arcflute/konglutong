"""Verify the supplied logo through the actual production frontend/Python server.

Checks real routes, theme switching, image decoding, navigation collapse, refresh,
source integrity and rendered-mask transparency. Writes only a new run directory.
"""
import argparse,hashlib,json,threading,time
from pathlib import Path
from playwright.sync_api import sync_playwright
from chengkong.web import SimulationApp,make_server
ROOT=Path(__file__).resolve().parents[1]
ROUTES={
 'gov':['overview','changes','assessment','history','reports'],
 'biz':['overview','evidence','tasks','analysis','maintenance','history'],
 'personal':['explore','preflight','following','records']}


def main(out):
 out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
 report={'scope':'supplied logo integration only, real production build and HTTP backend','layouts':[],'screenshots':[],'page_errors':[],'console_errors':[],'asset_responses':[]}
 source=ROOT/'docs/LOGO.png';report['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
 assert source.read_bytes()==(ROOT/'frontend/src/assets/brand-logo-source.png').read_bytes()
 app=SimulationApp(ROOT/'examples/frontend-v1/motion.json',out/'db',None,ROOT)
 server=make_server(app,port=0,demo_identities=True);th=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);th.start();base=f'http://127.0.0.1:{server.server_port}'
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
   ctx=browser.new_context(viewport={'width':1920,'height':1080},device_scale_factor=1);page=ctx.new_page()
   page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
   page.on('console',lambda e:report['console_errors'].append(e.text) if e.type=='error' else None)
   page.on('response',lambda r:report['asset_responses'].append({'url':r.url,'status':r.status}) if 'brand-logo-source' in r.url else None)
   report['browser']=browser.version;report['rendering']='Chromium headless / SwiftShader, DPR 1, default browser zoom 100%'
   def idle():
    page.wait_for_selector('.page-body',timeout=30000);page.wait_for_function("!document.querySelector('.busy-bar')")
   def logo_info():
    return page.locator('header .brand-logo').evaluate('''async el=>{
     const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
     const before=box(el);const image=el.querySelector('image');const src=image.href.baseVal;
     const loaded=new Image();loaded.src=src;await loaded.decode();await document.fonts.ready;
     const s=getComputedStyle(el.closest('.brand'));return {before,after:box(el),brand:box(el.closest('.brand')),header:box(el.closest('header')),src,natural:[loaded.naturalWidth,loaded.naturalHeight],ink:s.getPropertyValue('--brand-logo-ink').trim(),highlight:s.getPropertyValue('--brand-logo-highlight').trim(),ratio:1364/1065,viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,scale:visualViewport.scale,zoom:getComputedStyle(document.body).zoom,document_width:document.documentElement.scrollWidth,mask_count:el.querySelectorAll('mask').length,legacy_icons:document.querySelectorAll('.brand-symbol').length};}''')
   def check(role,path,state):
    value=logo_info();r=value['after'];brand=value['brand'];header=value['header']
    assert value['natural']==[1364,1065] and value['mask_count']==2 and value['legacy_icons']==0,value
    assert abs(r['width']/r['height']-1364/1065)<.002,value
    assert r['x']>=brand['x'] and r['x']+r['width']<=brand['x']+brand['width']+.1,value
    assert r['y']>=header['y'] and r['y']+r['height']<=header['y']+header['height']+.1,value
    assert value['before']==r,('image load shifted layout',value)
    assert value['document_width']<=value['viewport'][0],('page overflow',value)
    report['layouts'].append({'role':role,'path':path,'state':state,**value})
    return value
   def shot(name):
    page.screenshot(path=str(out/(name+'.png')));report['screenshots'].append(name+'.png')
   def ready_map():
    page.wait_for_selector('.cesium-widget canvas',timeout=30000);page.wait_for_function("!document.querySelector('.map-loading')");page.wait_for_timeout(200)
   def login(role):
    page.goto(base+'/',wait_until='networkidle');page.locator('.portal-card.'+role).click()
    name={'gov':'周老师','biz':'林工','personal':'陈同学'}[role]
    page.get_by_role('dialog').get_by_role('button',name=name).click();idle()
   # Render the actual SVG on a read-only canvas at original resolution. Embed
   # the unchanged source only to satisfy SVG-as-image external resource rules.
   def pixel_check():
    return page.locator('header .brand-logo').evaluate('''async el=>{
     const load=src=>new Promise((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=reject;im.src=src});
     const blob=await(await fetch(el.querySelector('image').href.baseVal)).blob();
     const source=await new Promise(resolve=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.readAsDataURL(blob)});
     const original=await load(source),c=document.createElement('canvas');c.width=1364;c.height=1065;const cx=c.getContext('2d');cx.drawImage(original,0,0);const raw=cx.getImageData(0,0,1364,1065).data;
     const clone=el.cloneNode(true);clone.setAttribute('xmlns','http://www.w3.org/2000/svg');clone.removeAttribute('class');
     clone.querySelectorAll('image').forEach(im=>im.setAttribute('href',source));
     const old=el.querySelectorAll('rect');clone.querySelectorAll('rect').forEach((r,i)=>r.setAttribute('fill',getComputedStyle(old[i]).fill));
     const im=await load('data:image/svg+xml;base64,'+btoa(unescape(encodeURIComponent(new XMLSerializer().serializeToString(clone)))));
     cx.clearRect(0,0,1364,1065);cx.drawImage(im,0,0);const d=cx.getImageData(0,0,1364,1065).data;
     let bg=0,bgLeaked=0,solid=0,solidRetained=0,alphaError=0;const colors=new Set();const sample=[];
     const clamp=v=>Math.max(0,Math.min(1,v));
     for(let i=0;i<d.length;i+=4){const r=raw[i],g=raw[i+1],b=raw[i+2],a=d[i+3];
       if(r>=245&&g>=245&&b>=245){bg++;if(a>1)bgLeaked++}
       if(b-r>64&&r<100){solid++;if(a>150)solidRetained++}
       const ref=clamp((245-r)/235)*clamp((b-r-3)/8)*255;alphaError=Math.max(alphaError,Math.abs(ref-a));
       if(a>250&&i%400===0)colors.add(Array.from(d.slice(i,i+3)).join(','));}
     for(const [x,y] of [[50,50],[640,90],[670,700],[940,270],[1075,270],[1000,500]]){const i=(y*1364+x)*4;sample.push({point:[x,y],rgba:Array.from(d.slice(i,i+4))})}
     return {background_pixels:bg,background_leaked:bgLeaked,solid_pixels:solid,solid_retained:solidRetained,max_alpha_reference_error:alphaError,opaque_color_count:colors.size,samples:sample,fill_colors:Array.from(old).map(r=>getComputedStyle(r).fill)};
    }''')
   page.goto(base+'/',wait_until='networkidle')
   report['pixels']={}
   for width,height in ((1920,1080),(1600,900),(1366,768)):
    page.set_viewport_size({'width':width,'height':height});check('portal','/','header')
   page.set_viewport_size({'width':1920,'height':1080});shot('portal-1920x1080')
   assert page.locator('.product-portal>footer .brand-logo').count()==1
   page.locator('.product-portal>footer').screenshot(path=str(out/'portal-footer.png'));report['screenshots'].append('portal-footer.png')
   # Independent filter IDs prevent the portal footer from shadowing its header.
   ids=page.locator('.brand-logo [id]').evaluate_all('(els)=>els.map(e=>e.id)');assert len(ids)==len(set(ids)),ids
   page.evaluate('scrollTo(0,0)');report['pixels']['portal']=pixel_check()
   for role,routes in ROUTES.items():
    login(role);ready_map()
    for width,height in ((1920,1080),(1600,900),(1366,768)):
     page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(150);check(role,page.url,'home');shot(f'{role}-{width}x{height}')
    report['pixels'][role]=pixel_check()
    for route in routes:
     path=f'/{role}/{route}'
     # Direct navigation and hard reload use the production /assets URL.
     page.goto(base+path+'?scenario=end_unconfirmed',wait_until='domcontentloaded');idle()
     for width,height in ((1920,1080),(1600,900),(1366,768)):
      page.set_viewport_size({'width':width,'height':height});check(role,path,'direct')
      if role!='personal':
       button=page.get_by_role('button',name='展开或折叠导航',exact=True)
       button.click();assert 'nav-collapsed' in page.locator('.app-shell').get_attribute('class');check(role,path,'collapsed')
       button.click();check(role,path,'expanded')
      else:
       # Personal uses its full desktop horizontal navigation, not a sidebar.
       assert page.locator('.personal-shell .sidebar nav a').count()==4
     page.reload(wait_until='domcontentloaded');idle();check(role,path,'refresh')
    if role!='personal':
     page.get_by_role('button',name='展开或折叠导航',exact=True).click();shot(role+'-collapsed-1366')
    # The existing brand action still returns to the product portal.
    page.locator('header .brand').click();page.wait_for_url(base+'/');assert page.locator('.portal-header .brand-logo').count()==1
   expected={'portal':'rgb(8, 79, 91)','biz':'rgb(8, 79, 91)','gov':'rgb(103, 180, 255)','personal':'rgb(73, 51, 145)'}
   for role,p in report['pixels'].items():
    assert p['background_pixels']>800000 and p['background_leaked']==0,(role,p)
    assert p['solid_retained']==p['solid_pixels'] and p['solid_pixels']>300000,(role,p)
    assert p['max_alpha_reference_error']<3 and p['opaque_color_count']>20,(role,p)
    assert p['fill_colors'][0]==expected[role],(role,p)
   assert all(r['status']==200 for r in report['asset_responses']) and report['asset_responses']
   asset=page.request.get(report['asset_responses'][0]['url']);assert asset.ok and hashlib.sha256(asset.body()).hexdigest()==report['source_sha256']
   assert page.request.get(base+'/docs/LOGO.png').status==404
   assert not report['page_errors'] and not report['console_errors'],report
   report['passed']=True;browser.close()
 except Exception as e:report['passed']=False;report['failure']=str(e);raise
 finally:
  server.shutdown();server.server_close();th.join(5)
  (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps({'passed':report.get('passed'),'layouts':len(report['layouts']),'screenshots':len(report['screenshots']),'output':str(out)}))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();main(a.output)
