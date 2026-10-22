# 结构化材料、字段证据与人工接受（round3）

`imports.StructuredImporter`提供真正的解析→校验→提议→显式接受→第一层事件/状态→重新计算流程。它不是多模态识别效果的替代声明。新增记录不自动替换现行任务或自动采纳维护候选。

## 输入契约

JSON顶层为`chengkong-material-v1`，包括`data_kind`（synthetic/record_assertion/mixed_simulation）、`is_real_case`、`coordinate_system`、`time_system`、`sources`和`records`。坐标单位、轴、原点、高度基准和模拟时间原点须逐项匹配当前世界；不尝试猜测英尺、经纬度、海拔、离地高度或时间原点。来源每项含稳定`id/kind/description/license`，同ID不同来源说明拒绝。

每条记录必需`id/record_type/source_id/effective_at_s/observed_at_s/payload`，可带`locator/valid_until_s`。未来才取得的记录不进入当前决策；已取得但未来生效的施工事件进入原有待生效队列。过期、缺失、无法解析、unsupported和冲突分别显示；不以最后上传顺序代替来源判断。

| record_type | 已支持的输入和限制 |
|---|---|
| facility | 新设施完整`id/physical_geometry`，可带完整motion_model；走facility_added事件 |
| building | 新静态建筑`id/geometry`；仅当前时刻追加，约束从import_valid_from_s开始 |
| pad | 新起降点`id/position_m/capacity/ground_wait_allowed`，仅追加 |
| task | 完整既有任务契约，含轨迹、服务点/服务时长/时间窗、速度、能量和资源ID；仅新增且出发不早于当前模拟时刻 |
| resources | 新waiting_areas及尚未配置的aircraft_turnaround_model；校验容量、位置、转场速度、能耗、周转与充电速率 |
| event | 当前支持的完整施工事件；材料行有效/取得时间须与事件一致 |
| field_evidence | object_id/field/value，按下表转换为第一层事件 |

建筑、资源和任务追加须`effective_at_s=world.as_of_s`。既有实体/资源不可通过此入口覆盖；既有任务变更继续用维护候选＋显式采纳事务。服务端存储层应使用受控导入事务提交新增任务，并核对`audit.task_additions`，不能将普通环境更新变成任意计划替换。来源同步保存在重建起点中，后续施工事件重放不会丢失导入来源；任务不属于施工重建字段。

CSV使用独立JSON元数据和以下列：`id,record_type,source_id,effective_at_s,observed_at_s,valid_until_s,payload_json`。复合几何/服务/轨迹放在有引号的JSON列，避免自行发明不完整平面表字段。

GeoJSON仅接受FeatureCollection的Point，目标为pad或facility。RFC7946经纬度不能直接充当园区米制坐标。导入要求`metadata.geographic_point_mapping={input_reference:"EPSG:4326",input_height_unit:"m",input_height_datum:"WGS84_ellipsoid",source_id,points:[{geographic:[lon,lat,z],local_m:[x,y,z]}]}`，每个点必须存在唯一、显式来源的测量对应；不插值、不推断投影，也不把海拔/深度自动转成本地z。设施的`geometry_relative_to_point`是声明的局部组件偏移；Point映射后平移这些组件。任意Polygon、任意CRS转换及未对应的点返回unsupported或invalid_input。示例中的地理点和对应关系全部为合成数据。

## 字段支持范围

| field | value与实际变更 | 限制 |
|---|---|---|
| geometry.position | `{from_anchor_m:[...],to_anchor_m:[...]}`，按明确锚点差平移静态全部几何 | 不猜中心点；动态设施须完整motion_model更新 |
| facility.height | `{component_index,top_z_m}`，更新指定盒体/竖直圆柱顶部高度 | 是统一高度基准的顶部z，不是无基准“高度”；胶囊/动态组件不支持简写 |
| activity.range | 已支持的完整几何 | 仍受事件有效时间和完成状态约束 |
| planned_interval | `[原开始时刻,新计划结束]` | 不改已记录开始事实，计划结束不确认完成 |
| completion | `{status:"confirmed",confirmed_at_s}` | 确认时刻必须等于有效时间，不得早于作业开始；设施保留 |
| parking_pose | 静态：`{physical_geometry:[...],pose:{...}}`；动态：`{angle_deg,load_z_m}` | 静态须明确几何；动态复制完整motion_model并通过其校验，按规则连续向停放目标运动，不表示已实测到位 |

