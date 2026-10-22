'use strict';
// Projection only: every geometry/pose comes from the versioned backend snapshot.
let spaceSnapshot=null,spaceObjects=[];
const detail=(label,value)=>{$('object-detail').innerHTML=`<details open><summary>${esc(label)}</summary><pre>${esc(JSON.stringify(value,null,2))}</pre></details>`;};
function drawSpace(snapshot){
 if(!current||!snapshot)return;spaceSnapshot=snapshot;spaceObjects=[];
 const w=current.result.world;let b=snapshot.bounds_m;
 if($('space-focus').value==='crane'){const first=snapshot.motion_snapshot?.facilities?.[0],points=[];for(const part of first?.components||[]){const g=part.geometry;if(g.type==='capsule')points.push(g.start_m,g.end_m);else if(g.type==='vertical_cylinder')points.push([...g.center_xy_m,g.z_min_m],[...g.center_xy_m,g.z_max_m]);else if(g.type==='aabb')points.push(g.min_m,g.max_m);}if(points.length){const lo=[0,1,2].map(i=>Math.min(...points.map(p=>p[i]))),hi=[0,1,2].map(i=>Math.max(...points.map(p=>p[i])));b={min:[lo[0]-55,lo[1]-55,0],max:[hi[0]+55,hi[1]+55,hi[2]+25]};}}
 const theta=Number($('azimuth').value)*Math.PI/180,c=Math.cos(theta),s=Math.sin(theta),tilt=.55;
 const raw=p=>[c*p[0]-s*p[1],tilt*(s*p[0]+c*p[1])-Math.sqrt(1-tilt*tilt)*p[2]];
 const corners=[];for(const x of [b.min[0],b.max[0]])for(const y of [b.min[1],b.max[1]])for(const z of [b.min[2],b.max[2]])corners.push(raw([x,y,z]));
 const lo=[0,1].map(i=>Math.min(...corners.map(p=>p[i]))),hi=[0,1].map(i=>Math.max(...corners.map(p=>p[i]))),scale=Math.min(900/(hi[0]-lo[0]),490/(hi[1]-lo[1]));
 const point=p=>{const q=raw(p);return [500+(q[0]-(lo[0]+hi[0])/2)*scale,290+(q[1]-(lo[1]+hi[1])/2)*scale];};
 const pts=ps=>ps.map(p=>point(p).map(v=>v.toFixed(2)).join(',')).join(' ');
 let svg='<rect width="1000" height="600" fill="#f6f9fa"/>';
 const line=(ps,color,width=1,dash='')=>`<polyline points="${pts(ps)}" fill="none" stroke="${color}" stroke-width="${width}" ${dash?`stroke-dasharray="${dash}"`:''}/>`;
 for(let i=0;i<=10;i++){const x=b.min[0]+i*(b.max[0]-b.min[0])/10,y=b.min[1]+i*(b.max[1]-b.min[1])/10;svg+=line([[x,b.min[1],0],[x,b.max[1],0]],'#d9e4e8');svg+=line([[b.min[0],y,0],[b.max[0],y,0]],'#d9e4e8');}
 function shape(obj,color,opacity=.35){const g=obj.geometry;if(!g)return;const key=spaceObjects.push(obj)-1;svg+=`<g data-space-object="${key}" tabindex="0"><title>${esc(obj.id+' / '+obj.component)}</title>`;
  if(g.type==='aabb'){const a=g.min_m,d=g.max_m,base=[[a[0],a[1],a[2]],[d[0],a[1],a[2]],[d[0],d[1],a[2]],[a[0],d[1],a[2]]],top=base.map(p=>[p[0],p[1],d[2]]);for(const face of [base,top,...base.map((p,i)=>[p,base[(i+1)%4],top[(i+1)%4],top[i]])])svg+=`<polygon points="${pts(face)}" fill="${color}" fill-opacity="${opacity}" stroke="${color}" stroke-width="1"/>`;}
  else if(g.type==='vertical_cylinder'){const ring=z=>Array.from({length:25},(_,i)=>[g.center_xy_m[0]+g.radius_m*Math.cos(i*Math.PI/12),g.center_xy_m[1]+g.radius_m*Math.sin(i*Math.PI/12),z]);for(const z of [g.z_min_m,g.z_max_m])svg+=`<polygon points="${pts(ring(z))}" fill="${color}" fill-opacity="${opacity}" stroke="${color}"/>`;for(let i=0;i<24;i+=6)svg+=line([ring(g.z_min_m)[i],ring(g.z_max_m)[i]],color,1.5);}
  else if(g.type==='capsule'){svg+=`<polyline points="${pts([g.start_m,g.end_m])}" stroke="${color}" stroke-opacity="${opacity}" stroke-width="${Math.max(2,2*g.radius_m*scale)}" stroke-linecap="round" fill="none"/>`;svg+=line([g.start_m,g.end_m],color,1);}
  else svg+=`<text x="25" y="560">${esc(obj.id)}: unsupported ${esc(g.type)}</text>`;
  svg+='</g>';
 }
 const colors={tower:'#665981',boom:'#735f8d',rope:'#9a7055',load:'#cb8659'};
 snapshot.objects.forEach(o=>shape(o,o.component==='activity_protection'?'#dbad76':'#879aab',o.component==='activity_protection'?.08:.22));
 const motion=Array.isArray(snapshot.motion_snapshot)?[]:(snapshot.motion_snapshot?.facilities||[]);
 for(const f of motion){for(const component of f.components||[]){shape({...component,id:f.facility_id,pose:f.pose,source_ids:f.source_ids,pose_status:f.pose_status},colors[component.component]||'#6b7993',.65);if(f.activity_clearance_m>0){const g=JSON.parse(JSON.stringify(component.geometry)),r=f.activity_clearance_m;if(g.type==='aabb'){g.min_m=g.min_m.map(v=>v-r);g.max_m=g.max_m.map(v=>v+r);}else if(g.type==='capsule')g.radius_m+=r;else if(g.type==='vertical_cylinder'){g.radius_m+=r;g.z_min_m-=r;g.z_max_m+=r;}shape({id:f.facility_id,component:component.component+'_activity_protection',geometry:g},'#d4a06c',.06);}}}
 const selected=(current.saved_candidates||current.result.candidates).find(candidate=>candidate.id===preview);
 function route(task,original=null){let tr=task.trajectory;for(let i=1;i<tr.length;i++){const a=tr[i-1],d=tr[i],same=original?.trajectory.some((p,j,ar)=>j>0&&JSON.stringify(ar[j-1])===JSON.stringify(a)&&JSON.stringify(p)===JSON.stringify(d));svg+=line([a.position_m,d.position_m],original?(same?'#8b9ca7':'#008d89'):'#889ba9',original&&!same?3.2:1.8,original?'':'6 5');if(a.position_m.every((v,j)=>v===d.position_m[j])){const q=point(a.position_m);svg+=`<circle cx="${q[0]}" cy="${q[1]}" r="5" fill="#e5ae52"><title>${esc(task.id)} 等待 ${fmt(d.time_s-a.time_s)}s · z=${a.position_m[2]}m</title></circle>`;}}
  const p=position(task,snapshot.view_time_s);if(p){const q=point(p);svg+=`<circle cx="${q[0]}" cy="${q[1]}" r="5" fill="${original?'#008d89':'#163d55'}"/><text x="${q[0]+8}" y="${q[1]-8}" font-size="12">${esc(task.id)} z=${fmt(p[2])}m</text>`;}
 }
 w.tasks.forEach(t=>route(t));if(selected)selected.tasks.forEach(t=>route(t,w.tasks.find(x=>x.id===t.id)));
 for(const pad of [...w.pads,...(w.waiting_areas||[])]){const q=point(pad.position_m);svg+=`<rect x="${q[0]-4}" y="${q[1]-4}" width="8" height="8" fill="white" stroke="#476376"/><text x="${q[0]+8}" y="${q[1]+14}" font-size="11">${esc(pad.id)} ×${pad.capacity}</text>`;}
 const origin=[b.min[0],b.min[1],0],unit=Math.min(b.max[0]-b.min[0],b.max[1]-b.min[1])*.15;
 [['X 东',[unit,0,0],'#a67267'],['Y 北',[0,unit,0],'#709683'],['Z 高度',[0,0,unit],'#6c85ae']].forEach(([label,delta,col])=>{const end=origin.map((v,i)=>v+delta[i]),q=point(end);svg+=line([origin,end],col,2);svg+=`<text x="${q[0]}" y="${q[1]-8}" fill="${col}" font-size="13">${label}</text>`;});
 svg+='<text x="25" y="582" fill="#587080" font-size="13">虚线：现行计划　青色：候选修改航段　灰色：未改变航段　橙点：等待位置　点击几何查看记录</text>';
 $('space').innerHTML=svg;$('view-time').value=snapshot.view_time_s;$('space-label').textContent=`环境 v${snapshot.environment_version} · T+${fmt(snapshot.view_time_s,0)}s · ${snapshot.temporal_status==='current_known_state'?'当前已知':snapshot.temporal_status==='prediction_from_current_known_rules'?'规则预测':'按当前知识重建'} · ${snapshot.data_kind}`;
}
const timeLabels={information_arrival:'信息到达',event_effective:'事件生效',planned_end:'计划结束（非确认）',confirmed_completion:'确认完成',plan_adopted:'计划采纳'};
window.renderRound3=()=>{drawSpace(current.spatial_view);$('timeline').innerHTML=(current.timeline||[]).map(row=>`<div class="timeline-item ${esc(row.kind)}"><b>T+${fmt(row.time_s,0)}s</b><span>${esc(timeLabels[row.kind]||row.kind)}</span><small>${esc(row.event_kind||row.object_id||row.candidate_id||'')}${row.pending?' · 尚未生效':''}</small></div>`).join('');$('field-ledger').textContent=JSON.stringify(current.result.world.field_evidence||[],null,2);
 $('models').insertAdjacentHTML('afterbegin','<div class="actions"><button id="compare-modes">同一版本：规则 / 学习实际求解对照</button></div><p class="hint">单场景实测，不代表统计增益。规则仍为默认；精算、独立复核与显式采纳始终保留。</p><div id="comparison-result"></div>');$('compare-modes').onclick=()=>action(async()=>{$('comparison-result').innerHTML=`<pre>${esc(JSON.stringify(await api('/api/compare',{}),null,2))}</pre>`;});
 const requests=current.field_requests||[];if(requests.length)$('field-result').innerHTML=requests.map(q=>`<p>${esc(q.object_id)} / ${esc(q.field)} <button data-field-query="${esc(q.id)}">查询此字段请求</button></p>`).join('');
};
$('azimuth').oninput=()=>drawSpace(spaceSnapshot);
$('space-focus').onchange=()=>drawSpace(spaceSnapshot);
$('space').onclick=event=>{const shape=event.target.closest('[data-space-object]');if(shape)detail('几何、姿态与来源',spaceObjects[Number(shape.dataset.spaceObject)]);};
$('view-refresh').onclick=()=>action(async()=>{const view=await api(`/api/view?scenario=${encodeURIComponent($('scenario').value)}&time_s=${encodeURIComponent($('view-time').value)}`);drawSpace(view);notify('只读查看已知运动规则；未推进模拟时间或获取新证据。');});
$('export-result').onclick=()=>action(async()=>{const data=await api('/api/export?scenario='+encodeURIComponent($('scenario').value));const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`chengkong-${data.scenario_id}-v${data.versions.environment_version}.json`;a.click();URL.revokeObjectURL(url);});
$('material-file').onchange=async()=>{const f=$('material-file').files[0];if(f){if(f.size>1000000){notify('材料超过当前1MB请求上限',true);return;}$('import-content').value=await f.text();$('import-format').value=f.name.endsWith('.csv')?'csv':f.name.endsWith('.geojson')?'geojson':'json';}};
$('load-template').onclick=()=>action(async()=>{$('import-content').value=JSON.stringify(await api('/api/template'),null,2);$('import-format').value='json';notify('已载入明确标注的合成材料；尚未解析或更新。');});
function showImportReview(o){$('import-result').innerHTML=`<p>${badge(o.status)} · 当前世界未修改</p><pre>${esc(JSON.stringify(o,null,2))}</pre>`+(Object.keys(o.conflicts||{}).length?'<label>冲突裁决 JSON（每个字段选择 record_id 并填写 reason）</label><textarea id="import-resolutions" rows="4">'+esc(JSON.stringify(Object.fromEntries(Object.keys(o.conflicts).map(k=>[k,{record_id:'',reason:''}])),null,2))+'</textarea>':'')+(o.proposal_id?`<button data-import-apply="${esc(o.proposal_id)}" class="primary">接受校验后的更新并重新计算</button>`:'');}
function materialBody(){return {content:$('import-content').value,format:$('import-format').value,metadata:$('import-metadata').value.trim()?JSON.parse($('import-metadata').value):undefined};}
$('import-prepare').onclick=()=>action(async()=>{const data=await api('/api/import',materialBody());setState(data.state);showImportReview(data.outcome);});
async function fieldQuery(body){const data=await api('/api/field/query',body);setState(data.state);if(data.import_review)showImportReview(data.import_review);$('field-result').innerHTML=`<pre>${esc(JSON.stringify({request:data.request,result:data.outcome},null,2))}</pre>`;notify(`字段补证：${data.outcome.status}。证据不会自动改变当前计划。`);}
$('field-query').onclick=()=>action(()=>fieldQuery({object_id:$('field-object').value,field:$('field-name').value}));
$('field-material-query').onclick=()=>action(()=>fieldQuery({...materialBody(),object_id:$('field-object').value,field:$('field-name').value}));
document.body.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;
 if(button.dataset.importApply)action(async()=>{const data=await api('/api/import/apply',{proposal_id:button.dataset.importApply,resolutions:$('import-resolutions')?JSON.parse($('import-resolutions').value):undefined});setState(data.state);$('import-result').innerHTML=`<p>${badge(data.outcome.status)}</p><pre>${esc(JSON.stringify(data.outcome,null,2))}</pre>`;notify(`材料更新：${data.outcome.status}；约束与候选已按当前版本重新分析。`,!data.outcome.world_updated);});
 if(button.dataset.fieldQuery)action(()=>fieldQuery({request_id:button.dataset.fieldQuery}));
 if(button.dataset.conflict){const conflict=Object.values(current.result.baseline_analysis).flatMap(a=>a.conflicts).find(c=>c.task_id+':'+c.constraint_id+':'+c.segment_index===button.dataset.conflict);detail('冲突时空、规则、证据与检查范围',conflict);$('object-detail').scrollIntoView({behavior:'smooth',block:'center'});}
});
