# 数据契约：首轮可执行版本

代码以 Python 3.10 标准库、JSON 对象和 `TypedDict` 为边界，导入验证见 `src/chengkong/contracts.py`。本契约描述当前实现，不将协议定义视为已训练功能。原始示例保持 `startup-example-v1`；可见世界为 `world-v1`；命令行结果为 `chengkong-result-v1`。当前导入器仅接受明确标记的合成数据。

## 1. 公共语义与错误

- ID 必须非空、稳定，字符限 ASCII 字母数字与 `_.:-`；世界对象、任务与来源 ID 不相撞。对象引用在导入时校验。设施 ID 与活动 ID 分开，任务版本与环境版本分开。
- 空间为局部米制右手坐标，轴为 `x_east/y_north/z_up`；高度基准 `local_flat_ground_z0`，没有地理定位。不把海拔、离地高度、英尺或经纬度自动转换为本契约坐标。
- 时间为 `synthetic_t0` 起的非负有限秒数，区间为 `[start,end)`。不能用布尔值、字符串、NaN 或无穷数充当数值。原始轨迹时间严格递增，连续点间线性运动，同位置不同时间表示停留。
- 空间接触计为冲突；活动结束瞬间不再属于该活动区间。单瞬间几何接触的冲突证据可能为 `[t,t]`，另有 `contact_at_single_instant=true`，它是接触证据而非非空占用区间。
- 当前几何为 `aabb(min_m,max_m)`、`vertical_cylinder(center_xy_m,radius_m,z_min_m,z_max_m)`、`capsule(start_m,end_m,radius_m)`。未知几何 `null`、不支持的几何类型原样保留，约束标记 `geometry_status=unknown/unsupported`；相关时段必须报告 `not_checked`，不能略过后声称无冲突。
- `ContractError.status=invalid_input` 表示结构、有限值、时序或引用不合法；`UnsupportedData.status=unsupported` 表示当前不支持的坐标、时间、事件或模式。CLI 会将这两种异常输出为带具体原因的输入错误结果。未知几何的安全语义与导入异常不同：保留对象使下游知道检查未完成。
- `environment_version` 随有效的新记录单调递增；`plan_version` 仅在显式采纳改变任务时递增。`scenario_id` 是仿真分支标签；`rule_version=rules-v1`，无学习模型时 `model_version=none`。模型排序不能免除独立复核。

## 2. 世界状态、来源与事件

`load_example(path)` 校验并返回原始输入。`visible_world(raw, scenario_id, as_of_s)` 是仿真边界，只释放 `observed_at_s <= as_of_s` 的事件，向分析/规划传入的世界不包含 `scenario_definitions_for_simulator_only`。示例决策时刻范围为 `[60,900]`；不允许回推 t=60 之前未提供的快照。

| 对象 | 当前字段和约束 |
|---|---|
| 世界 | 原快照的 `bounds_m/buildings/pads/facilities/activities/tasks/known_sources/scene_relations`，加 `schema_version/as_of_s/horizon_s/coordinate_system/time_system/environment_version/scenario_id/rule_version/model_version/event_log/information_gaps/assumptions/data_kind/is_real_case` |
| 来源 | `id/kind/observed_at_s`；事件新增来源另含 `effective_at_s/event_id/information_type`。事件来源类型明确为合成记录断言。观测时间不晚于快照时刻 |
| 建筑 | `id/geometry/source_id` |
| 起降点 | `id/position_m/capacity/ground_wait_allowed`；容量为正整数，不虚构任务开始前的占用 |
| 设施 | `id/type/installed/removal_confirmed/physical_geometry/source_id`，拆除时增加 `removal_confirmed_at_s`；空几何列表会生成未知几何约束 |
| 活动 | `id/facility_id/source_id/planned_interval_s/completion_status/completion_confirmed_at_s/activity_envelope/end_time_uncertainty`；状态只支持 `not_confirmed/confirmed`。未知完成时间保持 `null` |
| 语义关系 | `subject/predicate/object`，如设施 `performs` 活动、活动 `supported_by` 来源；引用必须存在 |

