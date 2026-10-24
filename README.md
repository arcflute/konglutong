# 空路通 · 施工变化驱动的低空航路维护原型

当前交付为 **桌面 v1.1 三端与统一门户，共16页**：政府深蓝研判、企业浅色运营、个人暖白靛紫公开服务。主视口1920×1080，适配1600×900、1366×768；个人端也是完整宽屏网页。数据来自同一Python计算后端和SQLite，全部飞行仅软件仿真。

施工材料 → 来源与字段核对 → 世界事件 → 三维时变占用 → 冲突与补证 → 多任务维护 → 独立复核 → 显式采纳。原“城空智护”三层算法、CLI及历史实验保留，代码包名继续为`chengkong`。本轮只完善界面及必要的权限/投影/持久化服务，没有训练新模型。

入口文档：[16页验收与截图](docs/FRONTEND_ACCEPTANCE.md)、[三端演示](docs/FRONTEND_DEMO.md)、[API对照](docs/FRONTEND_API_MAP.md)、[实施与部署](docs/FRONTEND_IMPLEMENTATION.md)、[设计规格v1.1](docs/FRONTEND_DESIGN_SPEC.md)。当前能力见[FEATURE_MATRIX](docs/FEATURE_MATRIX.md)、[VALIDATION](docs/VALIDATION.md)、[PROGRESS](docs/PROGRESS.md)。第四轮算法和合成研究结果保留在[ROUND4_RESULTS](docs/ROUND4_RESULTS.md)、[DYNAMIC_PLANNING](docs/DYNAMIC_PLANNING.md)，不因新界面变成现场效果证明。

## 构建与启动

在本服务器项目根执行。已有项目独立`.venv`，复用现有环境；新环境安装沿用`bash scripts/bootstrap.sh`。规则流程不需要CUDA或学习依赖。

```bash
cd /root/autodl-tmp/chengkong-zhihu
# 本服务器独立Node；其他机器使用自己的兼容Node 22.18+
export PATH=/root/autodl-tmp/chengkong-data/frontend-toolchain/node-v22.18.0-linux-x64/bin:$PATH
npm --prefix frontend ci --no-fund --no-audit
npm --prefix frontend run build

# 首次演示用新目录；正常重启仍使用同一输入、模型和目录
.venv/bin/chengkong serve --input examples/frontend-v1/motion.json \
  --db-dir runs/frontend-v1/live-motion-001 --host 127.0.0.1 --port 8765 \
  --demo-identities --action-policy keep_detour \
  --model-dir /root/autodl-tmp/chengkong-models/round3
```

Python同源提供生产构建与API，不必另启前端开发服务器；构建后运行无需Node进程。`--demo-identities`显式开放本地演示身份，在门户选择三端；不加该参数则真实身份提供方未配置，私有接口拒绝访问。身份、组织与操作权限由后端校验，个人只收到公开投影和本人记录。

模型目录为本机已有合成模型。省略`--model-dir`可从新目录运行规则流程，界面如实标记未加载；已绑定库重启不能更换/省略原模型。真实多模态服务未配置时仍可完成结构化导入及全部独立业务。

自己电脑执行并访问 `http://127.0.0.1:8765/`：

```bash
ssh -N -L 8765:127.0.0.1:8765 -p <SSH端口> <用户>@<服务器地址>
```

只监听loopback，Ctrl+C停止，不公开部署。三维使用本地Cesium资源和虚构ENU米制园区，地面z=0；无在线地图令牌，WebGL失败可回退二维。部署按完整仓库检出及构建，不宣称Python wheel已自带前端资源。

## 三端快速演示

1. **企业**：变化与证据→载入/上传结构化材料→解析核对→明确接受→影响分析→航路维护比较→明确采纳；旧候选应显示失效并被后端拒绝。计划和预约版本同步更新。
2. **政府**：查看同一场景的授权态势、台账与影响，填写核查备注；历史追溯只读，专题报告固定版本。核查不结束施工，也不代企业采纳计划。
3. **个人**：关注公开园区→企业更新获准公开的活动→查看去重提醒、标已读→保存查询和反馈。刷新/正常重启保留本人记录；其他个人身份不可读取。公开摘要撤回后旧提醒不再返回详情。

完整可重复的A延期维护、B完成补证但设施仍在、C个人提醒与恢复步骤见[FRONTEND_DEMO](docs/FRONTEND_DEMO.md)。B使用独立`park.json`新库，避免已绕行路径影响预期；运动展示使用`motion.json`。演示副本不是新增独立研究样本。

备份恢复见[RECOVERY](docs/RECOVERY.md)：新库包含`_portal.sqlite3`个人和核查数据；同一代码/输入/模型正常停启恢复。代码或权限配置变化使用新目录，不覆盖旧round2/3/4数据库和产物。

