'use strict';
const failureLabels={input_information_insufficient:'输入或约束信息不足',task_requirements_or_resources:'期限、服务或资源条件',candidate_graph_action_window_coverage:'有限航路图 / 动作 / 窗口覆盖',priority_reservation_backtracking_limit:'优先级、预约或回溯限制',timeout:'求解超时',search_budget_exhausted:'搜索预算耗尽',planner_review_semantics:'规划输出被独立复核拒绝',implementation_error:'已定位实现错误',undetermined:'原因仍未确定'};
const semanticLabels={time_varying_with_declared_bounds:'时变占用 · 使用已声明误差界',full_envelope_without_declared_error_bounds:'缺少误差界 · 全范围保守回退',continuous_activity_prohibition:'作业期间持续禁入',persistent_physical_occupancy:'持续物理占用'};
const actionLabels={keep:'保持',detour:'绕行',wait_or_retime:'等待 / 调整通过时刻'};
function drawOperations(data){
 if(!data){$('operations').textContent='该历史记录没有运行时间线。';return;}
 const tracks=data.tracks||[],h=current.result.world.horizon_s,lo=h[0],hi=h[1],x=t=>180+780*(t-lo)/Math.max(1,hi-lo);
 const groups=new Map();for(const row of tracks){const key=row.plan?row.plan+':'+row.task_id:row.id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
 let svg=`<svg viewBox="0 0 1000 ${60+groups.size*28}" role="img" aria-label="运动与任务同轴时间线">`;
 for(let i=0;i<=6;i++){const t=lo+(hi-lo)*i/6;svg+=`<line x1="${x(t)}" y1="25" x2="${x(t)}" y2="${40+groups.size*28}" stroke="#e0e8ed"/><text x="${x(t)}" y="16" font-size="12" fill="#567080">${fmt(t,0)}s</text>`;}
 let n=0;for(const [key,rows] of groups){const y=40+28*n++;svg+=`<text x="4" y="${y+4}" font-size="11" fill="#355365">${esc(key)}</text>`;for(const row of rows){const [a,b]=row.interval_s;const color=row.kind==='motion_validity'?'#8a70a5':row.kind==='continuous_prohibition'?'#c48263':row.kind==='flight'?(row.plan==='candidate'?'#008d89':'#94a6b0'):'#d4a34f';svg+=`<rect x="${x(Math.max(lo,a))}" y="${y-7}" width="${Math.max(1,x(Math.min(hi,b))-x(Math.max(lo,a)))}" height="13" rx="2" fill="${color}"><title>${esc(row.kind)} ${fmt(a,2)}–${fmt(b,2)}s ${esc(row.semantics||'')}</title></rect>`;for(const p of row.motion_program||[]){svg+=`<line x1="${x(p.interval_s[0])}" y1="${y-9}" x2="${x(p.interval_s[0])}" y2="${y+9}" stroke="#39285a"/>`;}}}
 for(const e of current.timeline||[]){if(e.kind==='information_arrival')svg+=`<line x1="${x(e.time_s)}" y1="25" x2="${x(e.time_s)}" y2="${40+groups.size*28}" stroke="#bd7d49" stroke-dasharray="3 4"><title>信息到达 ${fmt(e.time_s)}s ${esc(e.event_id)}</title></line>`;}
 svg+=`<line x1="${x(data.known_as_of_s)}" y1="25" x2="${x(data.known_as_of_s)}" y2="${40+groups.size*28}" stroke="#173e52"/></svg>`;
 $('operations').innerHTML=`<p class="small">环境 v${data.environment_version} · 已知至 ${fmt(data.known_as_of_s)}s · ${esc(data.candidate_id||'只显示现行计划')}</p>`+svg+'<div class="small">紫：设备运动有效期　橙褐：持续限制　灰：现行飞行　青：候选飞行　黄：等待或服务</div>';
}
window.renderRound4=()=>{
 const r=current.result,w=r.world,p=w.planning_policy||{},e=r.maintenance_explanations||{};
 $('planning-mode').value=p.mode||'conservative';$('action-policy').value=p.action_order?.[1]==='detour'?'keep_detour':'legacy';
 $('learned').checked=r.learning_enabled===true;$('inflight').checked=r.analysis_mode==='inflight_simulation';
 for(const id of ['time','timeRange','view-time']){$(id).min=w.as_of_s;$(id).max=w.horizon_s[1];} $('view-time').min=w.horizon_s[0];$('timeRange').step=1;
 $('recovery-status').textContent=current.recovery_status?`单实例状态：${current.recovery_status.status} · 输入 / 规则 / 模型已绑定`:'历史状态 · 只读';
 let html='<h3>维护决策依据</h3>';
 const complete=e.baseline_completion;if(complete)html+=`<p>初始对象检查：${complete.complete_task_reports} / ${complete.task_reports} 项任务完成检查，未完成比例 ${fmt(100*complete.task_report_unchecked_fraction)}%。任务要求、能耗和联合预约另行复核。</p>`;
 if(r.failure_diagnosis){const d=r.failure_diagnosis;html+=`<div class="diagnosis"><b>当前暂缓采纳：没有通过全部复核的联合候选</b><p>${d.categories.map(c=>esc(failureLabels[c]||c)).join('；')}</p><p class="hint">这些是有记录的限制因素，不是全局不可行证明。${d.unresolved_task_constraint_pairs} / ${d.required_task_constraint_pairs} 个任务—约束关系尚未得到完整几何判断。</p><details><summary>失败证据、任务条件及检查范围</summary><pre>${esc(JSON.stringify(d,null,2))}</pre></details></div>`;}
 const selected=(e.candidates||[]).find(c=>c.candidate_id===preview)||(e.candidates||[])[0];
 if(selected){html+=`<p><b>${esc(selected.candidate_id)}</b> · ${esc(selected.selection_reason||'先满足硬约束，再按真实变更代价排序')}</p>`+table(['任务 / 选择','触发与要求','实际变化 / 等待'],selected.actions.map(a=>`<tr><td>${esc(a.task_id)}<br><b>${esc(actionLabels[a.action]||a.action)}</b></td><td>${a.trigger_constraints.map(esc).join(', ')||'查看任务 / 联合条件'}<br>期限 ${fmt(a.deadline_s)}s · 保留 ${a.mandatory_services.length} 个服务点</td><td>到达偏移 ${fmt(a.changes.arrival_shift_s,2)}s · 增距 ${fmt(a.changes.distance_delta_m,2)}m · 增耗 ${fmt(a.changes.energy_delta,2)}<br>${a.wait_and_service_intervals.map(x=>`${fmt(x.interval_s[0],2)}–${fmt(x.interval_s[1],2)}s @ (${x.from_m.map(v=>fmt(v)).join(',')})m ${x.kind==='service_or_wait'?'服务 / 等待':x.kind==='ground_wait'?'地面等待':'空中等待'}`).join('<br>')}</td></tr>`));html+=`<details><summary>通行窗口、预约资源、保护误差和复核条件</summary><pre>${esc(JSON.stringify(selected,null,2))}</pre></details>`;}
 html+='<h4>区域规则的含义</h4>'+table(['约束','有效时间','通行含义'],(e.constraint_semantics||[]).map(c=>`<tr><td>${esc(c.constraint_id)}</td><td>${c.interval_s.map(v=>fmt(v)).join('–')}s</td><td>${esc(semanticLabels[c.semantics]||c.semantics)}<br><span class="small">${c.source_ids.map(esc).join(', ')}</span></td></tr>`));
 html+=`<details><summary>本次计算开销与模型适用范围</summary><pre>${esc(JSON.stringify({timings:r.timings,learning:r.learning_applicability},null,2))}</pre></details>`;$('decision-explanation').innerHTML=html;
 if(r.learning_applicability?.status==='out_of_training_scope')$('models').insertAdjacentHTML('afterbegin','<p class="diagnosis">当前含动态运动、同机连续任务或多次事件维护，超出旧合成模型训练范围。学习仅为实验排序；旧结束时间覆盖率不适用于本轮分布。</p>');
 drawOperations(r.operation_timeline);
};
$('apply-policy').onclick=()=>action(async()=>{setState(await api('/api/policy',{mode:$('planning-mode').value,action_policy:$('action-policy').value}));notify('规划策略已按版本更新并重新计算；现行任务仍需显式采纳才会改变。');});
$('align-timeline').onclick=()=>action(async()=>{const id=preview||current.result.candidates[0]?.id;drawOperations(await api('/api/operations?scenario='+encodeURIComponent($('scenario').value)+(id?'&candidate_id='+encodeURIComponent(id):'')));});