`apply_event(world,event)` 先校验，返回新世界，不原地修改。相同 ID、相同内容的事件回放幂等；同 ID 不同内容拒绝。当前支持：

| kind | 必需字段与行为 |
|---|---|
| `planned_end_changed` | `id/activity_id/observed_at_s/effective_at_s/source_kind/new_planned_end_s`；仅修改计划，不确认结束，不重新打开已完成活动 |
| `activity_completion_confirmed` | 公共事件字段及 `activity_id`；用有效时间关闭活动。`facility_removed=true` 不被接受，设施必须走独立事件 |
| `facility_removal_confirmed` | 公共事件字段及 `facility_id`；要求关联活动已确认结束，且结束不晚于拆除时刻；物理占用保存为历史区间 |

当前仅处理有效时间不晚于观测时间的已发生更新。未来才生效的事件明确返回 `unsupported`，下一阶段需要独立待生效队列；不能把提前收到的未来完成声明当成已完成事实。新增设施、尺寸修改、暂停/恢复等事件仍待扩展。

事件日志保存 `event/object_id/observed_at_s/effective_at_s/before/after/changed_fields/old_environment_version/new_environment_version/source_ids/scenario_id/rule_version/affected_object_ids`。新来源 ID 为 `source:<event_id>`。原始断言及全部来源引用保留。现阶段事件后运行全量重算，关联子图增量计算尚未实现。

三分支在 t=60 除标签外可见世界相同。按期确认在 t=360 释放；延期记录在 t=330 将计划结束改为 600，t=600 才释放完成确认；未确认分支始终未知。900 是检查时域末端，不是实际完成时刻，也没有情景发生概率。

## 3. 时空约束

`generate_constraints(world)` 返回 `id/object_id/kind/geometry/geometry_status/interval_s/source_ids/environment_version/scenario_id/rule_version/model_version/reason/information_type/observed_at_s/generated_as_of_s/validity_horizon_s/protection_semantics`。

- `observed_at_s` 为支撑来源的最晚实际观测时间，`generated_as_of_s` 为本次世界计算时刻，两者不混用。
- `kind=physical`：建筑与设施分量，ID 如 `physical:crane_1:0`。作业确认结束后保留设施；确认拆除仅关闭设施有效区间，历史占用保留。
- `kind=activity`：ID 如 `activity:lifting_1`，另含 `planned_interval_s/completion_status/end_is_confirmed/calibrated/beyond_horizon_status`。未确认时从计划开始保守占用至时域结束，`end_is_confirmed=false`，时域之外仍 `unknown`。
- 保护体的半径由任务指定，并由检查器只膨胀一次。活动圆柱本身是输入声明的合成保守外包络；圆柱端盖边缘再用径向/轴向保守膨胀，可能增加误报，不能当作校准置信区间。

## 4. 飞行任务、冲突与复核

任务保留 `id/aircraft_id/plan_version/priority/departure_time_s/deadline_s/launch_pad_id/landing_pad_id/mandatory_services/trajectory/nominal_horizontal_speed_mps/nominal_vertical_speed_mps/protection_radius_m/energy_assumptions`。

服务包含 `id/position_m/duration_s/position_tolerance_m/window_s`，必须按顺序实际停留，不允许候选删改任务要求。能源仅为 `synthetic_energy_unit`，参数为 `capacity/airborne_cost_per_s/ground_wait_cost_per_s/required_landing_reserve`。候选可增加 `actual_departure_time_s/ground_wait_interval_s/planning_energy_used`，复核重新计算而不信任缓存能耗。延后时地面等待从原始出发时刻开始，计入轨迹、碰撞、能耗和起降点容量；更早准备阶段在当前范围外。

