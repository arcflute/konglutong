# 首轮实际交付与验证记录

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
