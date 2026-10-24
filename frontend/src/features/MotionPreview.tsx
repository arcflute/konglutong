import {useEffect,useRef,useState} from 'react';
import {Pause,Play,RotateCcw} from 'lucide-react';
import {useApp} from '../app/context';
import {query,request,Data} from '../api/client';
import {SpatialScene,SceneProps} from '../shared/SpatialScene';
import {Badge,seconds} from '../shared/ui';
/** Preview reads known motion rules. No clock advancement or future event release. */
export function MotionPreview(props:SceneProps){const a=useApp(),[at,setAt]=useState(props.view.view_time_s),[preview,setPreview]=useState<Data|null>(null),[playing,setPlaying]=useState(false),[error,setError]=useState('');const seq=useRef(0);const horizon=a.context?.result?.world.horizon_s||a.context?.horizon_s||[0,900];const known=props.view.known_as_of_s;
 useEffect(()=>{setAt(props.view.view_time_s);setPreview(null);setPlaying(false)},[props.view]);
 useEffect(()=>{const token=++seq.current,ac=new AbortController();if(at===props.view.view_time_s){setPreview(null);return()=>ac.abort()}request(query('/api/portal/view',{scenario:a.scenario,time_s:at}),undefined,ac.signal).then(v=>{if(token===seq.current){setPreview(v);setError('')}}).catch(e=>{if(e.name!=='AbortError'){setError(e.message);setPlaying(false)}});return()=>ac.abort()},[at,a.scenario,props.view]);
 useEffect(()=>{if(!playing)return;const id=setInterval(()=>{if(document.hidden)return;setAt((old:number)=>{if(old+5>horizon[1]){setPlaying(false);return old}return old+5})},800);return()=>clearInterval(id)},[playing,horizon[1]]);
 return <><SpatialScene {...props} view={preview||props.view}/><div className="motion-preview"><div className="toolbar"><button aria-label={playing?'暂停仿真预览':'播放仿真预览'} onClick={()=>setPlaying(!playing)}>{playing?<Pause/>:<Play/>}{playing?'暂停':'运动预览'}</button><input aria-label="运动预览时刻" type="range" min={horizon[0]} max={horizon[1]} step="1" value={at} onChange={e=>{setPlaying(false);setAt(Number(e.target.value))}}/><strong>{seconds(preview?.view_time_s??props.view.view_time_s)}</strong><button aria-label="返回当前知识时刻" onClick={()=>{setAt(known);setPlaying(false)}}><RotateCcw size={14}/></button></div><small>{preview?'依据当前知识的规则预览；未接收后续证据、未推进当前仿真。':'几何来自同版本后端状态。预览不改变碰撞计算结论。'}</small>{at!==(preview?.view_time_s??props.view.view_time_s)&&<Badge tone="info">正在读取所选时刻</Badge>}{error&&<p className="error">{error}</p>}</div></>}
