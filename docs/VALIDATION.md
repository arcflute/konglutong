# 首轮实际交付与验证记录（历史）

第二轮当前结果见本文件后半部分；以下首轮条目保留当时状态，不代表当前未实现项。

日期：2026-10-23（Asia/Shanghai）。目录：`/root/autodl-tmp/chengkong-zhihu`。这是合成软件原型的执行记录，不是现实运行认证或完整技术路线完成报告。

## 实际修改与新增文件

| 类别 | 文件 |
|---|---|
| 新增工程/安装 | `pyproject.toml`、`requirements.txt`、`requirements-build.txt`、`scripts/bootstrap.sh` |
| 新增第一层/接口 | `src/chengkong/contracts.py`、`world.py`、`learning.py`、`evidence.py` |
| 新增第二层 | `src/chengkong/geometry.py`、`analysis.py`、`verification.py` |
| 新增第三层 | `src/chengkong/planning.py` |
| 新增公共入口/持久化 | `src/chengkong/__init__.py`、`__main__.py`、`pipeline.py`、`cli.py`、`storage.py` |
| 新增验证 | `tests/test_world.py`、`test_geometry.py`、`test_verification.py`、`test_pipeline.py`、`test_safety_regressions.py`、`test_planning.py`、`test_analysis_scope.py` |
| 新增文档 | `docs/ENVIRONMENT.md`、`docs/DATA_CONTRACTS.md`、`docs/VALIDATION.md` |
| 修改启动/状态文档 | `README.md`、`docs/IMPLEMENTATION_PLAN.md`、`docs/FEATURE_MATRIX.md`、`examples/README.md`；`docs/TECHNICAL_ROUTE.md`只更新开头状态声明，保留全部方法正文 |
| 修改配置说明 | `.env.example`明确哪些变量实际读取；`.gitignore`新增Python安装元数据排除 |
| 本地生成且忽略 | `.venv/`、`runs/`、Python缓存及安装元数据；`.git/`仅本地版本管理 |

原有AGENTS、项目说明、首轮任务、上传说明、原稿放置说明与合成JSON保留。`docs/source/`未提供明确最终版3.2，不能宣称完成原稿差异核对；具体待核对项已记入实施计划。未更改系统驱动、全局CUDA、原Conda环境、无关文件，未下载模型权重、未设置Git远端、未推送。

## 实际执行命令与结果

下面命令均已实际执行；运行目录为项目根目录。

| 命令 | 实际结果 |
|---|---|
| `python3 -m venv .venv` | 成功，独立prefix位于核验的数据盘 |
| `.venv/bin/python -m pip install --no-build-isolation --no-deps -e .` | 首次因缺wheel失败；仅venv补装wheel0.45.1后成功，详见ENVIRONMENT |
| `bash scripts/bootstrap.sh` | 安装脚本自身执行成功，复用既有venv和固定构建工具 |
| `.venv/bin/chengkong analyze --output runs/analysis.json` | 成功；产生1个独立复核通过的detour联合候选 |
| `.venv/bin/chengkong demo --output runs/demo` | 成功；3情景×4时刻，共12份快照与summary |
| `PYTHONPATH=src python3 -m chengkong analyze --output runs/source-entry.json` | 源码无安装入口实际运行成功（本轮末次元数据增强前已验证该入口） |
| `.venv/bin/python -m unittest discover -s tests -v` | 最终64项测试全部通过；当次执行0.532秒，非性能基准 |
| `.venv/bin/python -m compileall -q src tests` | 成功，无编译错误 |
| `.venv/bin/python -m pip check` | `No broken requirements found.` |
| `git check-ignore .env .venv/bin/python runs/analysis.json ...pyc` | 凭据路径、环境、运行结果、缓存均被忽略 |
| `git diff --cached --check` | 修正一个文件末尾空行后成功，暂存源码/文档无空白错误 |

日志 `runs/test-results.txt` 保存完整最终测试输出。初期独立测试曾因尚未安装且未加PYTHONPATH发生模块导入失败，设置正确源路径/安装后解决；不将这次失败当作通过。回归期间发现的历史改写和跨情景更新测试问题已修正，最终64项通过才计入本表。

## 已验证的实际链路

JSON导入并校验单位/时间/引用 → 建立几何对象与语义关系 → 按观测时刻释放施工事件 → 保存状态前后版本 → 生成物理与活动约束 → 保守筛选及连续时空冲突 → 有限图SIPP搜索 → 保留任务/服务要求并生成完整联合候选 → 重建全部世界约束独立复核 → JSON输出。可见记录补证已接入，默认无完成证据时返回unknown。SQLite显式采纳在临时数据库中验证，CLI没有采纳任何现行计划。