`check_task(task,constraints,world)` 返回任务与环境版本、`status/conflicts/unchecked/associations/screened_out_pairs/continuous_pairs_checked/checks_completed/scope`。状态为 `conflict/clear/not_checked`，有冲突且同时有未检查项时仍保留 `unchecked` 和未完成标记。`clear` 仅表示声明检查范围内未发现冲突。

单条冲突证据包含 `task_id/segment_index/constraint_id/object_id/kind/interval_s/entry_position_m/exit_position_m/protection_radius_m/method/reason/source_ids/environment_version/scenario_id/rule_version`。任务—约束关联记录筛选方法；联合复核额外报告飞机间连续间距、同一飞机重复预约和起降点容量冲突，字段有 `task_ids/resource_id/aircraft_id/required_separation_m` 等适用项。

## 5. 维护候选、版本冻结与显式采纳

`generate_candidates(world,constraints)` 生成完整联合计划候选，原世界任务保持不变。当前候选字段为：

| 类别 | 字段 |
|---|---|
| 身份与动作 | `id/kind/status`，`kind=retain/delay/detour`；尚未复核时 `pending_review` |
| 冻结输入 | `base_environment_version/base_plan_versions/rule_version/model_version/scenario_id/valid_interval_s/constraints_used` |
| 计划与变化 | `tasks/changes`；变化含 `task_id/changed/departure_shift_s/arrival_shift_s/distance_delta_m` |
| 求解记录 | `searches` 中包含每任务状态、扩展数、节点数、算法名；`ranking_source=deterministic_rule_v1` |
| 适用条件 | `conditions`，经流水线后附 `review` |

搜索在有限图和预算下使用安全时间区间与带能耗的标签。状态区分 `found/search_budget_exhausted/no_solution_in_bounded_graph/task_already_started/unsupported_route_profile` 等，不把未找到候选写为全局不可行。当前对受影响且已开始的任务不倒改历史轨迹，返回需要运行人工复核的状态；真实在途滚动修复待实现。

`verify_candidate(candidate,world,constraints)` 从完整世界重新检查全部候选任务、设施、活动、任务要求和资源。返回 `status/feasible/checks_completed/violations/unchecked/task_reports/task_metrics/joint_report/conditions`，并携带版本。状态为 `conditionally_feasible/rejected/not_checked/needs_recheck`。版本、场景或规则/模型变化要求复核；未检查不等于可采纳。

`PlanStore` 在本地 SQLite 将 `current_state`、`proposals`、`history` 分开。`save_candidate` 只存建议。`adopt(candidate, explicit=False)` 默认返回 `explicit_adoption_required`，不更新现行计划。显式采纳在一个事务中对当前版本重新复核并更新任务、版本和起降点预约。成功返回 `status=adopted/adopted=true/record`，记录包含 `candidate_id/explicit/before_plan_versions/after_plan_versions/environment_version/conditions`；失败保留原计划。`replace_world` 保存证据更新历史、保留现行任务，标记 `needs_recheck`。这是本地软件 API，不是外部飞行调度指令。

## 6. 补证与学习接口

`generate_requests` 使用未确认字段的确定性规则触发，当前支持第二层 `conflict_analysis` 和第三层 `maintenance_decision` 的统一请求来源标签；后者的实际决策价值估计仍为 `not_implemented`。

请求含 `id/source_layer/object_id/field/related_task_ids/required_evidence/possible_impact/deadline_s/max_queries/environment_version/scenario_id/rule_version/model_version/trigger/allowed_source`。查询只接受 `world.known_sources/event_log` 中已可见的记录，不接收原始仿真分支配置。结果含 `status/answer/evidence/queries_used/query_cost/remaining_queries/as_of_s/queried_at_s/world_updated` 及版本，状态为 `resolved/unknown/budget_exhausted/deadline_exceeded/stale_request/unsupported`。无确认则 `answer=null`，不修改世界。现有可见确认可被复用；本轮尚无外部查询适配器和自动补证回写循环。

