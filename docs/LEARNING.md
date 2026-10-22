# 第二轮小规模合成学习实验

已实际运行 CPU 合成数据生成、精算标注、XGBoost 敏感性回归、可训练图消息传递网络、XGBoost pairwise 修复排序及双分位数/独立共形校准。权重可保存、加载和推理。它们只提供精算/补证/处理顺序，不能排除检查、判定安全或自动采纳。默认仍为确定性规则。

本轮只配置每个模型 **1 组**超参数，没有按测试分数调参。`configs/training-small.json`配置最多1组，训练器拒绝超过3组的预算。每个训练作业由父进程`subprocess.run(timeout=90)`限时（包括原生库代码），每个参考求解有3秒/256展开限制，总生成上限600秒。CPU依赖仅装入项目`.venv`，不使用Torch、大模型、驱动或CUDA变更。

## 命令与产物

```bash
cd /root/autodl-tmp/chengkong-zhihu
.venv/bin/python -m pip install -r requirements-learning.txt
.venv/bin/python -m chengkong.training benchmark --config configs/training-small.json
.venv/bin/python -m chengkong.training train --config configs/training-small.json
.venv/bin/python -m chengkong.training evaluate --config configs/training-small.json
.venv/bin/python -m unittest discover -s tests -p test_learning_v2.py -v
```

`train`包含生成、训练、独立校准、保存、重新加载和评估；不需要从其他目录找凭据。训练通过之前不会更新仓库内的完成清单。可修改配置中的输出目录建立另一套实验，保留原产物。

- 数据盘`/root/autodl-tmp/chengkong-data/round2/`：`benchmark.json`、`visible_scenarios.jsonl`、`simulator_truth.jsonl`、`dataset.json`、`data_manifest.json`。
- 数据盘`/root/autodl-tmp/chengkong-models/round2/`：`sensitivity.json`、`ranker.json`、`gnn.npz`、`quantile_lower.json`、`quantile_upper.json`、`calibration.json`、`manifest.json`、`evaluation.json`。
- Git只保存代码、依赖、`configs/training-small.json`与小型`configs/learning-artifacts.json`。清单包括模型版本、随机种子、特征名、模板划分、实际完整指标和运行预算。模型清单记录权重校验和；加载时验证。原始数据和权重不进入Git。

## 可见性、分组与标签

生成器覆盖不同设施中心、半径、高度、布局长度、任务数量（2–5）、飞行高度/速度、服务时长、时间窗、能量余量、开始/计划延期模式、缺失活动几何和共享起降点冲突。全部是局部米制合成情景，原始例不是现实标注数据。

固定种子20261023；48个独立完整布局/事件模板，每模板3个近似变体，共144事件、498任务、996任务约束对、1494个实际求解前提案。先以6事件测生成与标注耗时，再决定本次规模。每模板的变体只属于一个集合：训练22模板/66事件，验证8/24，校准9/27，测试9/27。同模板近似副本不得跨组。模板ID和事件ID只用于索引和分组，不作为特征。

`visible_scenarios.jsonl`只含决策可见世界；未来实际结束、隐藏几何和延迟结果单独存入`simulator_truth.jsonl`。在线函数的参数只有`world`、约束和待求解动作；不接收真值文件。记录的计划结束不被当作确认结束，模型预测也不用于释放占用。

| 模型 | 可见输入 | 标签及使用位置 |
|---|---|---|
| XGBoost敏感性 | 包围盒净空、时段重叠、航路距离/时长、缺失标识、未确认、来源时效、已知计划时间、任务余量/优先级 | 连续几何精算：活动半径0.75/1/1.25倍与计划/模拟实际/保守时域结束组成9种扰动的冲突比例。物理占用单次精算。输出处理/补证优先级，比例不是事故概率。 |
| 小型GNN | 任务特征、由可见共享资源/时段和保守走廊建立的邻接矩阵 | 标签为直接扰动影响和实际联合资源冲突组成的影响度，再加邻域影响；用于多任务复核顺序，不是最优修复集合。 |
| XGBoost排序 | 节点可见特征＋keep/delay/detour动作＋已知计划剩余时长和约束数 | 对每任务三动作实际调用原计划检查/SIPP，再独立检查任务、连续几何和固定其他任务预约。仅标签包含求解可行性、实际时间/距离/能耗代价。pairwise relevance按合格候选实际代价排序。运行时发生在求解前。 |
| 双分位数＋校准 | 已知计划时长、已过去时间、来源时效、已知延期量、已知半径及缺失标识 | 模拟实际结束减记录计划结束的连续误差。仅用于区间建议，不覆盖完成事实。 |

GNN实际计算`H=ReLU(XW1+b1)`、`M=D^-1/2 A D^-1/2 H`、`p=sigmoid(MW2+b2)`，MSE梯度经邻接传播回传至两层权重，使用NumPy Adam训练180轮。测试检查改变邻接会改变预测、所有参数组的有限差分梯度、权重更新与保存加载。因此不是每节点独立MLP或随机分数接口。

模型不互相读取其他模型的预测作输入，因此本轮没有需要折外预测的上下游链；清单明确`upstream_predictions_in_downstream_features=false`。未来加入上游分数时仍须先做折外预测或独立生成。