默认 `end_unconfirmed@60` 的手工可核算结果与程序吻合：

| 项目 | 实际结果 |
|---|---|
| task_1活动冲突 | `[135.7,148.3]` 秒；水平航段两端都在圆柱外，中间连续穿越仍被检出 |
| task_2活动冲突 | `[395.7,408.3]` 秒；计划结束360不能解除未确认活动 |
| task_3活动关系 | 在声明的几何与保护参数下空间分离；完整原计划另作任务与资源复核 |
| 维护方案 | 1个detour联合候选，3项任务保留；复核状态`conditionally_feasible` |
| task_1/task_2变化 | 各新增13.7746801649米、晚到1.3774680165秒，起飞时间不变 |
| task_3变化 | 无变化 |
| 默认补证 | `unknown`、`answer=null`、无伪造证据、世界未更新 |
| 学习状态 | VLM/LLM=`not_configured`；其他学习与校准=`not_trained` |

三分支在60秒都没有未来完成证据，除分支标识外可见状态一致。按期分支360秒环境版本2，task_2对该活动clear；延期分支330秒版本2、计划改600但未确认，task_2仍conflict；600秒延期确认后版本3，仍保留task_2过去395.7—408.3秒的冲突记录。未确认分支版本1、占用维持到计算时域末端，900秒之后仍未知。所有未拆除分支均保留塔身和臂杆物理约束。

原始任务1在330秒之前已经开始，因此后续快照只做历史回放与当前影响分析，返回`needs_operational_review_started_task`，不会编造回到过去的维护方案。这不等于已实现真实在途修复。

## 必要检查覆盖

- 明确单位/高度/时间基准，拒绝NaN/Inf、时间倒序、非法引用；未知几何保留并返回未检查。
- 观测时刻隔离未来事件，计划结束和计算时域都不等于确认结束；活动完成不等于设施拆除。
- AABB、胶囊体、圆柱保守外包络连续相交；中间穿越、边界接触、半开时间、超时域与保护余量单独触发。
- 候选重建全部约束，空/删改约束集不能绕过；不许删任务/服务或篡改保护、能耗、窗口、速度要求。
- 独立任务能耗与服务停留；飞机连续相对运动交叉、同机重复预约、共享起降点容量与显式地面等待。缺少同机转场/补能模型时保持未检查。
- 有限活动区间的独立搜索fixture在154.300001秒延后起飞，保留地面等待与服务，飞行能耗114；这是声明有限占用的单元场景，不是对原包未来完成事件的偷看。
- 默认未知活动的直接延后搜索无结果，绕行实际成功；预算耗尽单独报告，不写成已证明不可行。未知未来几何即使与原计划不同时也不能在规划时崩溃或被忽略。
- 版本变化使旧候选待复核，时间前进后已启动任务不可改写；保存建议不采纳，显式采纳事务更新版本/预约，重复过期候选拒绝；证据更新保留现行任务。
- 补证只能查询可见记录，预算/截止/陈旧版本各有明确状态；模型未训练不产生伪分数/覆盖率。

## 产物位置与复现

主结果：`runs/analysis.json`。演示索引：`runs/demo/summary.json`。完整测试日志：`runs/test-results.txt`。当前产物大小/校验值：`runs/evidence-manifest.json`；运行目录不纳入Git，重跑可再生成。

保留的合成JSON SHA256：`e4715a91f060c17d7d5d7935a5348002d291dcfa421926251653f5ca77d18461`。

## 尚未实现或训练

多模态真实服务、XGBoost敏感性、GNN关联、学习排序、分位数/共形校准、历史演化学习均未训练/接入。只有可替换接口、真实不可用状态与有标识规则回退。

当前占用体在时间区间内固定；完整设备运动、暂停/恢复/新增等事件、复杂三维图、有限回溯、修复集合扩展、同机转场补能、在途滚动维护、选择性增量重算、决策价值补证与自动写回、训练/校准数据流水线、前端及真实环境效果尚未完成。多任务求解目前由障碍冲突触发；仅资源/任务条件不满足时完整复核会拒绝，但自动联合修复触发待扩展。几何手算/回归已做，独立第三方几何引擎和完整对照实验尚未运行。

下一步工作及保留的完整路线见 `IMPLEMENTATION_PLAN.md` 与全部32项 `FEATURE_MATRIX.md`（8项W、10项C、10项P、4项I）。