`learning.py` 定义可替换的 `MultimodalParser.parse`、`SensitivityModel.predict`、`AssociationModel.predict`、`UncertaintyCalibrator.calibrate`、`RepairRankingModel.rank`。公共结果有 `capability/status/model_version/output/fallback/trained/reason/safety_authority`。VLM/LLM 返回 `not_configured`；XGBoost、GNN、分位数/共形校准、学习修复排序和历史演化学习为 `not_trained`。规则回退显式标明 `deterministic_rule/explicit_relation_graph`，不输出伪模型分数。校准覆盖率、区间、分布假设均为空，样本数为 0。

训练集必须按完整情景/事件划分，校准与测试隔离；下游使用上游预测时需要折外特征。在线输入禁止包含未来事件、真实未来结束、事后修复结果。当前尚未实现训练、校准和数据划分流水线，接口约定不代表模型能力已经实现。


## 7. 本轮补充边界与输出

`pipeline.run_analysis(raw,scenario_id,as_of_s)`同时给出`baseline_analysis`（仅任务对施工对象的检查）、`baseline_joint_analysis`（联合资源）和`baseline_full_review`（含边界、服务、能耗和资源的完整原计划复核）。三者范围不同，不能把单个对象分析的clear扩大成整体通过。

冲突另携带`plan_version/model_version/observed_at_s/trigger/unprotected_overlap_intervals_s`。trigger区分`physical_intersection/activity_occupancy/protection_margin_only`；保留未膨胀几何的重叠时间，便于解释保护余量触发。

规划器记录`search_diagnostics`中的失败分支；不支持或缺少几何时返回`unsupported_or_incomplete_search_input`，即使该对象与原计划不同时，也不允许候选搜索将它静默忽略。节点区间/边检查连续，运动预约冲突后以1秒步长有限重试；几何使用1e-9容差，搜索边界外移使用1e-6秒与相应保守比较。该数值策略和有限图没有完备性/最优性保证。多容量起降点在搜索阶段仍保守按单占用阻塞，独立复核按真实声明容量计数。

同一飞行器的重叠任务会报双重预约；不重叠的多任务复用目前缺少转场/补能契约，报告`aircraft_turnaround_unmodeled`并保持未检查，不能默认回满电或瞬移。当前包内三项任务各有独立飞行器，不受此限制。采纳更新不改变场景分支，不允许观测时间倒退；已启动任务的轨迹和等待被修改时，即使版本相同也拒绝采纳。

## 9. 第二轮：双时间事件、增量依赖和补证闭环（覆盖上文首轮对应限制）

当前 `world-v2`、`rules-v2` 支持 `advance_world(world, as_of_s, events=[])`。纯模拟时钟推进不增加环境版本；收到新事件增加版本；已接收的待生效事件在有效时间到达时增加版本并写入 `activation_log`。函数返回新对象，不改写传入快照。`tasks` 不属于施工事件重建范围，已采纳计划和模拟在途进度不会被初始施工快照覆盖。

第一层分别维护：

- `observed_events`：只有已经到达的原始记录，不包含未释放的仿真分支。
- `pending_events`：已到达但有效时间尚未来临的记录；未来完成声明在此期间不释放活动占用。
- `initial_state`：设施、活动、已知来源及语义关系的重建起点，不含任务计划。
- `occupancy_history`：按有效时间排序的几何/活动状态区间；约束追加 `geometry_version`。有几何变化时 ID 如 `physical:crane_1:0:v1`，旧几何保留在旧有效时段。
- `event_log`：收件时的 `before/after`、`received_at_s`、`observed_at_s`、`effective_at_s`、`status_at_receipt` 和环境版本。迟到记录使当前知识重建历史有效区间，但不重写以前的事件日志或调用者保存的决策快照。

新增事件仍必需 `id/kind/source_kind/observed_at_s/effective_at_s`：

