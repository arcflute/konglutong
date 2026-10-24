export type Data = Record<string, any>;
export async function request(path:string, body?:Data, signal?:AbortSignal):Promise<any>{
  const controller=new AbortController();let timedOut=false;
  const abort=()=>controller.abort();if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(()=>{timedOut=true;controller.abort()},90000);
  try{
    const response=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,
      headers:body?{'Content-Type':'application/json','X-KLT-Request':'1'}:{},body:body?JSON.stringify(body):undefined});
    const data=await response.json();
    if(!response.ok)throw new Error(data.reason||data.status||`HTTP ${response.status}`);
    return data;
  }catch(e){if(timedOut)throw new Error('请求超过90秒未确认，请刷新核对当前版本；不能据此断言服务器未提交。');throw e}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',abort)}
}
export const query=(path:string,params:Data)=>path+'?'+new URLSearchParams(Object.entries(params).filter(([,v])=>v!==undefined&&v!==null).map(([k,v])=>[k,String(v)])).toString();
export function download(value:unknown,name:string){const u=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