# 第二轮实际交付与验证记录

日期：2026-10-23 UTC。当前环境/代码/产物均重新检查，未直接采用首轮报告。起始工作区干净，93f10e2存在，main未回退；docs/source只有README，没有最终3.2。全部为本地软件和合成实验，不是现实部署效果。

## 实际阶段与本地提交

| 阶段 | 实际结果 | 本地提交 |
|---|---|---|
| 审计、环境与清单 | 64项基线重新通过，定位原占位/几何共享/任务2判断条件 | dbe3dfd |
| 事件、增量与补证 | 事件全类型、双时间/迟到/待生效、依赖、两种触发和校验写回 | 042a272 |
| 协同与在途 | 有限重排回溯、扩集合、预约、同机资源、真实稳定性代价、冻结前缀及独立几何参考 | ea91c8f；驻留边界修复ced20e5 |
| 实际小模型训练 | 分组生成/标注、XGBoost/GNN/排序/分位数及独立校准、保存加载与实际对照 | 0e9ed5b |
| 多模态与Agent | 结构化服务适配、模拟/超时、引用与尺度校验、受控更新、统一补证请求 | 7f56ee9 |
| Web与采纳事务 | 实际后端+SQLite+静态界面、版本校验/回滚/并发、浏览器闭环 | 3d40cc9 |
| 最终验收与文档 | 下列实测命令、说明、截图和模型结果 | 收尾提交见Git日志 |

阶段2和3并行开发，因此提交顺序不等于用户清单编号顺序。没有配置远端或推送。Git作者采用各次提交局部`-c user.name=Codex -c user.email=codex@local.invalid`；首次学习阶段提交因无全局作者身份失败后用相同局部身份成功，没有更改全局Git配置。

## 执行命令与产物

以下均在项目根实际执行。完整回归日志为runs/round2-final-tests.txt，末次139项通过（10.081秒）。初次136项通过后，追加实证发现驻留与施工包络交叠漏检；修复并增加3项强回归后执行末次检查，没有降低断言或重复训练。

| 命令/检查 | 实际结果 |
|---|---|
| `.venv/bin/python -m unittest discover -s tests -v` | **139项通过（10.081秒）**；覆盖原64项及新增语义/资源/学习/事务/HTTP，详见日志 |
| `.venv/bin/chengkong analyze --output runs/analysis-v2.json` | 1个完整复核通过候选，原任务未改 |
| `.venv/bin/chengkong demo --output runs/demo-v2` | 12快照及summary.json |
| `.venv/bin/chengkong analyze --as-of 360 --inflight --output runs/inflight-v2.json` | 从当前模拟状态产生1个复核通过候选，已执行前缀保留 |
| `.venv/bin/chengkong analyze --model-dir /root/autodl-tmp/chengkong-models/round2 --learned --output runs/learned-v2.json` | 实际加载训练产物、输出排序和结束误差区间、1个复核通过候选 |
| `scripts/demo_cooperation.py --case cooperation --output runs/demo-cooperation`（用venv Python） | 3个失败顺序后task_2→task_1成功；绕行新增关联task_3；转场余能−114明确拒绝 |
| `scripts/demo_cooperation.py --case inflight --output runs/demo-inflight`（用venv Python） | 120/210/360三个时刻各3项任务前缀保持，历史冲突单列；3份完整计算输出 |
| `.venv/bin/python -m chengkong.training evaluate --config configs/training-small.json` | 从保存权重重新加载，结果与小清单一致；runs/round2-evaluation-command.json |
| `.venv/bin/chengkong serve --host 127.0.0.1 --port 8765 --db-dir runs/web-final-qa --model-dir /root/autodl-tmp/chengkong-models/round2` | 实际启动，GET health和state返回ok/v2/1候选/loaded_synthetic；runs/round2-server-entry.json |
| `.venv/bin/python scripts/browser_smoke.py`（下述局部环境变量） | 6组实际UI工作流、14次API请求、0页面脚本错误；桌面/390px页面无横向溢出 |
| `.venv/bin/python -m compileall -q src tests scripts` | 成功 |
| Playwright自带Node `--check src/chengkong/static/app.js` | JS语法通过 |
| `.venv/bin/python -m pip check` | No broken requirements found |
| editable安装 | chengkong-zhihu0.2.0成功，仅项目venv |
| findmnt/df | 数据盘XFS/rw，50G总量/约49G余量，保留原环境和无关文件 |