| kind | 字段及实际行为 |
|---|---|
| `facility_added` | `facility={id,physical_geometry,...}`；从有效时刻开始物理占用，来源由事件统一登记 |
| `facility_geometry_changed` / `facility_pose_changed` | `facility_id/physical_geometry`，可带 `pose/parameters`；明确提供新几何，姿态文字或角度不能替代米制几何 |
| `activity_started` | 新增时带 `activity={id,facility_id,planned_interval_s,activity_envelope}`；已有活动使用 `activity_id`；开始不确认结束 |
| `activity_paused` / `activity_resumed` | `activity_id`；可带明确 `activity_envelope`。暂停缺少新包络时保留原保守包络，不能自动消除占用 |
| `activity_envelope_changed` | `activity_id/activity_envelope`；`null` 表示未知，必须进入未检查状态 |
| `planned_end_changed` | 仅改计划，不能重启已经确认结束的活动；迟到但有效时间早于完成的计划变更可进入当前历史解释 |
| `activity_completion_confirmed` | 关闭活动区间，物理设施仍在；不允许捎带拆除 |
| `facility_removal_confirmed` | 要求关联活动已确认结束，单独关闭设施物理区间 |

同一有效时间按新增设施、开始活动、普通变化、完成、拆除的依赖顺序处理，再按到达时间及 ID 确定次序。相同 ID 和内容幂等，不同内容拒绝；失败更新不留下部分状态。相互矛盾的完成/拆除时间要求明确的数据纠错工作流，当前不会自动选择有利的答案。未发生/未观测的未来真值始终不传入决策世界。

`incremental.DependencyEngine.analyze(world,candidates=[])` 返回 `constraints/baseline_analysis/joint_analysis/dependencies/cache/changed_constraint_ids/invalidated_candidate_ids`，`update` 为同一入口。按任务和约束的完整可见内容、规则/模型/情景/时域缓存任务—约束检查；新增和变化几何对所有任务检查，防止旧关联漏掉新冲突。联合资源仍全量复核；候选依赖暂保守扩展至全部约束，环境或计划版本变动时标记失效，缓存不赋予采纳权限。针对相同版本、变化几何、未知几何、任务修改和时钟推进的用例将结果与全量报告逐字段比较。

`generate_requests(..., source_layer='conflict_analysis'|'maintenance_decision', candidates=[])` 实际计算两种明确分支：未确认活动继续占用，或者假设在 `max(as_of_s,planned_end_s)` 已有确认。后一分支只是查询价值分析，绝不进入世界状态或充当证据；不提供虚构分支概率。冲突敏感性依据原任务判断变化触发；维护决策价值依据保留/修复或暂缓动作变化触发；传入已求解候选时，先在实际世界独立复核任务/资源/版本，再单独重算两分支几何判断与字典序偏好；分支候选偏好仅用于补证优先级，不赋予可行或可采纳状态。两个分支都不改变判断/建议时不请求。当前仅针对完成状态，未实现全部未知几何字段的查询价值。

`query_evidence(world,request,evidence_source=None,timeout_s=5)` 可读世界中已见记录，或显式提供 `{id,kind,records:[event,...]}` 的证据源。`kind` 只接受 `available_records` 或 `synthetic_evidence_source`。只释放 `observed_at_s<=world.as_of_s` 且有效时间已到达的完成记录。模拟源可用 `simulated_delay_s` 验证超时，字段明确标识为模拟延迟，函数不会伪装实际网络服务。冲突记录返回 `conflicting_evidence`，无记录、超时、超预算、到期均保留未知。请求有截止时间、单位成本、最大查询数、状态和冻结版本。

解析到的有效记录返回 `status=resolved`、`proposed_event` 和来源引用，仍然 `world_updated=false`。调用 `apply_evidence_result(world,result)` 时再次核对版本、请求对象、答案与原始记录匹配，随后走 `apply_event`，才产生新环境版本与占用。补证失败是无状态变化操作。字段真实性依赖用户提供的材料；本地合成证据源验证的是软件闭环，不证明现实施工已结束。