## 可复制的CLI与导入

```bash
.venv/bin/chengkong analyze --input examples/motion_crane_synthetic.json \
  --scenario end_unconfirmed --as-of 60 --output runs/frontend-v1/cli-motion-reproduction.json
.venv/bin/chengkong analyze --scenario end_unconfirmed --as-of 120 --inflight \
  --model-dir /root/autodl-tmp/chengkong-models/round3 --learned \
  --output runs/frontend-v1/inflight-reproduction.json
.venv/bin/chengkong demo --output runs/frontend-v1/event-demo-reproduction
.venv/bin/python scripts/demo_cooperation.py --case cooperation --output runs/frontend-v1/cooperation-reproduction
.venv/bin/python scripts/demo_cooperation.py --case inflight --output runs/frontend-v1/inflight-reproduction

.venv/bin/python -m chengkong.imports examples/imports/construction_delay.json \
  --accept --output runs/round3/import-json-reproduction.json
.venv/bin/python -m chengkong.imports examples/imports/construction_delay.csv \
  --format csv --metadata examples/imports/metadata.json \
  --accept --output runs/round3/import-csv-reproduction.json
.venv/bin/python -m chengkong.imports examples/imports/surveyed_pad.geojson \
  --format geojson --metadata examples/imports/geojson_metadata.json \
  --accept --output runs/round3/import-geojson-reproduction.json
```

CLI导入使用独立临时world并输出JSON；Web接受操作才更新其SQLite会话。模板另含`examples/imports/park_tasks_resources.json`（建筑、起降点、任务、必达服务与等待资源）。已有任务不能通过导入替换。[导入说明](docs/IMPORTS.md)列出严格元数据、六字段白名单、冲突裁决及不支持状态。GeoJSON只支持明确测量点对应的Point，不猜测投影或高度基准。[来源说明](docs/DATA_SOURCES.md)区分合成材料和公开负例材料。

## 第四轮运行对照

```bash
.venv/bin/chengkong analyze --input examples/round4/dynamic_window.json \
  --planning-mode dynamic_windows --output runs/round4/dynamic-reproduction.json
.venv/bin/chengkong analyze --input examples/round4/dynamic_window.json \
  --planning-mode conservative --action-policy keep_detour --output runs/round4/conservative-reproduction.json
```

`--planning-mode`也可用于serve，仅初始化新会话；已恢复会话的策略需在界面显式切换。输入缺误差界时退保守包络，无法支持的几何不会静默跳过。最终序列协议、冻结与复评命令见[LEARNING_ROUND4](docs/LEARNING_ROUND4.md)。本轮根据失败诊断保留原模型，不重新训练；敏感性、GNN和修复排序仍只调整检查/求解顺序。旧校准覆盖率不适用于新的动态分布。

## 第四轮固定序列评估

[本轮结果](docs/ROUND4_RESULTS.md)和[学习说明](docs/LEARNING_ROUND4.md)保留全部失败与协议纠正。新测试为3个人工父拓扑、每父2条完整序列、每序列4次决定。保守规则、动态规则、强规则、学习的决定成功分别16/24、18/24、17/24、17/24；整序列成功分别3/6、4/6、4/6、4/6。学习未显示可靠收益，动态反复修改更多，全部结论仅限合成仿真。本轮没有重新训练模型或获得新分布校准覆盖。

使用固定源码快照复现，生成新的结果文件，不覆盖第一次观察：

```bash
cd /root/autodl-tmp/chengkong-data/round4/evaluation-source-topology-v3
PYTHONPATH=$PWD/src /root/autodl-tmp/chengkong-zhihu/.venv/bin/python \
  -m chengkong.evaluation_round4 evaluate --config configs/evaluation-round4.json
```

主结果在`/root/autodl-tmp/chengkong-data/round4/topology_evaluation/final-reproduction-1792694050515993778.json`，SHA256、全部策略分母和源码/输入路径见`configs/round4-evaluation-artifacts.json`。当前工作区后续改动会触发固定代码检查；不要以测试集继续调参。

仅重新计算可核查摘要或旧失败诊断：

```bash
cd /root/autodl-tmp/chengkong-zhihu
.venv/bin/python scripts/summarize_round4.py \
  /root/autodl-tmp/chengkong-data/round4/topology_evaluation/final-reproduction-1792694050515993778.json \
  --output runs/round4/summary-reproduction.json
.venv/bin/python -m chengkong.diagnostics_round4 --output runs/round4/failure-reproduction.json
.venv/bin/python scripts/reference_round4.py
```

## 历史小模型训练与评估（round3）

