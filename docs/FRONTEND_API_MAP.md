# 空路通 v1.1 页面 / API 接入表

勘察起点 main / 66cb3e6；用户新增 FRONTEND_DESIGN_SPEC.md 保留。原 HTTP 无身份隔离，必须整体加会话和机构检查，不能只保护新接口。以下是实施契约，完成状态由 FRONTEND_ACCEPTANCE 记录。

|页面|读接口|主要写操作|服务端范围|
|---|---|---|---|
|P00 门户|/api/session|/api/session/demo|显式本地演示开关；正常模式关闭|
|G01 态势|/api/portal/context|无事实写入|授权场景、明确共享任务|
|G02 台账|context、/api/gov/notes|核查备注|政府；独立于世界状态|
|G03 研判|context、notes|核查状态与报告|政府；无采纳权限|
|G04 追溯|/api/portal/replay|无|政府过滤历史快照|
|G05 报告|/api/reports|生成快照报告|政府授权范围|
|B01 总览|context、/api/state|无|场景所属机构|
|B02 证据|state、/api/template|import→import/apply、field/query、evidence|机构，第一层校验后明确接受|
|B03 任务|state、context.reservations|结构化任务导入|机构；原有事务追加|
|B04 分析|state、/api/operations|analyze、compare|机构；实际计算|
|B05 维护|state、operations|adopt、policy|机构；独立复核及提交时版本校验|
|B06 回放|/api/portal/replay、reports|生成报告|机构；历史只读|
|C01 探索|context公开投影|/api/personal/follow|服务端白名单、本人|
|C02 航前提示|context|/api/personal/query|公开范围；保存本人查询|
|C03 关注|/api/personal/records|follow、read|本人，用户/事件/公开版本去重|
|C04 记录|personal/records|feedback|本人；不修改世界|

旧 /api/state、export、view、replay、template、operations 及全部原有写接口统一校验企业会话与场景机构。个人不会收到这些响应。新报告下载同样校验主体与范围。GET API 不作 HTML 回退。HttpOnly/SameSite 会话；POST 同源校验与专用请求头；身份由服务端预置配置决定。
