# 城空智护 · 动态航路维护与运行验证原型

施工材料 → 来源与字段核对 → 世界事件 → 三维时变占用 → 冲突与补证 → 多任务维护 → 独立复核 → 显式采纳。Python、SQLite、CLI和本地Web使用同一计算后端。全部飞行仅软件仿真。

第四轮把带运动误差界的时变通行窗口接入SIPP候选搜索，增加失败诊断、维护解释、完整联合预约持久化和单实例恢复。三层使用同一版本化输入；持续禁入不会因几何间隙解除，候选仍需独立复核与显式采纳。旧合成模型只作范围外排序对照，规则继续默认。真实多模态服务、真实施工演化学习和现场效果尚未验证。

当前记录见[第四轮结果](docs/ROUND4_RESULTS.md)、[动态算法及边界](docs/DYNAMIC_PLANNING.md)、[固定演示](docs/ROUND4_DEMOS.md)、[恢复与备份](docs/RECOVERY.md)、[功能清单](docs/FEATURE_MATRIX.md)、[验证](docs/VALIDATION.md)、[恢复位置](docs/PROGRESS.md)。历史round2/round3数据、模型和原始结果保留；本轮产物使用独立round4目录。

## 安装与启动

在项目根执行；当前服务器已有独立`.venv`及CPU学习依赖，本轮复用了它们。纯规则计算只依赖Python标准库，不需CUDA、在线地图令牌或大型模型。

```bash
cd /root/autodl-tmp/chengkong-zhihu
# 仅新环境需要；复用已存在的项目环境：
bash scripts/bootstrap.sh
# 仅需要学习功能但尚无依赖时：
.venv/bin/python -m pip install -r requirements-learning.txt

# 前后端同进程；使用新db-dir重演，保留旧库。
.venv/bin/chengkong serve --host 127.0.0.1 --port 8765 \
  --db-dir runs/web-round4-demo \
  --model-dir /root/autodl-tmp/chengkong-models/round3
```

没有前端构建步骤；等价入口是`.venv/bin/python -m chengkong.web`。省略`--model-dir`可运行规则流程，学习状态明确为未加载。模型权重在数据盘，不能只克隆代码就声称已加载。

自己电脑上执行以下命令，并在浏览器打开`http://127.0.0.1:8765`：

```bash
ssh -N -L 8765:127.0.0.1:8765 -p <SSH端口> root@<服务器地址>
```

服务仅监听loopback，Ctrl+C停止；没有公网发布、自启动或真实飞控。若本机端口占用，改用`-L 8766:127.0.0.1:8765`并访问8766。

动态通行窗口使用独立输入和数据库（默认T=0；原round3运动场景仍兼容）：

```bash
.venv/bin/chengkong serve --input examples/round4/dynamic_window.json \
  --host 127.0.0.1 --port 8766 --db-dir runs/web-round4-motion \
  --model-dir /root/autodl-tmp/chengkong-models/round3
```

相应转发8766端口。在空间视图选择“吊装细节”，调整视角和查看时刻，可核对塔身、臂、索、吊物及活动保护。预览未来只使用已知运动规则，不推进模拟时间或释放完成证据；“事件回放”才显示当时保存的知识状态。

## 演示与材料流程

本轮A动态等待/绕行/保守比较、B补证到达与旧候选失效、C多任务在途变化的完整步骤和实际输出见[ROUND4_DEMOS](docs/ROUND4_DEMOS.md)。左侧可切换占用规划和动作顺序；策略变更会使旧候选失效。以下保留材料流程说明。

从新的静态演示库开始，三套流程使用不同分支，避免相互覆盖。浏览器自动验收脚本也实际执行这些流程。

