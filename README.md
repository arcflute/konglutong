# 城空智护 · 本地合成仿真原型 v0.2

施工信息导入 → 世界状态更新 → 三维时空约束 → 连续航段与资源冲突 → 多任务维护候选 → 独立复核 → 显式采纳。现有 Python 后端、SQLite、CLI 与本地 Web 使用同一计算链路。软件不接真实飞控，不自动采纳建议。

第二轮已实际训练小型 XGBoost 敏感性、消息传递 GNN、求解前候选排序、结束误差分位数与独立校准模型；全部只在合成数据上验证，规则仍是默认。真实多模态服务及真实施工历史演化模型未接入/未训练。完整状态见 [功能清单](docs/FEATURE_MATRIX.md)、[验收记录](docs/VALIDATION.md)和[学习实验](docs/LEARNING.md)。

## 安装与基本计算

当前服务器已有独立 `.venv`，无需修改系统 Python、驱动或全局 CUDA。纯规则后端只依赖标准库；以下命令在项目根运行：

```bash
cd /root/autodl-tmp/chengkong-zhihu
# 新环境才需要安装；脚本复用已有 .venv，不删除文件。
bash scripts/bootstrap.sh
.venv/bin/chengkong analyze --output runs/analysis-v2.json
.venv/bin/chengkong demo --output runs/demo-v2
.venv/bin/chengkong analyze --scenario end_unconfirmed --as-of 360 \
  --inflight --output runs/inflight-v2.json
```

`python -m chengkong`与`chengkong`入口等价。`demo`生成3个事件分支×4个时刻的12份 JSON 快照。默认快照分析保留历史冲突；`--inflight`显式启用软件仿真中的前缀冻结与余下航程修复。已执行航段不改写，当前点不可无代价移回起点。

## Web 与 SSH 访问

后端同时提供静态前端和 API，没有前端构建步骤。默认只监听 `127.0.0.1`；模型目录必须显式提供：

```bash
cd /root/autodl-tmp/chengkong-zhihu
.venv/bin/chengkong serve --host 127.0.0.1 --port 8765 \
  --db-dir runs/web-demo \
  --model-dir /root/autodl-tmp/chengkong-models/round2
```

等价后端入口：`.venv/bin/python -m chengkong.web`，支持相同服务参数。省略`--model-dir`仍可使用全部规则流程，界面如实显示本运行时未加载学习产物。加载模型需要下面的可选学习依赖。

在自己的电脑执行（将SSH端口和主机替换为实际值）：

```bash
ssh -N -L 8765:127.0.0.1:8765 -p <SSH端口> root@<服务器地址>
```

浏览器打开 `http://127.0.0.1:8765`。如果本地端口已占用，改为`-L 8766:127.0.0.1:8765`并访问8766。服务在前台运行，Ctrl+C停止；没有配置公网监听、云发布或后台自启动。SQLite保留所选分支的状态；重新演示用新的`--db-dir`，不要删除旧数据库。

## 可完整操作的演示

使用新的`runs/web-demo`数据库，按以下步骤操作。这套流程已通过浏览器实际调用后端验证。

1. 默认“结束未确认”、60秒：查看3项任务、塔吊和活动包络；“冲突证据”显示任务1、2在航段中间穿过活动范围，即使航段端点都在外也会检出。任务2的冲突区间为395.7–408.3秒，记录的计划结束360秒不能解除占用。
2. 切换“按期确认结束”，在“维护候选”预览路径。点击左侧“计划延期至600秒”，环境版本改变。点击旧候选的“尝试采纳过期候选”，观察拒绝原因；再显式采纳新候选，计划版本与预约版本原子更新。候选生成本身不改现行任务。
3. 切回尚未修改的“结束未确认”，推进至360秒。在“主动补证”对第一个请求查询已有记录，结果`unknown`。对另一个触发请求点击“提供明确合成完成记录”，它是使用者明确提供的模拟证据，经第一层校验后更新活动状态；设施仍保留。每请求预算1次，重复查询会预算耗尽，不可靠重试捏造证据。
4. 勾选“冻结已执行前缀”和“启用合成模型排序”，点击重新计算。在途候选只处理当前时刻以后的航程；“学习与材料”展示真实加载状态、排序建议与结束误差区间。没有候选时查看诊断，不能解释为全局不可行。
5. 在“学习与材料”解析“合成延期材料（模拟响应）”，先查看待接受提议，再显式接受；准备提议不改世界。真实服务未配置，模拟文字不能当作实际模型识别效果。
6. 在“事件回放”选择初始状态，查看当时60秒的未知信息；回放禁止修改操作。点击“返回当前状态”，当前360秒计划与信息仍在，历史决策记录没有被后来的证据覆盖。

另一组可重复的协同/资源/在途演示：

```bash
.venv/bin/python scripts/demo_cooperation.py --case cooperation --output runs/demo-cooperation
.venv/bin/python scripts/demo_cooperation.py --case inflight --output runs/demo-inflight
```