模型`synthetic-round3-a774ca1b9f04`位于`/root/autodl-tmp/chengkong-models/round3`；数据位于`/root/autodl-tmp/chengkong-data/round3`。160个独立布局/事件模板，各2个变体/快照；按模板隔离为80训练、24验证、32校准、24测试。最终测试48事件、752任务约束对、192完整候选组。它们不是320个独立模板或真实施工事件。

```bash
# 复评已保存模型，不覆盖第一次最终测试；生成新的reproduction结果。
.venv/bin/python -m chengkong.training_round3 evaluate --config configs/training-round3.json
# 事后更强规则与去边诊断，不用于重新选型：
.venv/bin/python -m chengkong.diagnostics_round3 --config configs/training-round3.json
```

新训练应使用独立目录。以下保留现有产物并可复制运行；同一配置不能无限重复训练或重新利用已观察的test调参。

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
c=json.loads(Path('configs/training-round3.json').read_text())
c['data_dir']='/root/autodl-tmp/chengkong-data/round3-reproduction'
c['model_dir']='/root/autodl-tmp/chengkong-models/round3-reproduction'
Path('runs/round3-reproduction-config.json').write_text(json.dumps(c,indent=2))
PY
.venv/bin/python -m chengkong.training_round3 benchmark --config runs/round3-reproduction-config.json
.venv/bin/python -m chengkong.training_round3 generate --config runs/round3-reproduction-config.json
.venv/bin/python -m chengkong.training_round3 train --config runs/round3-reproduction-config.json
.venv/bin/python -m chengkong.training_round3 evaluate --config runs/round3-reproduction-config.json
```

敏感性MSE降低且半数检查预算内覆盖更多关键pair，但更慢、完整检查相关任务的比例更低。GNN有小幅排序改善，联合修复未增加完成任务。排序相对原固定规则有提升，但事后简单`keep→detour`规则达到同样92/192成功，不能宣称可靠学习优势。32个独立校准组的区间覆盖提高但平均宽度185.76秒；不是整条任务安全概率。详见[完整指标、分母和失败分析](docs/LEARNING_ROUND3.md)。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests scripts
.venv/bin/python -m pip check
# 本服务器已经准备本地Chromium及字体库：
PLAYWRIGHT_BROWSERS_PATH=/root/autodl-tmp/chengkong-data/browser \
LD_LIBRARY_PATH=/root/autodl-tmp/chengkong-data/browser-libs/extracted/usr/lib/x86_64-linux-gnu \
FONTCONFIG_FILE=/root/autodl-tmp/chengkong-data/browser-libs/fonts.conf \
.venv/bin/python scripts/browser_frontend.py --output runs/frontend-v1/browser-reproduction-001
```

浏览器脚本使用真实后端、随机本地端口、临时SQLite，结束时关闭；需要`requirements-dev.txt`。当前脚本验证16页、三端9个主要桌面视口、三套实际闭环及恢复。迟到响应、公开撤回和受控解析提议的专项命令见FRONTEND_ACCEPTANCE。旧browser_round3/round4脚本仅用于对应历史UI版本。验证日志和截图见[VALIDATION](docs/VALIDATION.md)。

## 工程边界

- 第一层：`world/contracts/motion/imports/field_evidence/multimodal`，记录来源、未知、事件和时变占用。真实服务只读取项目`config/multimodal.local.json`及专用环境变量，参见`config/multimodal.example.json`，未配置则明确未接入；无尺度图像不产生可靠米制参数。
- 第二层：`analysis/geometry/verification/reference_geometry/incremental`，连续航段、同时间动态几何、预算化检查顺序及独立数值参考。动态误差膨胀和容差见[MOTION](docs/MOTION.md)。仍可能保守误报，未知/超限不能放行。
- 第三层：`planning/cooperation/resources/execution/storage`，有限SIPP与重排回溯、修复集合扩展、共享资源、同机衔接、冻结前缀、事务采纳。SIPP可切换完整范围与时变通行窗口，等待/航段带保守误差界；不声称全局最优或完备。

只支持AABB、竖直圆柱、胶囊及分段线性航迹。吊物摆动、小车变幅、臂架俯仰、精细网格、真实遥测/飞控、地面运输路径和车辆容量未实现。周转、能耗采用显式简化模型。真实施工演化训练、真实现场泛化、任意字段补证/坐标转换、服务多实例恢复仍待后续。全部研究路线保留，不把合成实验当作现实部署效果。

技术依据仍为[工程整理路线](docs/TECHNICAL_ROUTE.md)；`docs/source/`无最终3.2原稿。恢复开发前读[AGENTS](AGENTS.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md)、[PROGRESS](docs/PROGRESS.md)及当前Git状态。下一步需要有许可、时空对齐和测量尺度的现场材料、任务/资源参数、真实历史序列及明确项目服务配置。
