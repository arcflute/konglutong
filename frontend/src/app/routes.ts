export const pages={gov:[['overview','区域态势','区域变化与任务影响'],['changes','城市变化台账','变化有迹可循，核查有据可查'],['assessment','影响研判与协同核查','聚焦共享范围内的影响与核查进度'],['history','历史追溯','回到当时掌握的信息'],['reports','专题报告','固定版本，保留依据与未解决事项']],biz:[['overview','运营总览','施工变化下，保持任务连续'],['evidence','变化与证据','从材料到可追溯的环境更新'],['tasks','任务与资源','任务要求与资源预约，一处查看'],['analysis','影响分析','定位航段、时段与触发约束'],['maintenance','航路维护','先复核，再明确采纳'],['history','回放与报告','每一次调整，都有完整依据']],personal:[['explore','区域探索','了解身边的低空环境变化'],['preflight','航前环境提示','查询公开环境信息与覆盖范围'],['following','关注与提醒','关注的区域，有变化再来看'],['records','记录与反馈','保留查询，也让信息线索有处可达']]} as const;
export type Role=keyof typeof pages;
export const roleNames={gov:'政府研判端',biz:'企业运营端',personal:'个人服务端'};
export const home=(role:string)=>`/${role}/${role==='personal'?'explore':'overview'}`;