1. **材料延期与维护**：切换“按期确认结束”，先记下/预览原候选。打开“导入与字段核对”，载入合成延期模板，对`lifting_1 / 计划时段`点击“查询上方材料并提出字段更新”。核对来源、提取值和版本后，显式接受，计划时段更新为`[120,600]`，尚未确认结束。回到候选，尝试采纳旧候选应被拒绝；预览新候选，再显式采纳，计划与预约原子更新。
2. **补证改变判断但保留设施**：切换“结束未确认”，推进至360秒；对`lifting_1 / 完成状态`查询已有证据，结果未知。导入模板JSON并将记录改成新ID、有效/获取时间均360，payload改成下面的完成记录。点击解析、核对、接受。任务2对象检查由冲突变为无冲突，塔吊物理设施仍在。完整任务是否可执行还需联合资源和候选复核。点击“导出当前结果”保存证据、版本、候选条件和未解决信息。
3. **协同与在途变化**：切换“延期后确认结束”，推进至120秒，勾选“冻结已执行前缀”，可再勾选学习实验，点击重新计算。任务1从当前模拟位置维护，已执行前缀不变。候选中比较任务变更、延误、增距、能耗；“学习与材料”可运行同一版本的规则/学习实际对照。历史回放保持只读，返回实时状态后当前计划不变。

第二套材料的payload：

```json
{"object_id":"lifting_1","field":"completion","value":{"status":"confirmed","confirmed_at_s":360}}
```

新证据不会自动覆盖计划。预算耗尽、未知、冲突来源、过期候选或无候选都有明确状态；不要反复重试制造有利证据。补证过程、尚未接受提议、调用预算、当前场景、计划与全部联合预约已持久化。相同输入/代码/模型和db-dir正常重启可恢复；不匹配则拒绝。旧库不自动迁移，备份与独立目录恢复见RECOVERY；没有多实例协议。

## 可复制的CLI与导入

```bash
.venv/bin/chengkong analyze --input examples/motion_crane_synthetic.json \
  --scenario end_unconfirmed --as-of 60 --output runs/round3/motion.json
.venv/bin/chengkong analyze --scenario end_unconfirmed --as-of 120 --inflight \
  --model-dir /root/autodl-tmp/chengkong-models/round3 --learned \
  --output runs/round3/inflight-learned.json
.venv/bin/chengkong demo --output runs/round3/event-demo
.venv/bin/python scripts/demo_cooperation.py --case cooperation --output runs/round3/cooperation
.venv/bin/python scripts/demo_cooperation.py --case inflight --output runs/round3/inflight

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
.venv/bin/python scripts/browser_round4.py --output runs/round4/browser-reproduction
```

浏览器脚本使用真实后端、随机本地端口、临时SQLite，结束时关闭；需要`requirements-dev.txt`。它验证三套流程、22次API请求、空间/冲突视图、导出、只读回放及移动布局，不用固定响应代替计算。验证日志和截图见[VALIDATION](docs/VALIDATION.md)。

## 工程边界

- 第一层：`world/contracts/motion/imports/field_evidence/multimodal`，记录来源、未知、事件和时变占用。真实服务只读取项目`config/multimodal.local.json`及专用环境变量，参见`config/multimodal.example.json`，未配置则明确未接入；无尺度图像不产生可靠米制参数。
- 第二层：`analysis/geometry/verification/reference_geometry/incremental`，连续航段、同时间动态几何、预算化检查顺序及独立数值参考。动态误差膨胀和容差见[MOTION](docs/MOTION.md)。仍可能保守误报，未知/超限不能放行。
- 第三层：`planning/cooperation/resources/execution/storage`，有限SIPP与重排回溯、修复集合扩展、共享资源、同机衔接、冻结前缀、事务采纳。SIPP可切换完整范围与时变通行窗口，等待/航段带保守误差界；不声称全局最优或完备。

只支持AABB、竖直圆柱、胶囊及分段线性航迹。吊物摆动、小车变幅、臂架俯仰、精细网格、真实遥测/飞控、地面运输路径和车辆容量未实现。周转、能耗采用显式简化模型。真实施工演化训练、真实现场泛化、任意字段补证/坐标转换、服务多实例恢复仍待后续。全部研究路线保留，不把合成实验当作现实部署效果。

技术依据仍为[工程整理路线](docs/TECHNICAL_ROUTE.md)；`docs/source/`无最终3.2原稿。恢复开发前读[AGENTS](AGENTS.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md)、[PROGRESS](docs/PROGRESS.md)及当前Git状态。下一步需要有许可、时空对齐和测量尺度的现场材料、任务/资源参数、真实历史序列及明确项目服务配置。
