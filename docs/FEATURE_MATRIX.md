# 功能落实清单

本表为第二轮当前状态，覆盖完整三层路线的32项功能。实现、专项验证、合成训练、真实服务接入和现实部署是不同状态；下表每项结论限定在声明范围内。首轮历史保留在`IMPLEMENTATION_PLAN.md`；本轮最终验收以`VALIDATION.md`为准。完整技术路线未因分阶段实现而删减。

| ID | 层 | 方法/功能 | 阶段 | 当前状态、已落实范围及限制 | 代码/证据位置 |
|---|---|---|---|---|---|
| W01 | 第一层 | 几何模型与语义场景图 | P1/P2 | 已实现并验证盒体/竖直圆柱/胶囊体、稳定对象ID、设施—作业—来源关系及版本历史；复杂城市网格、精细设备几何未接入 | `contracts.py`, `world.py`; `test_events_v2.py`, `test_safety_regressions.py` |
| W02 | 第一层 | 来源、有效时间、观测时间与版本 | P0/P2 | 已验证有效时间与到达时间分离、迟到重放、待生效事件、同ID幂等/冲突拒绝；历史决策快照保留当时知识，不用新证据覆盖旧决策 | `world.py`, `storage.py`; `test_events_v2.py`, `test_storage_v2.py` |
| W03 | 第一层 | 施工文本/图片/视频解析适配器 | P3 | 可替换HTTP服务适配器、结构化校验、引用与原文核对、超时/失败处理已通过模拟及本地HTTP验证；真实VLM/LLM服务未配置，未验证真实图片/视频识别效果 | `multimodal.py`; `test_multimodal.py`; 项目内配置模板 |
| W04 | 第一层 | Agent 查询、校验与辅助更新 | P3 | 已验证可见材料查询、字段/证据核对、显式接受后第一层更新与审计；缺字段对接统一预算补证流程已实现并通过真实HTTP联动测试。无尺度图像拒绝米制几何；歧义实体保留人工识别，不宣称自动实体匹配已完成 | `multimodal.py`, `evidence.py`, `web.py`; `test_multimodal.py`, `test_web_api.py` |
| W05 | 第一层 | 设施及活动参数化 | P1/P2 | 已验证设施新增、物理尺寸/姿态几何替换、活动包络变化及分段占用；设施持续占用与作业活动分别维护。当前姿态以显式几何记录表达，连续吊臂回转动力学未实现 | `world.py`, `contracts.py`; `test_events_v2.py` |
| W06 | 第一层 | 规则世界模型与多情景推演 | P1/P2 | 已验证开始、暂停、恢复、延期、完成、拆除的规则转换；暂停保留保守活动占用，计划到期不释放；情景分支不提前泄漏。未为无统计依据的分支添加概率 | `world.py`; `test_events_v2.py`, `test_evidence_v2.py` |
| W07 | 第一层 | 历史数据驱动的演化预测 | 后续研究 | **未训练、未接入真实施工历史数据**；规则世界模型继续为基线。本轮合成结束误差回归不等于学到真实施工演化规律 | `learning.py`; `docs/LEARNING.md` |
| W08 | 第一层 | 事件增量更新与补证写回 | P2 | 已验证对象—约束—任务—候选依赖、任务约束对缓存、保守失效和校验后补证写回；新几何对全任务检查，候选依赖保守扩大，联合资源仍全量重算 | `world.py`, `incremental.py`, `evidence.py`, `pipeline.py`; `test_incremental.py`, `test_evidence_v2.py` |
| C01 | 第二层 | 施工语义规则映射 | P1/P2 | 已验证带来源与版本的物理/活动约束及事件有效时间分段；完成只结束活动，确认拆除才结束设施占用；历史时段保留 | `world.py`; `test_events_v2.py`, `test_world.py` |
| C02 | 第二层 | 动态占用、电子围栏与保护范围 | P1/P2 | 已验证分段固定施工包络、连续飞行器保护体、空间边界与时段交集；动态吊臂精细运动、任意多边形/城市网格围栏未实现。圆柱边缘采用声明的保守外扩 | `geometry.py`, `analysis.py`, `verification.py`; `test_geometry.py`, `test_reference_geometry.py` |
| C03 | 第二层 | 分位数估计与不确定性校准 | P3 | **已合成训练、保存加载和评估**：结束时间误差双分位数＋独立模板分组共形校准。9个校准模板；测试经验覆盖100%但平均区间宽346.82秒。仅单变量、可交换合成模板条件，不是整条飞行安全概率 | `training.py`, `model_runtime.py`; `test_learning_v2.py`; `docs/LEARNING.md` |
| C04 | 第二层 | 保守时空筛选与候选关联 | P1/P2 | 已验证时间重叠/膨胀包围盒、任务约束对依赖与缓存复用；新约束和未知几何不因旧关联图被漏检。专用空间索引加速未实现 | `analysis.py`, `incremental.py`; `test_analysis_scope.py`, `test_incremental.py` |
| C05 | 第二层 | XGBoost 冲突敏感性学习 | P3 | **已合成训练和推理**：可见任务约束特征预测连续几何扰动冲突比例，用于显式学习模式的补证优先级；低分不免检。测试MSE0.04492、包围盒规则0.38250；无现实事故概率含义 | `synthetic.py`, `training.py`, `model_runtime.py`, `pipeline.py`; `test_learning_v2.py` |
| C06 | 第二层 | GNN 多任务关联 | P3 | **已训练真实消息传播及梯度更新**，显式启用时提供任务处理顺序。测试NDCG0.37852低于包围盒规则0.40125，默认规则不变。未使用上游模型分数，无需折外特征；未来加入此类特征仍须折外/独立生成 | `model_runtime.py`, `training.py`, `cooperation.py`; `test_learning_v2.py`; `docs/LEARNING.md` |
| C07 | 第二层 | 连续航段时空冲突精算 | P1/P2 | 已验证航段内部穿越、边界接触、同时间保护距离与迎面/交叉/同向冲突；新增独立参考求交与手算核对。主规划和主复核共享部分几何primitive，参考检查不构成形式化正确性证明 | `geometry.py`, `analysis.py`, `reference_geometry.py`; `test_reference_geometry.py`, `test_resources.py` |
| C08 | 第二层 | 冲突证据与检查完成度 | P1/P2 | 已验证冲突位置/时段/对象/来源/版本、未知和未检查状态，Web展示对应后端结果；未检查或资料缺失不能当作无冲突 | `analysis.py`, `verification.py`, `web.py`, `static/app.js`; `test_analysis_scope.py`, `test_web_api.py` |
| C09 | 第二层 | 冲突驱动主动补证 | P2/P3 | 已验证两种显式结束假设下精算分类变化触发、统一预算/截止/状态、可见或明确模拟来源查询与第一层写回；无结果/超时保持未知，学习只调顺序。当前专门实现完成状态补证，其他字段适配器仍待扩展 | `evidence.py`, `pipeline.py`; `test_evidence_v2.py`; 浏览器补证操作证据 |
| C10 | 第二层 | 候选计划复核接口 | P1/P2 | 已验证从当前world重新生成完整约束，核对任务、几何、时间、预约、能量和版本，并用独立参考补充关键几何；不只检查最初冲突对象，也不调用规划器来宣称自身通过 | `verification.py`, `reference_geometry.py`, `resources.py`; `test_verification.py`, `test_cooperation.py` |
| P01 | 第三层 | 既有任务与时空航路图 | P1/P2 | 已验证保留必达点、顺序、完整服务时长、窗口、任务优先级和任务分配；在途可保留已完成/部分服务。仍为固定巡航高度、垂直起降及有限航路图，不是任意三维飞行动力学规划 | `planning.py`, `execution.py`, `verification.py`; `test_planning.py`, `test_execution.py` |
| P02 | 第三层 | 学习排序修复候选 | P3 | **已训练XGBoost pairwise排序**，输入只含求解前可见上下文/动作，显式启用后实际调整搜索顺序；完整候选仍按实际约束/代价比较。首选可行率与规则相同54.02%，首次可行代价略差，规则仍默认 | `synthetic.py`, `training.py`, `model_runtime.py`, `cooperation.py`; `test_learning_v2.py`, `test_cooperation.py` |
| P03 | 第三层 | 安全时间区间搜索 SIPP | P1/P2 | 已验证安全区间、连续边、能耗标签和预约；新增显式时间/展开/搜索次数预算。结果区分超时、预算耗尽、信息不足和有限图未找到，不宣称全局最优或完备 | `planning.py`, `cooperation.py`; `test_planning.py`, `test_cooperation.py` |
| P04 | 第三层 | 等待、能耗和飞行器资源复核 | P1/P2 | 已验证地面等待位置/容量、空中等待空间与线性能耗、任务期限及同机连续任务余能；转场采用显式虚构地面搬运速度/耗能与周转补能，不瞬移、不重置电量。周转驻留禁等、连续分离、施工占用及独立reference已验证；地面载运路径/车辆容量未建模，搜索尚未提前纳入所有跨任务驻留；真实电池/地面保障/飞行动力学未建模 | `resources.py`, `planning.py`, `verification.py`; `test_resources.py`, `test_cooperation.py` |
| P05 | 第三层 | 多任务预约与有限回溯 | P2 | 已验证有限优先级重排、失败记录、回溯成功及预算耗尽，联合预约覆盖连续间距、共享起降/等待容量和显式同机衔接；预约版本独立维护。当前小规模有限图启发式，不是完整联合搜索 | `cooperation.py`, `analysis.py`, `resources.py`, `storage.py`; `test_cooperation.py`, `test_resources.py`, `test_storage_v2.py` |
| P06 | 第三层 | 修复集合扩展与稳定性优化 | P2 | 已验证绕行/到达改变影响其他任务或起降资源时扩大集合再求解；硬约束优先，然后比较变更任务数、延误、绕行和能耗，输出真实代价与选择依据。更大规模邻域优化仍保留为后续研究 | `cooperation.py`, `verification.py`; `test_cooperation.py` |
| P07 | 第三层 | 决策价值驱动主动补证 | P2 | 已验证对声明的两种结束分支比较保留/修复建议及现有候选偏好，统一预算化请求；假设不进入证据，不越过权威复核。无可信概率，不称期望收益最优；尚未实现任意字段多分支价值优化 | `evidence.py`; `test_evidence_v2.py` |
| P08 | 第三层 | 事件驱动滚动维护 | P2 | 已验证事件/证据更新重算、旧候选失效及软件在途维护：冻结执行前缀，从模拟当前位置/时间/余能继续，保留服务进度。纯软件仿真；未接真实遥测、偏差估计或飞控 | `execution.py`, `pipeline.py`, `world.py`; `test_execution.py`; 浏览器在途操作证据 |
| P09 | 第三层 | 条件化输出与用户采纳 | P1/P2 | 已验证显式采纳时核对环境、任务计划及预约版本，事务内重新复核并原子更新计划/预约；过期拒绝、注入失败回滚、双线程版本竞争一致性已专项检查。候选从不自动覆盖现行计划 | `storage.py`, `verification.py`, `web.py`; `test_storage_v2.py`, `test_web_api.py` |
| P10 | 第三层 | 仿真训练与求解结果标注 | P3 | 已完成小样本耗时测量、48完整模板/144事件生成、996对/1494动作标注和四组隔离，保存种子/配置/特征/代码与产物校验和；真值独立存储，不进入在线特征。参考仅有预算局部求解，不是全局最优标签 | `synthetic.py`, `training.py`; `configs/learning-artifacts.json`, `test_learning_v2.py` |
| I01 | 公共能力 | 数据契约、持久化与事件回放 | P0/P2 | 已验证版本化JSON契约、SQLite计划/预约/决策历史、事件幂等和只读回放；历史“当时知道什么”与当前接收迟到证据后的状态分别保留。多用户权限和分布式事务未实现 | `contracts.py`, `world.py`, `storage.py`, `web.py`; `test_events_v2.py`, `test_storage_v2.py`, `test_web_api.py` |
| I02 | 公共能力 | 前后端集成与方案比较 | P4 | **已接真实后端并浏览器操作验证**：场景、2D位置/高度/时间、事件推进、冲突证据、补证、候选比较、显式采纳/过期拒绝、在途/学习模式和只读回放。仅本地监听＋SSH转发，不依赖地图令牌；未提供大型3D城市界面或公网部署 | `web.py`, `static/`, `pipeline.py`; `test_web_api.py`; `VALIDATION.md`浏览器记录 |
| I03 | 公共能力 | 独立参考检查与对照实验 | P1/P4 | 已执行独立几何参考/手算、增量与全量、规则与学习排序、无学习完整动作枚举、校准前后及两个实际pipeline对照。真实运行效果、整批重规划/关闭补证的系统级统计对照和形式化证明仍未完成 | `reference_geometry.py`, `tests/`, `training.py`; `runs/learning-pipeline-comparison.json`, `docs/LEARNING.md` |
| I04 | 公共能力 | 数据来源、模型记录与运行说明 | 全程 | 已记录合成来源、数据盘权重、四组划分、实际模型结果、运行入口、预算与局限；保留阶段Git提交及恢复记录。无真实施工训练集、现实部署或真实VLM效果验收 | `README.md`, `ENVIRONMENT.md`, `DATA_CONTRACTS.md`, `LEARNING.md`, `VALIDATION.md`, `PROGRESS.md` |

源码路径相对于`src/chengkong/`，测试文件相对于`tests/`。`docs/source/`仍未提供明确标注最终版的3.2，当前按工程整理版实施，后续须核对原稿公式、方法及边界。W07、精细运动、真实多模态、扩展学习与协同优化等路线均保留；本轮软件原型与合成实验不表示整套研究路线已完成。