模型目录约292KiB，合成数据目录约3.4MiB；未下载大型AI权重。产物校验清单`runs/round2-artifact-manifest.json`包含实际文件尺寸及SHA256。原始合成例JSON未修改。

## 关键验收与已修复问题

- 原始连续航段穿越仍按135.7–148.3、395.7–408.3秒检出。任务2仅在收到完成且有效结束早于其穿越时才clear；延期确认不抹掉已经发生的冲突；完成不拆除塔吊。
- 独立复核不调用SIPP搜索器，从当前world重建所有约束，保留任务/服务/窗口/能耗条件。新增reference_geometry采用独立距离最小化参考与手算边界，主规划/主复核仍共享底层primitive；没有声称完全独立引擎或形式化证明。
- 显式预算区分超时、搜索耗尽、信息不足、有限图未找到；绕行引入新航线冲突或起降点冲突后扩集合重算。保留所有服务要求，优先级顺序失败有实际记录。硬检查通过后按变更任务数、正延误、正增距、正增耗的字典序比较。
- 迎面/交叉/同向连续保护距离、共享起降/显式等待容量、同机转场时间/能耗/补能及在途前缀均有专项检查。收尾复查实际发现周转驻留未查禁等规则及邻近飞行器，新增两条强回归后修复；进一步实证发现驻留[244,350]与短时施工[260,300]交叠漏检，现同时进入完整世界约束检查和独立reference；新增3项检查涵盖未知几何及在途历史区分。地面carrier运输途中避障/载具容量仍为未建模范围。
- 完整设施/活动事件、同ID幂等或冲突拒绝、有效/到达时间分离、未来生效及迟到重建已验证。增量缓存与相同world下全量结果逐字段一致；新几何不只沿旧关联查，资源全量复算，候选依赖保守扩大。Web实际接入候选失效图，事件和采纳后旧候选均列入失效集合。
- 补证两触发与Agent共用请求ID/预算/期限。读取已有可见或明确模拟记录；无证据/超时/预算耗尽保留未知。Agent无法定位字段则人工识别；真实HTTP验证材料缺项→请求→模拟记录→第一层→任务2转clear、设施仍在、计划不自动改变。其他高度/坐标字段适配器未接入。
- SQLite测试在事务写入中注入失败，world/预约/history/decision完全回滚；双线程各自连接竞争同revision，仅一个提交；过期环境/预约/计划拒绝；候选同ID异内容拒绝，历史decisions不可变。
- 实际发现并修复：材料引用未持久保存、嵌套活动几何未做图片尺度检查、refs=[42]引起AttributeError、顶层JSON数组断开请求。现有回归验证明确结构化拒绝、事件引用持久保存且world未变。
- 两个旧断言因真实功能升级调整：未来有效事件由“不支持”改为“待生效不确认、生效后激活”；结果schema v1→v2。没有降低安全/冲突/未知/采纳断言。

## 浏览器操作证据与复现

脚本启动真实SimulationApp、随机loopback端口与临时SQLite，使用Chromium点击真实控件。它验证初始SVG与任务表→切分支→路径预览→事件→旧候选拒绝→新候选采纳→360秒未知→查询失败保持未知→显式合成证据更新→inflight/learning后端→模拟材料待接受与接受→只读60秒回放→返回360秒当前状态。断言来自HTTP结果和DOM，不用写死业务答案。输出：

- `runs/round2-browser/report.json`：14次实际API路径、6组工作流及page_errors=[]。
- 同目录`initial.png / adopted.png / evidence.png / models.png / mobile.png`：实际浏览器截图。
- 首次截图因服务器无中文字体出现方框，局部解压Noto CJK后重新检查；产品本身使用系统字体，不依赖外网字体资源。

此服务器复验命令：

```bash
PLAYWRIGHT_BROWSERS_PATH=/root/autodl-tmp/chengkong-data/browser \
LD_LIBRARY_PATH=/root/autodl-tmp/chengkong-data/browser-libs/extracted/usr/lib/x86_64-linux-gnu \
FONTCONFIG_FILE=/root/autodl-tmp/chengkong-data/browser-libs/fonts.conf \
.venv/bin/python scripts/browser_smoke.py
```

Playwright是可选验收依赖，服务运行不依赖它。浏览器缺库仅下载deb到数据盘解压，未更改全局系统库，详情ENVIRONMENT。未注册/发布站点或开放公网；QA使用的自建服务已通过Ctrl+C关闭，用户按README重新启动。

## 学习的实际结果与边界