## 10. 第二轮结果、采纳与本地HTTP

结果schema为`chengkong-result-v2`，保留v1核心字段，增加`baseline_full_review`、`incremental.dependencies/cache/invalidated_candidate_ids`、`learning_advice/learning_enabled`及在途模式。候选带`base_reservation_version`、修复集合与扩展历史、真实`stability_cost`和选择理由；其ID由内容、版本与分析时刻生成。学习启用和模型版本也参与身份，不与规则结果混用。

`PlanStore`以SQLite `BEGIN IMMEDIATE`核对环境/各任务计划/预约/规则/模型/情景并在事务内重新生成约束复核。成功时同时提交当前world、计划版本、预约版本和历史；任何异常回滚全部写入。`state_revision`用于环境/时间写入的CAS，避免覆盖并发采纳后的计划。候选建议保存在proposals，不采纳就不替代现行任务；同ID不同内容拒绝。decisions保存当时完整输入/结果，后来证据不得回写旧行。

本地HTTP仅loopback，默认端口8765，JSON body不超过1MiB，拒绝非有限数和非对象顶层。除GET外操作均为POST JSON，带`scenario`（默认end_unconfirmed）。Web是软件仿真，没有飞控接口。

| 路径 | 输入与行为 |
|---|---|
| GET `/api/scenarios`、`/api/state?scenario=...` | 可用分支；当前可见world、真实分析、候选、审计与历史，不传隐藏未来事件 |
| POST `/api/analyze` | `inflight`、`learned`布尔；按明确开关执行实际计算 |
| POST `/api/advance` | `time_s`，只前进，按到达时刻释放对应仿真记录 |
| POST `/api/event` | `event`，第一层校验、版本化写入、增量分析与维护 |
| POST `/api/adopt` | `candidate_id`，显式采纳；失败返回review/version原因 |
| POST `/api/evidence` | `request_id`，可选显式`source`；服务端持有预算，失败不改未知 |
| POST `/api/material` | `records`，可选明确标注的`mock_response`；返回已校验待接受提议，不直接更新 |
| POST `/api/material/apply` | `proposal_id`，核对版本后受控更新；证据引用随事件持久保存 |
| GET `/api/replay?scenario=...&id=...` | 按历史记录的可见world重新检查，`read_only=true`；不修改live或原决策，当前实现不重新生成历史候选 |
| GET `/health` | 服务状态及`flight_control_connected=false` |

历史回放用保存的world和其规则/模型版本标识，按当前实现重做检查，不将后来证据注入。完整当时决策另外存储在decisions。数据库只适用于本地单实例原型，尚未实现多实例分布式会话与生产级认证。

## 11. 多模态结构化协议

仅项目内`config/multimodal.local.json`启用`structured_http`适配器，api_key_env限定`CHENGKONG_VLM_API_KEY`。POST服务载荷为`{records,context,response_schema:"chengkong-extraction-v1"}`，context包含当前时间、环境版本、情景、坐标与时间系统。响应必须为对象，结构示例：

```json
{"schema_version":"chengkong-extraction-v1","unknown_fields":[],"candidate_events":[{"event":{"id":"example_event","kind":"planned_end_changed","activity_id":"lifting_1","effective_at_s":60,"observed_at_s":60,"source_kind":"record_assertion","new_planned_end_s":700},"evidence_refs":[{"record_id":"material_1","locator":"text:0","quote":"计划结束调整为700秒"}]}]}
```

材料为`{id,kind,observed_at_s,text/image_ref/frame_ref,metric_reference?}`，kind为text/image/video_frame；引用必须定位已提供且当前可见的材料。图像带米制几何时，metric_reference要求`unit=m`、正`scale_m_per_pixel`、三维`coordinate_origin_m`、与world相符的`axes`和`height_datum`、可见`source_record_id`。该校验只检查依据存在与结构一致，不认证现实测量正确。

