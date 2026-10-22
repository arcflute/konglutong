# 第三轮交付索引

本轮从实际9d30ece开始，当前工程阶段提交为85d1072；本索引及最终恢复记录另有文档提交。没有推送远端，所有模型/数据在数据盘，round2未覆盖。

## 阶段提交

- `b9cd253` docs: freeze round-two artifacts and record round-three audit
- `962672c` feat: model crane motion with conservative continuous collision checks
- `bb821cb` feat: import traceable materials and reconcile field evidence
- `4793f3b` feat: train and audit grouped round-three learning with measured baselines
- `85d1072` feat: connect material review and versioned spatial views to live backend

## 使用与结果

- [README](../README.md)：后端/前端同进程、SSH、动态设备场景、材料导入、三套可重复操作、独立训练和评估命令。
- [VALIDATION](VALIDATION.md)末尾第三轮：183项全量+2项追加边界、20 API浏览器、CLI/导入/协同/在途和旧产物校验实际结果。
- [LEARNING_ROUND3](LEARNING_ROUND3.md)：5指标分母、实际求解/失败率、强规则事后诊断、无可靠整体增益结论。
- [MODEL_CARD_ROUND3](MODEL_CARD_ROUND3.md)：模型输入、真实使用位置与适用边界。
- [MOTION](MOTION.md)、[IMPORTS](IMPORTS.md)、[DATA_SOURCES](DATA_SOURCES.md)：运动算法/误差界、支持字段与格式、许可和来源。
- `runs/round3/verified-browser/`：report.json、export.json及五张真实界面截图。
- `configs/round3-delivery.json`：实际修改列表、代码SHA256和验证摘要。

第三轮模型为synthetic-round3-a774ca1b9f04。原始评价与修正分开；同机关系/完整序列/动态细节不在学习训练覆盖内。默认规则，所有学习排序保留硬约束复核。在线校准输出区间，动作影响目前仅离线条件建议统计，不自动维护或放行。

## 实际修改文件（相对9d30ece）

- `README.md`
- `config/multimodal.example.json`
- `configs/learning-round3-artifacts.json`
- `configs/round3-baseline.json`
- `configs/training-round3.json`
- `docs/DATA_CONTRACTS.md`
- `docs/DATA_SOURCES.md`
- `docs/FEATURE_MATRIX.md`
- `docs/IMPLEMENTATION_PLAN.md`
- `docs/IMPORTS.md`
- `docs/LEARNING.md`
- `docs/LEARNING_ROUND3.md`
- `docs/MODEL_CARD_ROUND3.md`
- `docs/MOTION.md`
- `docs/PROGRESS.md`
- `docs/ROUND3_AUDIT.md`
- `docs/VALIDATION.md`
- `examples/imports/construction_delay.csv`
- `examples/imports/construction_delay.json`
- `examples/imports/geojson_metadata.json`
- `examples/imports/metadata.json`
- `examples/imports/park_tasks_resources.json`
- `examples/imports/public/manifest.json`
- `examples/imports/public/usgs_one_event.geojson`
- `examples/imports/surveyed_pad.geojson`
- `examples/motion_crane_synthetic.json`
- `scripts/browser_round3.py`
- `src/chengkong/analysis.py`
- `src/chengkong/contracts.py`
- `src/chengkong/diagnostics_round3.py`
- `src/chengkong/evaluation_round3.py`
- `src/chengkong/field_evidence.py`
- `src/chengkong/imports.py`
- `src/chengkong/learning_round3_runtime.py`
- `src/chengkong/model_runtime.py`
- `src/chengkong/motion.py`
- `src/chengkong/multimodal.py`
- `src/chengkong/pair_keys.py`
- `src/chengkong/pipeline.py`
- `src/chengkong/planning.py`
- `src/chengkong/presentation.py`
- `src/chengkong/reference_geometry.py`
- `src/chengkong/static/app.js`
- `src/chengkong/static/index.html`
- `src/chengkong/static/round3.js`
- `src/chengkong/static/style.css`
- `src/chengkong/storage.py`
- `src/chengkong/synthetic_round3.py`
- `src/chengkong/training_round3.py`
- `src/chengkong/verification.py`
- `src/chengkong/web.py`
- `src/chengkong/world.py`
- `tests/test_imports.py`
- `tests/test_learning_round3.py`
- `tests/test_motion.py`
- `tests/test_multimodal_round3.py`
- `tests/test_presentation.py`
- `tests/test_web_round3.py`

本交付索引、configs/round3-delivery.json以及最终PROGRESS收尾也属于本轮文档修改。后续开发不要把历史首/二轮条目当作当前未完成状态。
