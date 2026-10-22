# 第四轮固定演示与操作顺序

全部是本地米制合成仿真，不是现场记录。模型如加载，使用只读round3产物`synthetic-round3-a774ca1b9f04`，不宣称对动态运动、连续任务和事件序列有效。规则为默认；每个候选携带环境、任务计划、预约、规则和模型版本，显式采纳时再次检查。`runs/round4/browser-first/`保存本轮首轮实际输出、截图与请求记录；最终验证位置以VALIDATION为准。

## A：动态窗口、等待和绕行

```bash
.venv/bin/chengkong serve --input examples/round4/dynamic_window.json \
  --db-dir runs/web-round4-A --host 127.0.0.1 --port 8765
```

1. 首次进入T=0，输入来自`record_initial`，明确零运动误差只用于可核算合成规则。塔身持续存在；吊索在(50,60)且0–20秒保持，之后回转。
2. 原航段在7–14秒从(10,60,10)飞往(80,60,10)，11秒穿过吊索。即使两个端点可用，整条航段仍有冲突。
3. 打开维护候选：实际有绕行和等待候选。等待候选从2秒在起点占用地面资源，约17.2166秒起飞、37.2166秒结束；原2秒必达服务保留。绕行候选有实际增距和能耗，当前字典序稳定性比较优先选延误较小的绕行，不强行宣称等待最佳。
4. 选择等待候选，点击“对齐所选候选”。空间叠加、时间线、等待位置与整航段出发窗口均来自后端。展开“通行窗口、预约资源、保护误差和复核条件”。
5. 切换“全范围保守占用”，点击应用策略。环境版本增加，旧候选尝试采纳被拒绝；保守模式可绕行，但直达等待的有限图搜索找不到通路。`test_dynamic_planning.py`另验证期限缩短时必须绕行、持续禁入不得通行和时刻误差变化后重判。

限制：零误差不代表真实设备；当前有速度界时间管、保守复核余量及有限图，可能丢弃真实可用窗口，不保证SIPP原始最优性或完备性。`clock_s`是同一运动模型的程序相位误差，不是未知设施参数更新生效时刻的误差。

## B：材料/补证到达，旧候选失效

```bash
.venv/bin/chengkong serve --input examples/park_crane_synthetic.json \
  --db-dir runs/web-round4-BC --host 127.0.0.1 --port 8765
```

1. 选择“按期确认结束”，T=60记录一个候选ID。未来分支名称不作为当前证据。
2. “导入与字段核对”载入合成延期模板，保持`lifting_1 / 计划时段`，点击“查询上方材料并提出字段更新”。当前世界不变，先查看来源和字段校验。
3. 显式接受材料，已知计划区间变为[120,600]，未确认结束，设施保持安装。候选重算，环境版本增加。
4. 尝试采纳步骤1旧候选，必须拒绝；预览新复核候选，再显式采纳。计划和全部联合预约在同一事务更新。
5. 可切换“结束未确认”，T=360查询完成字段，无实际记录保持未知；已有`examples/imports/`契约可输入明确来源的合成完成记录。确认结束只撤销活动限制，不拆除塔身。不得为让规划通过而伪造补证。

首轮浏览器实际输出：`B-evidence-arrival-and-explicit-adoption.json`，包含证据审查、旧候选拒绝和新计划采纳。JSON材料上传不是外部大模型识别演示。

## C：多任务协同与在途维护

沿用BC服务，选择“延期后确认结束”：

1. 推进至T=120，勾选“冻结已执行前缀”，重新计算。
2. 任务从当前模拟位置、时间和余能维护。预览路径与代价，已经执行的前缀应逐点相等，服务要求不会删除。
3. 可在启动时指定`--model-dir /root/autodl-tmp/chengkong-models/round3`，再开启合成模型排序；查看实际精算顺序及范围外提示。所有低分任务仍需检查，学习不能批准候选。
4. 导出结果，查看输入/环境/计划/预约/规则/模型版本、失败诊断、代价、证据和适用条件。进入事件回放只读，不能切策略或把历史结果当当前计划采纳。
5. Ctrl+C停止后用相同命令重启，恢复活跃场景、T=120、计划预约、补证及待接受材料。备份恢复另见[RECOVERY](RECOVERY.md)。输入、代码或模型不匹配时明确拒绝，不降级复用。

同机连续任务和完整事件序列另在本轮独立协议评估中验证，不能用这套多机在途演示冒充同机能力已在界面逐步演示。所有执行均为软件模拟，无飞控连接。

## 自动实际浏览器与进程恢复

```bash
PLAYWRIGHT_BROWSERS_PATH=/root/autodl-tmp/chengkong-data/browser \
LD_LIBRARY_PATH=/root/autodl-tmp/chengkong-data/browser-libs/extracted/usr/lib/x86_64-linux-gnu \
FONTCONFIG_FILE=/root/autodl-tmp/chengkong-data/browser-libs/fonts.conf \
.venv/bin/python scripts/browser_round4.py --output runs/round4/browser-reproduction-001
.venv/bin/python scripts/demo_recovery.py --output runs/round4/recovery-reproduction-001
```

浏览器脚本使用随机loopback端口和临时库，最后关闭自启服务。恢复脚本的输出目录必须不存在，不能覆盖已有演示。SSH方式：自己电脑执行`ssh -N -L 8765:127.0.0.1:8765 -p <SSH端口> root@<服务器地址>`，访问`http://127.0.0.1:8765`。
