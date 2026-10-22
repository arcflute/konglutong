# 第三轮：参数化吊装运动与连续时变占用

当前实现是有来源的确定性吊装规则仿真，不是设备遥测、动力学模型或真实安全认证。`examples/motion_crane_synthetic.json`保留原来三个事件分支，增加塔身、水平回转吊臂、固定幅度吊点、竖直吊索和球形保守吊物。场景明确合成；原静态场景继续兼容。

## 输入及来源

`facility.motion_model`使用`schema_version=crane-motion-v1`、`kind=tower_crane`。完整可运行字段见上述JSON；以下字段都有明确单位，坐标和高度服从world的局部米制基准。

| 字段 | 含义与边界 |
|---|---|
| `activity_id/source_ids` | 唯一关联作业及参数来源；来源必须在当前已知材料中，缺失来源拒绝。错误关联保留unknown |
| `base_m/tower_height_m/tower_radius_m` | 塔身底部三维坐标、高度及竖直圆柱半径 |
| `boom_length_m/boom_radius_m` | 水平吊臂的长度与胶囊体半径，转轴为塔顶 |
| `trolley_radius_m/rope_radius_m/load_radius_m` | 固定幅度吊点距离、索胶囊半径、吊物保守球体半径；没有实现小车变幅或吊物摆动 |
| `angle_range_deg/load_z_range_m` | 连续展开的角度范围（宽度不超过360度）；吊物中心绝对z范围（在塔底与塔顶之间） |
| `max_slew_deg_per_s/max_hoist_mps` | 显式角速度、升降速度上限，不来自模型置信度 |
| `initial_pose/parking_pose` | `{angle_deg,load_z_m}`；停车姿态可null，未知则保持全范围保守占用 |
| `program` | 连续分段线性规则：每段`interval_s/start_pose/end_pose`，同一时刻姿态不能跳变，速度不能越限；段间保持上段姿态；空程序unknown |
| `outside_program_policy` | 默认`conservative_range`：程序已结束但未来姿态未确认，继续占用完整声明范围。只有明确选择`hold_last_pose`才将末姿态保持作为规则假设。计划作业结束不截断物理或活动占用 |
| `activity_policy` | 默认`recorded_envelope`保留既有活动包络；显式选择`moving_components_with_clearance`时，以四个运动部件及`activity_clearance_m`构成作业保护范围 |
| `max_time_step_s/max_cell_inflation_m` | 保守细分参数，默认1秒、0.5米；允许上限5秒、5米。实际时间片还受速度界约束 |

运动模型启用时，设施原有`physical_geometry`成为兼容参考，碰撞使用运动模型生成的四个部件。未知/不支持的运动模型产生未知几何、未完成检查状态，不回退成旧静态几何放行。支持的运动参数是有来源的记录断言；程序输出被标为规则状态或预测，不代表现实测量。

## 事件、暂停和结束

新增`facility_motion_changed`事件携带`facility_id`和完整`motion_model`（或显式null未知）。它沿用有效时间、到达时间、幂等、迟到重建、版本和历史机制。参数版本在有效时刻分段，不用后来模型覆盖此前几何。

现有`activity_paused`在有效时刻冻结程序相位和当前姿态；`activity_resumed`平移后续程序时间，保留暂停时长。未知姿态的暂停仍是全范围占用。暂停不等于确认完成。

`activity_completion_confirmed`结束活动保护约束，但塔身、臂、索和吊物仍在。若完成时姿态和停车目标均已知，物理部件从该姿态按声明速度上限连续移向停车目标；不是瞬移。未知停车目标、或完成时程序姿态已未知，继续保守全范围占用。停车目标在完成后被材料修订时，使用更新有效时刻的当前姿态重新开始过渡，不能借用旧完成时间瞬时跳到新目标。新范围排除当前已知姿态时拒绝更新，需明确几何纠错/重新导入。

只有独立`facility_removal_confirmed`事件才结束设施物理占用。未到达或尚未生效的未来完成记录不提前释放当前约束。静态几何事件不能覆盖已启用的运动设备；必须显式更新完整运动模型。

## 实际动态碰撞方法

每条约束保存完整范围`geometry`，以及`motion={model,component,commands,parking_transition,motion_status,clearance_m,...}`。粗筛使用完整范围。细查使用飞行器与设备在同一时刻的三维位置，绝非只改变前端动画。

设时间片长为`Δt`，中点时刻为`tm`。各物理部件相对中点几何的Hausdorff位移不超过`V Δt/2`：

- 塔身：`V=0`。
- 吊臂：`V=臂长×最大角速度（弧度/秒）`。
- 吊索及吊物：`V=吊点幅度×最大角速度＋最大升降速度`。

检查器在时间片中点生成真实部件几何，并增加`V Δt/2`膨胀，再与整段连续飞行航段求交。任何同时间真实碰撞都包含在这个保守包络检查中，因此两端采样没有碰撞也不能漏掉中间横扫。时间片切在程序边界、暂停/恢复、完成与模型版本处。未知姿态直接使用声明的全范围包络，不将未知时刻采样成一个有利姿态。业务活动余量和飞行器保护半径各只加入一次；运动逼近误差另行明示。

结果的`dynamic_check`记录速度界、实际最大膨胀、时间片数、未知姿态包络片数和方法。冲突时段是`conservative_possible_contact`，可能误报，不能当作精确接触起止或真实事故。基础几何容差`1e-9`，单航段最多20,000个时间片；超限返回`dynamic_check_budget_exhausted`的未检查状态。

完整候选复核重新生成当前世界运动约束。附加独立参考采用耦合时间的距离Lipschitz分支界（飞行速度＋部件速度），以距离下界证明分离；未分离且小于0.01秒的分支保守判占用，最多50,000节点，超限未检查。它共享参数化姿态规则与输入速度界，但不调用主检查的几何求根或SIPP判断；这是数值交叉检查，不是独立物理观测或形式化证明。

SIPP当前使用完整运动范围作保守图搜索，并在诊断中明确记录；候选进入第二层后才做时变详细复核。因此可能错过仅在回转间隙可行的航路，不声称已完成运动感知最优SIPP或全局完备搜索。所有硬检查和显式采纳仍保留。

## 渲染与复现

`motion_snapshot(world,at_s=None)`返回同一环境/规则版本的设施组件几何、姿态、未知状态和来源。未来预览明确标`parameterized_rule_prediction`，不会释放隐藏未来事件，也不会修改world。历史“当时知道什么”须传入保存的历史world；对当前world指定过去时间只是按当前知识重算该有效时间，二者不可混淆。

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_motion.py' -v
.venv/bin/chengkong analyze --input examples/motion_crane_synthetic.json \
  --scenario end_unconfirmed --as-of 60 --output runs/motion-analysis.json
.venv/bin/chengkong serve --input examples/motion_crane_synthetic.json \
  --host 127.0.0.1 --port 8765 --db-dir runs/motion-web
```

手算用例包括：90度两端不相交而中间横扫；相同外包络内错误时刻/错误半平面确实无碰撞；0.5米/秒升降经过指定高度；1秒高速回转的采样间穿越；暂停保持/恢复；完成后的连续停车与未拆除塔身；未知停车保守占用；参数未知/不支持拒绝放行；速度越限及不连续程序拒绝；迟到模型分段及旧快照不变；增量/全量一致；候选不能漏传运动约束；完成后停车目标修订不能瞬移。

不支持：设备弹性、吊物摆动、索弧垂、臂架俯仰、小车变幅、多机构耦合、精细网格、真实闭环遥测与飞控。没有真实设备来源时保持合成标识；动力学和测量可信度需后续资料与验证。
