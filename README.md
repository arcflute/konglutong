# 城空智护

首轮可运行的软件原型：把合成施工记录转成版本化世界状态与时空约束，连续检查既有任务，使用有限航路图上的安全时间区间搜索生成维护候选，再独立复核任务、几何、时间和资源。只用于软件与合成仿真，尚无训练好的AI模型或真实飞行验证。

## 安装与运行

工作目录是 `/root/autodl-tmp/chengkong-zhihu/`，已核验位于50G数据盘。现有独立环境已安装本项目，可以直接运行：

```bash
cd /root/autodl-tmp/chengkong-zhihu
.venv/bin/chengkong analyze --output runs/analysis.json
.venv/bin/chengkong demo --output runs/demo
.venv/bin/python -m unittest discover -s tests -v
```

`analyze` 默认使用 `examples/park_crane_synthetic.json`、`end_unconfirmed` 情景、决策时刻60秒。输出是可供后续前端读取的UTF-8 JSON。没有常驻服务，命令运行结束后退出；本轮不需要Node、GPU或API密钥。

新机器安装（Python 3.10+，先核验数据盘）：

```bash
cd /root/autodl-tmp/chengkong-zhihu
bash scripts/bootstrap.sh
```

脚本仅在`.venv`不存在时创建环境，不删除已有环境；构建依赖固定在`requirements-build.txt`，运行时无第三方依赖。网络不可用时，可在已有Python环境中直接执行：

```bash
PYTHONPATH=src python3 -m chengkong analyze --output runs/analysis.json
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

实际环境、安装失败原因和处理记录见 [ENVIRONMENT](docs/ENVIRONMENT.md)。`.env.example`是配置说明，不会自动加载；目前仅`CHENGKONG_RUN_DIR`进程环境变量用于设置demo默认输出目录。

## 演示与结果语义

```bash
.venv/bin/chengkong analyze --scenario on_time_confirmed --as-of 360 --output runs/on-time-360.json
.venv/bin/chengkong analyze --scenario delayed_confirmed --as-of 360 --output runs/delay-360.json
.venv/bin/chengkong analyze --scenario end_unconfirmed --as-of 600 --output runs/unknown-600.json
```

`demo`生成三种情景在60、330、360、600秒的12份快照，以及`runs/demo/summary.json`。仿真器按观测时刻释放事件，规划器只接收当前可见世界。60秒时三种情景都不知道未来结局，不能凭分支名称提前放行。360秒收到按期完成确认后，任务2的历史完整轨迹不再与该活动重叠；延期或未确认分支仍有冲突。所有分支都保留未拆除的塔吊物理占用。

默认60秒输入实际检出任务1、2的活动冲突，任务3与活动空间分离；生成绕行候选并独立复核。900秒仅为计算时域，不是活动结束时间；无法等待到一个已知结束时刻时，延后搜索会明确报告在当前图与预算内没有结果。有限占用的专门测试验证了延后与地面等待。330秒之后的示例任务有的已启动，历史分析可回放，程序不改写已执行航段，返回`needs_operational_review_started_task`。

主要输出字段：`world`、`constraints`、`baseline_analysis`、`baseline_joint_analysis`、`baseline_full_review`、`candidates`、`search_diagnostics`、`evidence_requests`、`evidence_results`、`learning_status`。`clear`仅指相应检查范围；候选只有全量复核通过才标记`conditionally_feasible`。条件、版本、变更量、未检查项、搜索失败原因均随结果输出。

## 工程结构与开发依据

- 第一层：`world.py` / `contracts.py`，输入校验、语义关系、事件和版本化占用；`evidence.py`为仅查询可见记录的补证入口。
- 第二层：`geometry.py` / `analysis.py` / `verification.py`，连续碰撞、保守筛选、联合资源与独立候选复核。
- 第三层：`planning.py`，有限航路图、SIPP能耗标签、等待与优先级私有预约；`learning.py`定义各层可替换学习接口。
- 公共能力：`pipeline.py` / `cli.py` / `storage.py`，结构化入口、演示回放、SQLite采纳事务。

`PlanStore`支持显式采纳API，事务内重做复核、校验版本、同时保存计划与预约和采纳记录。CLI分析/演示不会自动采纳，不会覆盖现行任务。成功采纳与过期拒绝在临时数据库测试中验证；本轮没有真实调度计划被采纳。

首次开发应读 [AGENTS.md](AGENTS.md)、[项目说明](docs/PROJECT_BRIEF.md)、[技术路线](docs/TECHNICAL_ROUTE.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md)、[功能清单](docs/FEATURE_MATRIX.md)、[数据契约](docs/DATA_CONTRACTS.md)和[首轮任务](docs/CODEX_FIRST_TASK.md)。`docs/source/`尚无明确最终版3.2，本轮依据工程整理版；原稿提供后需逐项核对。原始合成JSON保留，来源边界见 [示例说明](examples/README.md)。

## 当前限制与下一步

AABB、竖直圆柱、胶囊体及分段线性轨迹可检查；圆柱保护体采用明确的保守径向/轴向外扩。不支持几何或未知尺寸返回未检查。航路搜索仅支持单一巡航高度、垂直起降、固定速度和线性能耗；动态施工占用为时间区间内固定外包络。候选在有限图/预算内搜索，无全局最优或完备性保证。

多任务当前采用顺序预约与全量复核；同机多任务周转/补能尚无模型，返回未检查；有限回溯、修复集合扩展、选择性增量重算、精细吊臂运动和真实飞行动力学尚未实现。VLM/LLM未接入；XGBoost、GNN、学习排序、历史演化预测和分位数/共形校准均未训练。补证无可见确认时保持未知；决策价值估计与自动补证闭环待实现。下一阶段优先补齐协同修复与增量维护，再建立按完整事件隔离的仿真训练/校准数据，接入真实模型与前端。

具体测试证据和本轮文件清单见 [VALIDATION](docs/VALIDATION.md)。