除此之外明确返回unsupported。缺失值保持未知。`field_review`逐条给出提取值、来源位置、原值、冲突状态和adopted=false。接受后`import_audit`保存采纳和拒绝记录ID、人工选择理由、前后版本、受影响对象及约束、需重新计算标记；`field_evidence/imported_records`保留原始材料。

同一字段的有效未过期来源若不同（包括原生planned_end/completion/envelope/geometry/motion事件的对应断言），prepare返回`conflicting_evidence`且仍不更新。accept必须提供`resolutions={"object_id::field":{"record_id":"所选ID","reason":"人工核对理由"}}`；被拒绝的断言仍保留，不能无痕删除。已接受来源被明确替代时标为superseded_by_explicit_resolution。单次批处理全部在副本校验，任一记录错误均不留下部分结果。

`make_field_request`冻结环境/场景/规则/模型，`query_field_evidence`带预算、期限及超时，只读取可见记录。没有证据返回unknown，过期返回expired，冲突返回conflicting_evidence，找到实际记录仅返回evidence_available；再交给StructuredImporter审阅接受。源仅为available_records或明确synthetic_evidence_source，模拟延迟只能出现在后者，不能伪装真实网络调用。

## 可复制命令与Python接口

```bash
cd /root/autodl-tmp/chengkong-zhihu
.venv/bin/python -m chengkong.imports examples/imports/construction_delay.json \
  --accept --output runs/round3/import-delay.json
.venv/bin/python -m chengkong.imports examples/imports/construction_delay.csv \
  --format csv --metadata examples/imports/metadata.json \
  --accept --output runs/round3/import-csv.json
.venv/bin/python -m chengkong.imports examples/imports/surveyed_pad.geojson \
  --format geojson --metadata examples/imports/geojson_metadata.json \
  --accept --output runs/round3/import-geojson.json
.venv/bin/python -m chengkong.imports examples/imports/park_tasks_resources.json \
  --accept --output runs/round3/import-tasks.json
```

CLI默认只准备；`--accept`明确授权对一次性仿真世界接受并真实调用analyze_world，输出审查、接受审计及实际分析，不写运行Web数据库。

```python
service = StructuredImporter()
review = service.prepare(world, content, format='json', metadata=None)
audit, updated_world = service.accept(world, review['proposal_id'], explicit=True, resolutions=None)
# audit['world_updated']才可进入受控持久化和analyze_world(updated_world)
```

环境、各任务版本或预约版本改变后，旧提议返回needs_recheck。准备服务实例应由同一Web会话复用。多模态MaterialAgent提议也冻结任务/预约/规则/模型版本，不能在计划已采纳变化后直接接受旧提议。材料接受和维护候选采纳是两个明确动作。

## 多模态路径及未接入项

现有HTTP协议继续为`chengkong-extraction-v1`，请求增加可选model_id，项目配置`max_calls`默认3次、上限100次，计数属于适配器实例且包含失败请求。Web必须在会话内复用适配器；没有项目内`config/multimodal.local.json`时真实服务为not_configured。本轮检查该目录只有example配置，未读取其他项目或目录的凭据。

MaterialAgent能将`crane_1.facility.height`等明确对象字段的未知项映射到统一字段请求；不支持字段显示unsupported_field。动态图像中的motion_model/parameters/parking_pose也必须通过尺度、坐标与来源校验，模型自报置信度不作为物理测量可靠性。真实VLM调用、真实历史施工演化学习仍未接入/未训练。

材料待接受提议和查询预算仍属于单进程会话；已接受材料和审计嵌在world，经SQLite持久保存。任意坐标系转换、任意字段、既有任务通过导入任意编辑以及真实测量正确性认证均不在已实现范围。