受控接受时event保存`evidence_refs`和引用材料的小型原始记录，world事件/known_sources进入SQLite历史；无接受时只在会话中保留提议。超时/连接失败/无效JSON/错误引用/未知字段都不会转为有利几何。模拟响应标记explicit_mock和synthetic_mock，真实调用只在实际配置后发生，本轮未接入真实提供方。

MaterialAgent还输出`evidence_requests/field_review/material_query_scope`。完成状态缺项复用两类统一请求，initiator=material_agent；HTTP材料流程按ID合并，不增加预算或延长期限。后续使用同一/api/evidence及第一层校验写回。无法定位对象时object_id=null并要求人工定位；任意高度/坐标证据适配器尚未接通，不能从unknown自行生成几何。

同机累计余量以`baseline_joint_analysis.aircraft_transitions`及候选`review.joint_report.aircraft_transitions`为准；单任务task_metrics中的landing_energy_remaining是满容量起算的局部指标，不可当成同机累计余量。周转地面驻留会进入容量与连续空间分离复核，禁等起降点不能用于长时周转等待；地面运输途中避障与载运车辆容量不在此简化模型内。

周转驻留的对象与独立参考报告分别位于joint_report.stationary_object_reports / stationary_reference_reports；在途复核只在这些字段呈现未来检查，complete_stationary_*保留完整历史检查，historical_conflicts单列已发生冲突。证据查询预算、待接受材料及缓存目前属于单进程会话；施工事件、现行world、预约、候选提案与完整决策记录在SQLite持久保存，尚无跨实例会话恢复。

## 第三轮扩展契约

- `facility.motion_model: crane-motion-v1`：米、秒、度、有来源与速度界的回转/升降程序；`facility_motion_changed`按有效时间分段。部件/暂停/停车规则、误差界和unsupported策略见[MOTION](MOTION.md)。约束含保守`geometry`和实际`motion`，独立复核不能漏传运动信息。
- `chengkong-material-v1`：明确coordinate_system/time_system/data_kind/sources/records；JSON/CSV/受限GeoJSON；六字段白名单、人工来源裁决及追加任务边界见[IMPORTS](IMPORTS.md)。准备结果冻结环境、计划、预约、规则与模型版本；接受阶段再次验证，过期提议拒绝。
- `POST /api/import`：content、format、可选metadata→解析/field_review/提议，不写回；`POST /api/import/apply`：proposal_id、可选resolutions→显式接受与重新计算。
- `POST /api/field/query`：object_id、field或request_id；可附content/format/metadata查询实际上传材料。先通过导入校验，返回实际记录及import_review，不自动接受。服务器维护查询计数；未知、过期、冲突、unsupported均保留。只有明确接受提议才更新第一层，候选采纳另行执行。
- `GET /api/view?scenario=...&time_s=...`：只读同版本空间快照，future标规则预测；不释放隐藏事件。当前world查看过去仅是按当前知识重建；当时知识必须用`/api/replay`的保存world。
- `POST /api/compare`：同一当前world/预算实际运行规则与学习，规则侧不执行模型推理，输出单场景耗时与真实候选；不采纳，不作为统计性能证明。
- `GET /api/export`：`chengkong-export-v3`，含事件、版本、计划、候选差异/条件、证据与字段审查、未解决信息和历史。原结果保持兼容`chengkong-result-v2`，新增checking_schedule明确精算已执行与待检查pair。初始检查预算不是全分析预算，规划/独立复核有自己的必须检查。

未知几何在展示中也不应被解释为空空间。数据库持久化已接受world及事件、当前计划与预约、历史决策；尚未接受导入、查询计数和外部服务调用预算当前为进程内状态，不支持多实例共享恢复。Web端未公开任意文件读取；`/api/template`只返回固定合成模板。文件上传仍受1MB请求上限。