两个目录的`summary.json`分别汇总实际回溯、扩展、能量拒绝和前缀冻结结果，解释与实际检查见VALIDATION。

## 学习训练、评估和实验推理

现有模型版本 `synthetic-round2-001cfc579ce2`，目录分别为`/root/autodl-tmp/chengkong-models/round2/`与`/root/autodl-tmp/chengkong-data/round2/`。代码和小清单进入Git，权重和批量数据留在数据盘。

```bash
.venv/bin/python -m pip install -r requirements-learning.txt
.venv/bin/python -m chengkong.training benchmark --config configs/training-small.json
.venv/bin/python -m chengkong.training train --config configs/training-small.json
.venv/bin/python -m chengkong.training evaluate --config configs/training-small.json
.venv/bin/chengkong analyze --model-dir /root/autodl-tmp/chengkong-models/round2 \
  --learned --output runs/learned-v2.json
```

已有模型可直接`evaluate`或加载，无需重训。`train`会重建配置指定目录的实验产物；新实验请复制配置并修改输出目录以保留本次结果。144个事件按48个完整模板分组，训练/验证/校准/测试隔离。每模型本次1组参数，训练器最多允许3组；单作业90秒超时，标签求解也有明确预算。下游模型本轮不用上游预测特征，无折内预测泄漏链。

测试集上GNN排序不如包围盒规则，学习排序首选可行率与规则同为54.02%，首次可行实际代价略差。校准将单变量经验覆盖由62.96%变成100%，代价是平均区间扩大到346.82秒；只有9个独立校准模板，不能解释为现实飞行安全概率。所有学习建议不能绕过强制复核。

## 多模态与受控材料更新

仅从本项目`config/multimodal.local.json`读取显式服务配置；模板为`config/multimodal.example.json`。配置和专用环境变量`CHENGKONG_VLM_API_KEY`需要使用者自行提供，不自动扫描其他项目/用户目录，不自动加载`.env`。当前真实服务未配置。

适配器向配置端点POST规范化`records/context/response_schema`，期待`chengkong-extraction-v1`结构化响应，包含候选事件与可定位证据引用。文本引文必须存在于可见材料；图像米制几何须有尺度、坐标原点、轴及高度基准。模型自报置信度不当作校准结果。无结果、超时、引用不成立或缺尺度时保留未知。具体协议和API见[数据契约](docs/DATA_CONTRACTS.md)。

## 验证与工程位置

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests scripts
.venv/bin/python -m pip check
# 可选浏览器验收；此服务器的字体/本地库环境参数见VALIDATION。
.venv/bin/python scripts/browser_smoke.py
```

浏览器验收脚本启动自己的随机loopback端口和临时数据库，退出时关闭；可选依赖`requirements-dev.txt`。它记录请求、结果与截图，使用真实后端而非固定响应。

- 第一层：`contracts.py / world.py / evidence.py / multimodal.py`，有效/到达时间、事件重放、参数化占用、来源与受控写回。
- 第二层：`geometry.py / analysis.py / verification.py / incremental.py / reference_geometry.py`，连续冲突、全量复核、保守增量依赖及独立数值参考。
- 第三层：`planning.py / cooperation.py / resources.py / execution.py`，有限图SIPP、预算化重排回溯、动态修复集合、同机资源、在途仿真。
- 公共与学习：`pipeline.py / storage.py / cli.py / web.py / static/`；`synthetic.py / training.py / model_runtime.py / learning.py`。

关键输出是`chengkong-result-v2`：世界和约束版本、逐任务与联合资源分析、完整原计划复核、候选与条件、真实变更代价、预算/失败诊断、两类补证请求、模型状态及增量依赖。只有全部硬检查通过才标记`conditionally_feasible`；搜索失败、信息不足、预算耗尽和超时分别记录。

## 适用边界与依据

几何支持AABB、竖直圆柱与胶囊体，轨迹分段线性；运动占用由有效时间分段固定包络表示。航路仍为固定巡航高度、垂直起降和显式线性能耗，不是完整三维动力学。有限重排与SIPP无全局完备/最优保证。相同飞行器周转使用显式配置的地面运输、充电速率和周转时间；地面载运路径避障及载运车辆容量未建模，必须在条件中保留这一假设。周转驻留接受完整复核，但搜索器未提前纳入所有跨任务驻留，因此可能错过可行方案；等待区只复核显式轨迹，未自动生成地面搬运路线。空中等待需校验空间与能耗，地面等待需合法位置和容量。

主规划和主复核不复用搜索器，但共享几何底层函数；独立凸距离参考及手算边界用于额外交叉检查，不是形式化安全证明。真实施工历史预测、精细吊臂运动、真实多模态服务、真实标注/校准数据和现实部署评估仍待完成。

先读[AGENTS](AGENTS.md)、[项目说明](docs/PROJECT_BRIEF.md)、[技术路线](docs/TECHNICAL_ROUTE.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md)。`docs/source/`尚无标注最终版的3.2，不能声称已核对不存在的原稿。持续恢复位置见[PROGRESS](docs/PROGRESS.md)。