模型版本synthetic-round2-001cfc579ce2，48模板/144事件，完整模板划分为66训练/24验证/27校准/27测试事件。随机种子20261023、可见特征定义、标签/配置/校验和/模板归属已保存；真值另文件，在线特征不接收未来结局。每模型1组配置，训练作业硬超时90秒，未无限调参或以测试集选择参数。

| 合成测试指标 | 模型/校准 | 基线/未校准 |
|---|---:|---:|
| XGBoost敏感性MSE | 0.04492 | 0.38250 |
| GNN排序NDCG | 0.37852 | 包围盒0.40125 |
| 修复排序NDCG@3 | 0.62421 | 规则0.52738 |
| 首个提案可行率 | 54.02% | 规则54.02%；全部动作无学习枚举67.82% |
| 首次可行平均真实代价 | 1.6934 | 规则1.6765 |
| 结束误差经验覆盖 | 100% | 62.96% |
| 结束误差区间宽度 | 346.82秒 | 80.81秒 |

GNN和修复代价没有优于基线，因此规则默认保留。排序对照重放相同实际求解结果，不宣称实测端到端加速；校准只有9独立组，100%是27合成测试事件的经验覆盖且区间很宽。它不等于真实施工泛化、整条航线安全概率或现实安全认证。消息传递梯度、邻接影响、分组隔离、禁止答案特征和模型保存加载8项必要测试已实际执行。更多指标/假设见LEARNING与configs/learning-artifacts.json。

## 本轮修改范围与保留缺项

实际文件以`git diff --name-only 93f10e2 HEAD`核对；核心新增cooperation/resources/execution/reference_geometry/incremental、synthetic/training/model_runtime、multimodal/web/static和两个演示脚本，修改world/contracts/analysis/planning/verification/evidence/learning/pipeline/storage/cli与对应测试，更新README、契约/功能/实施/环境/验收/进度及训练配置。保留原始合成输入、技术路线完整方法、根约定与原稿目录；未重置用户文件。

未完成/未验证：真实VLM/LLM服务；真实施工历史演化学习和外部校准；无尺度图像的可靠米制重建；连续吊臂动力学、任意三维图、地面运输路径/载具容量、真实飞行动力学；大型任务集与系统级关闭补证/全量重规划统计对照；真实地图、真实飞控、现实运行及分布式多用户服务。当前物理和学习假设限制已写入条件与说明，不能当作完整研究路线完成。

所需用户资料：最终3.2原稿；带许可的施工记录/材料和可核对有效/到达时间；测量/尺度/坐标高度基准；真实任务/资源/周转/补能参数；项目内显式配置的模型服务、协议、授权和预算。具体后续任务表见IMPLEMENTATION_PLAN。

## 相对93f10e2的实际文件清单

以下来自实际`git diff --name-only 93f10e2`（含本次文档工作区）；环境、权重和运行产物被忽略并保留在数据盘。

```text
.env.example
.gitignore
README.md
config/multimodal.example.json
configs/learning-artifacts.json
configs/training-small.json
docs/DATA_CONTRACTS.md
docs/ENVIRONMENT.md
docs/FEATURE_MATRIX.md
docs/IMPLEMENTATION_PLAN.md
docs/LEARNING.md
docs/PROGRESS.md
docs/TECHNICAL_ROUTE.md
docs/VALIDATION.md
pyproject.toml
requirements-dev.txt
requirements-learning.txt
scripts/browser_smoke.py
scripts/demo_cooperation.py
src/chengkong/__init__.py
src/chengkong/analysis.py
src/chengkong/cli.py
src/chengkong/contracts.py
src/chengkong/cooperation.py
src/chengkong/evidence.py
src/chengkong/execution.py
src/chengkong/incremental.py
src/chengkong/learning.py
src/chengkong/model_runtime.py
src/chengkong/multimodal.py
src/chengkong/pipeline.py
src/chengkong/planning.py
src/chengkong/reference_geometry.py
src/chengkong/resources.py
src/chengkong/static/app.js
src/chengkong/static/index.html
src/chengkong/static/style.css
src/chengkong/storage.py
src/chengkong/synthetic.py
src/chengkong/training.py
src/chengkong/verification.py
src/chengkong/web.py
src/chengkong/world.py
tests/test_cooperation.py
tests/test_events_v2.py
tests/test_evidence_v2.py
tests/test_execution.py
tests/test_incremental.py
tests/test_learning_v2.py
tests/test_multimodal.py
tests/test_pipeline.py
tests/test_reference_geometry.py
tests/test_resources.py
tests/test_storage_v2.py
tests/test_web_api.py
tests/test_world.py
```
