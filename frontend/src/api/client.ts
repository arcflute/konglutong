export type Data = Record<string, any>;
export async function request(path:string, body?:Data, signal?:AbortSignal):Promise<any>{
  const response=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',signal,
    headers:body?{'Content-Type':'application/json','X-KLT-Request':'1'}:{},body:body?JSON.stringify(body):undefined});
  const data=await response.json();
  if(!response.ok)throw new Error(data.reason||data.status||`HTTP ${response.status}`);
  return data;
}
export const query=(path:string,params:Data)=>path+'?'+new URLSearchParams(Object.entries(params).filter(([,v])=>v!==undefined&&v!==null).map(([k,v])=>[k,String(v)])).toString();
export function download(value:unknown,name:string){const u=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