标签保留`no_solution_in_bounded_graph`、`search_budget_exhausted`、`timeout`、`unsupported_or_incomplete_search_input`等实际状态，不把所有失败包装成已证明不可行。排序标签只评价单任务对其他固定预约的局部提案，不声称多任务联合最优；在线完整候选仍须联合复核。

## 对照和校准语义

完整实际数值以`configs/learning-artifacts.json`与模型目录`evaluation.json`为准。评估包括XGBoost与未知优先/包围盒规则、GNN与包围盒/任务优先级规则、学习排序与keep→delay→detour规则、无学习完整求解全部动作，以及未校准区间对照。排序对照重排已经实际执行的相同提案，不把重放的尝试次数变化宣称为实测端到端加速。不可行查询的NDCG记0，完整结果保存查询数与可求解数。

校准仅使用9个独立校准模板：先求每事件区间外残差，再取同模板3变体最大残差，以第`ceil((9+1)×0.8)=8`个组分数扩大区间。目标是同分布合成模板族中该单变量区间的组覆盖，依赖模板可交换性；27条校准行不当作27个独立模板。测试覆盖率是小样本经验结果，不能解读成整条航路、现实飞行或联合安全概率。区间可能非常宽；没有通过缩窄区间换好看的指标。

当前训练结果中，GNN排序未优于规则，学习排序首次可行的实际平均代价也略差于规则/完整枚举。即使敏感性误差或提案NDCG较好，也不足以切换默认策略。保留确定性规则默认、显式启用学习实验；所有候选最后按真实硬约束与实际稳定性代价比较。

## 本次最终产物与实测结果

模型版本`synthetic-round2-001cfc579ce2`。最终生成耗时2.19秒；包含进程启动的五个实际训练作业分别约0.74、1.64、0.79、0.79、0.81秒，均在90秒预算内。GNN训练损失由0.14677降至0.02404；测试表现仍须单独判断，不能以训练损失代表泛化。

测试集为9模板/27事件、174任务约束对和87个局部排序查询，其中59个至少有一个可行提案。

| 项目 | 学习/校准结果 | 对照 |
|---|---:|---:|
| 敏感性MSE（越低越好） | 0.04492 | 包围盒规则0.38250 |
| 敏感性NDCG | 0.40344 | 未知字段优先0.35997 |
| GNN任务排序NDCG | 0.37852 | 包围盒规则0.40125；任务优先级规则0.34513 |
| 候选排序NDCG@3 | 0.62421 | keep→delay→detour规则0.52738 |
| 第一个候选可行率 | 54.02% | 规则54.02%；无学习完整枚举67.82% |
| 可解查询找到第一个可行候选的平均求解次数 | 1.2034 | 规则1.4068 |
| 第一个可行候选的平均实际代价 | 1.6934 | 规则及完整枚举最小值1.6765 |
| 结束误差单变量经验覆盖 | 校准100% | 未校准62.96% |
| 结束误差区间平均宽度 | 校准346.82秒 | 未校准80.81秒 |

GNN未超过包围盒规则；排序虽然少评估部分提案，首选可行率没有提升且实际代价略差。因此本轮不采用学习作为默认，不宣称性能全面提升。覆盖率100%仅指这27个合成测试事件，校准只有9独立组且区间很宽。

`tests/test_learning_v2.py`的8项必要检查实际通过。`runs/learning-pipeline-comparison.json`另外记录了包内初始场景与4任务共享资源合成场景的实际后端规则/学习对照：各自两种模式均产生1个经完整复核通过的候选，基线冲突分类一致、当前world未修改、所有任务保留检查、可行候选无未检查项或硬约束违反。这里没有真实飞控或现实部署评价。

## 加载与接入

```python
from chengkong.model_runtime import ModelBundle
models = ModelBundle('/root/autodl-tmp/chengkong-models/round2')
models.sensitivity(world, constraints)          # pair_order + scores
models.association(world, constraints)          # task_order + scores
models.rank_proposals(world, constraints, [
    {'id':'delay','action':'delay','task_ids':['task_1']},
    {'id':'detour','action':'detour','task_ids':['task_1']},
])                                            # proposal_order + scores
```

`end_error_interval(features)`返回误差区间、未校准区间、目标量、名义覆盖和分组条件。`capability_statuses(model_dir)`仅在显式加载目录时报告`trained_synthetic`；未加载的后端保留`not_trained`兼容状态，同时原因明确为“本运行时未加载产物”，不冒称已执行模型。真实权重是否存在、是否加载、是否作为默认策略是三件不同的事。

真实历史施工演化模型仍未训练；没有真实施工数据、视频时序或现实泛化评估。需用户后续提供带许可、时间对齐、观测到达时间、实际结束/拆除确认、尺寸来源与任务资源记录的数据；小型合成实验不能替代这些材料。

XGBoost目标函数与分组排序接口依据官方参数文档，并已在本项目安装的CPU 3.0.5上实际运行：[XGBoost Parameters](https://xgboost.readthedocs.io/en/stable/parameter.html)。
