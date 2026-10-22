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
