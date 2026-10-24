# 空路通 v1.1 页面 / API 对照

当前全部16页连接真实Python后端。以下路径均同源；`context` 指 `GET /api/portal/context?scenario=<id>`，返回结构由**服务器会话身份**决定。不会把企业完整world发给个人再前端隐藏。

| 页面 / 路由 | 真实数据与接口 | 操作 → 结果落点 |
|---|---|---|
| P00 `/` 统一门户 | GET `/api/session` | 本地身份选择→POST `/api/session/demo`→相应首页；关闭演示模式则显示未配置 |
| G01 `/gov/overview` 区域态势 | context授权变化、共享分析、几何；GET `/api/portal/view` | 地图定位、详情和时间预览；进入核查台账 |
| G02 `/gov/changes` 城市变化台账 | context.changes/notes/publication_controls | 查询对象/时间/类型；POST `/api/gov/notes` 持久化备注；POST `/api/gov/publication` 显式控制预批准摘要可见性 |
| G03 `/gov/assessment` 影响研判与协同核查 | context.analysis/shared_tasks、共享计划/预约版本 | 冲突详情→来源与检查范围；核查意见→notes；报告入口 |
| G04 `/gov/history` 历史追溯 | GET `/api/portal/replay?scenario&id[&time_s]` | 选择历史知识快照、按生效时刻预览、版本叠加；只读，不写当前状态 |
| G05 `/gov/reports` 专题报告 | GET/POST `/api/reports`、GET `/api/report?id[&format=html]` | 参数/范围生成固定快照→预览、JSON下载、打印HTML/PDF |
| B01 `/biz/overview` 运营总览 | context.result/changes/reservations/versions | 实际统计和任务队列→分析/候选；POST `/api/advance` 推进软件模拟 |
| B02 `/biz/evidence` 变化与证据 | context中来源/字段/事件/审计；GET `/api/template` | `/api/import`→`/api/import/apply`；`/api/field/query`；`/api/evidence`；`/api/material`→`/api/material/apply`，先校验再接受，再重算 |
| B03 `/biz/tasks` 任务与资源 | context.result.world.tasks/resources、完整reservations | 任务/同机筛选、服务与能耗/高度详情；复用导入接受新增任务与预约事务 |
| B04 `/biz/analysis` 影响分析 | baseline_analysis/constraints/evidence；GET `/api/operations` | POST `/api/analyze`、`/api/compare`，真实在途/学习对照；冲突详情到证据与时间 |
| B05 `/biz/maintenance` 航路维护 | 实际候选、独立review、stability_cost、诊断、operations | POST `/api/policy` 变更策略；POST `/api/adopt` 明确采纳→计划/预约/摘要同步重读；旧版本拒绝 |
| B06 `/biz/history` 回放与报告 | portal/replay、GET `/api/export`、reports/report | 历史与当前叠加、事件时间线、完整版本导出、固定快照报告 |
| C01 `/personal/explore` 区域探索 | context公开投影/独立公开版本/白名单几何 | 定位公开对象、公开详情、POST `/api/personal/follow` |
| C02 `/personal/preflight` 航前环境提示 | context；POST `/api/personal/query` | 查询时段→实际覆盖状态、缺口与来源；保存本人记录/下载；不提供个人航路优化 |
| C03 `/personal/following` 关注与提醒 | GET `/api/personal/records` | follow取消/关注、POST `/api/personal/read`；按公开版本去重，撤回后不返回详情 |
| C04 `/personal/records` 记录与反馈 | personal/records中的本人queries/feedback | POST `/api/personal/feedback`→持久ID与待审状态；不改世界；其他用户无法读取 |

## 通用契约与权限

| 接口 | 输入 / 返回关键字段 | 权限与状态 |
|---|---|---|
| GET session | subject、demo_mode、identities、authentication_status | 未登录可读；只有显式demo模式提供预置选择 |
| POST session/demo / logout | identity / 空JSON | demo必须启用；身份由服务器固定配置查找；会话旋转/注销 |
| GET portal/scenarios | scenarios、active_scenario | 登录后按区域/组织过滤；无权限机构返回空范围 |
| GET context | scenario；企业完整计算state，政府过滤投影，个人公开投影 | 401未登录、403越权、404不存在或不授权；不接受role提权 |
| GET portal/view | scenario、time_s；同版本时变geometry | 政府/企业；政府执行前缀按共享task过滤；个人禁止 |
| GET portal/replay | scenario、id、可选time_s | 企业/政府只读；历史保持当时知识，不携带后来核查意见 |
| POST gov/notes | scenario、record_id、text、status | 政府；record_id必须存在；pending/reviewing/reviewed；fact_updated=false |
| POST gov/publication | scenario、object_id、visible布尔、reason | 政府；仅已批准的合成活动；审计和可见性持久化，world_updated=false |
| POST import | scenario、content、format、可选metadata | 企业；JSON/CSV/受限GeoJSON；返回提议ID、字段来源/冲突/错误；不更新world |
| POST import/apply | scenario、proposal_id、可选resolutions | 企业；显式来源裁决及原版本核验→原子更新、重新分析，旧候选失效 |
| POST field/query | scenario、object_id、field、可选content/format/metadata | 企业；geometry.position/facility.height/activity.range/planned_interval/completion/parking_pose六字段；无材料保持unknown，不支持返回unsupported |
| POST analyze | scenario、learned、inflight | 企业；实际调用原pipeline，学习仅排序，冻结前缀为软件模拟 |
| POST adopt | scenario、candidate_id、base_environment_version、base_plan_versions、base_reservation_version | 企业；客户端版本不符client_version_mismatch；事务再次校验实际版本和独立复核，outcome.adopted为实际结果 |
| POST personal/follow、read | scenario/following布尔，或id | 个人；subject来自会话，不能指定他人ID；跨用户read拒绝404 |
| POST personal/query、feedback | scenario、from_s/to_s，或text | 个人；时段有限数校验；query返回coverage，feedback返回received_pending_review和world_updated=false |
| GET/POST reports、GET report | scenario/title/可选record_id；保存report id/parameters/versions | 仅当前报告所有人及其角色/组织范围；HTML转义，下载与预览同快照 |

原 `/api/state`、`export`、`view`、`replay`、`template`、`operations` 和全部原业务写接口也校验企业及机构，不能从旧API绕过。`/health`和构建页面/本地资源可匿名访问，均不含私有业务数据。

POST要求JSON对象、`X-KLT-Request: 1`，限制1MiB；同源/Host检查；HttpOnly/SameSite=Strict会话。状态码与业务outcome分开：例如正常处理一个过期采纳可以返回HTTP200、`adopted:false`，页面必须读取后者。API失败不返回SPA HTML。

撤回后公开活动几何/摘要、提醒详情和保存查询中的对应详情都重新过滤；已独立获准公开的塔吊物理实体仍可见。新上传材料、私有来源描述、企业新增对象不自动加入公共白名单。报告事件筛选不伪装成该事件单独导致的影响：报告明确分析为当前授权完整输入版本的任务检查。

## 明确缺口

没有生产统一身份接入、政府法定审批、真实短信/邮件、跨机构联邦数据、任意发布审核平台或真实个人飞行优化。多模态适配器支持现有项目明确配置；本轮无凭据，真实调用未接入，材料页显示该状态。演示范围为单机构、一个合成园区的多个情景分支，不称全市态势或真实现场效果。
