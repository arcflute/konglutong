# 空路通桌面 v1.1 验收记录

本次验收使用真实Python服务、SQLite与本地Chromium，数据全部是有标识的合成园区。当前工程起点 `66cb3e6`；不是对旧交付报告的直接沿用。原始日志、数据库、失败记录、截图和导出在独立 `runs/frontend-v1/`，代码/小型交付清单进入Git。以下只记录实际执行。

## 16页完成情况

每页均有实际布局、数据加载/错误/空态，共享身份和场景上下文；业务写入以服务器结果为准。页面刷新与直接深层路由均通过浏览器检查。接口和权限详见[API表](FRONTEND_API_MAP.md)。

| 页 | 已完成内容与主要操作 | 实际接入/验证 |
|---|---|---|
| P00 统一门户 | 三端入口、产品场景与边界、本地身份选择、返回导航 | session/demo实际登录；独立1920截图；概念插图明确非统计 |
| G01 区域态势 | 深蓝多面板、授权变化统计、共享航路/设施、重点事件和核查入口 | context真实聚合；三档桌面截图、地图定位与预览 |
| G02 城市变化台账 | 对象/时间/类型筛选、来源联动、核查状态备注、已批准摘要撤回/恢复 | 备注持久化且不改事实；公开撤回专项实际UI操作 |
| G03 影响研判与协同核查 | 共享任务影响、冲突依据抽屉、核查记录和运营方共享版本 | task_1/task_2白名单；gov不能调用采纳或事实更新 |
| G04 历史追溯 | 当时知识快照、事件/到达时间线、两版本叠加与返回当前 | replay只读；后续信息不写入过去；新时点保护 |
| G05 专题报告 | 参数/事件范围、授权检查统计、固定快照报告与下载/打印 | report保存、按本人范围读取；HTML转义及JSON快照一致 |
| B01 运营总览 | 浅色工作台、实际任务/问题/候选、空间态势、下一步动作 | context实际数量；采纳后新计划和预约版本同步 |
| B02 变化与证据 | 材料上传/粘贴、模板、逐字段来源/错误/裁决、补证、接受与重算 | 真实导入→第一层校验→接受；无证据unknown；完成不拆除 |
| B03 任务与资源 | 任务/同机筛选、服务点/时间窗/能耗、资源预约、轨迹高度剖面 | 后端任务和完整预约；新增任务走原子导入，原核心回归保留 |
| B04 影响分析 | 位置/时段/原因/证据、检查范围、在途/学习实验与实际对照 | 真实analyze/compare；冻结前缀及预算/失败语义保留 |
| B05 航路维护 | 原计划/候选差异、代价、独立复核、条件、策略切换、明确采纳 | 旧候选按钮禁用及直接POST拒绝；确认弹窗1366可达；事务更新 |
| B06 回放与报告 | 版本回放、运动/占用/等待轨道、完整导出、报告预览/JSON/打印 | 历史不改当前；实际下载enterprise-report.json/pdf |
| C01 区域探索 | 暖白靛紫桌面导航、宽屏公开地图、摘要/来源/关注 | 无企业任务和成本；三档桌面及390/360次级兼容 |
| C02 航前环境提示 | 区域与查询时段、覆盖/缺口、公开记录和保存 | 服务器查询并持久化；范围外无覆盖不显示可飞 |
| C03 关注与提醒 | 关注/取消、公开更新通知、已读、详情状态 | 同用户/事件/公开版本去重；撤回红线；重启保留 |
| C04 记录与反馈 | 本人查询、反馈表单、持久ID与待审状态、导出 | 重复提交去重、刷新/重启保留、用户隔离；不改环境 |

没有菜单页仅显示“即将上线”。演示身份、材料和模型状态均由后端提供；页面没有随机数图表、硬编码冲突/成本/统计或假采纳。

## 实际执行与证据

| 检查 | 实际结果 | 证据（相对 runs/frontend-v1） |
|---|---|---|
| 起点基线 | 215项，214通过、1个真实恢复顺序失败，22.328s | baseline-tests.txt |
| 恢复顺序专项 | 9项通过；显式candidate_order，无削弱断言 | recovery-fix.txt |
| 角色/权限/公开/事务/恢复专项 | 最后12项通过，3.728s | publication-targeted.txt |
| 第一次集成全回归 | 226项通过，25.805s | regression-final.txt |
| 撤回修复后最终全回归 | **227项通过，26.682s，无skip** | regression-release.txt |
| TypeScript与生产构建 | 通过；Cesium大块警告保留 | build-release.txt |
| 三端完整浏览器 | 16路由、43截图、35次布局检查，180次页面API请求记录，0页面脚本异常 | browser-verified/report.json |
| 迟到响应/撤回/键盘与个人终版 | 6条边界流程、9截图、3次最新个人首页布局检查，0脚本异常；含明确模拟提议接受与旧模型真实规则/学习对照 | browser-edges-verified/report.json |
| 三进程停启与独立备份恢复 | 18次HTTP（含3次显式登录），全部强断言通过 | recovery-process/result.json |
| CLI实际动态输入 | 真实生成1个候选 | cli-motion-release.json |
| 历史数据/模型保留 | 186文件SHA256全部一致 | historical-artifacts-check.json |
| 编译、依赖与差异检查 | compileall、pip check、git diff --check通过 | release-checks.json |

不把重复执行相加为测试总数。浏览器错误注入故意制造一次503，因此console有1条相应HTTP错误；正常业务无脚本异常，外部请求0。WebGL不可用专项通过禁用canvas的WebGL上下文触发真实二维回退，不用固定数据代替规划。迟到响应专项只延迟真实HTTP返回值，不替换成功响应。

首次浏览器尝试的 `browser-first/` 保留：主要闭环已经通过，末尾测试脚本的JS括号笔误导致未完成，修正脚本后重新运行 `browser-verified/`。早期Cesium太阳对象可空造成初始化失败，已按实际API加空值保护。候选UI、图例、任务不变部分、地图标签高度、错误措辞与弹窗焦点经过实际检查后修正，后续专项补齐撤回与迟到响应，不隐藏失败日志。补充提议验收的第一次断言把两条同有效/到达时间的记录误当成“后上传覆盖”；实际事件按既有确定顺序重放，当前计划仍600秒。原失败`browser-edges-release/`保留，改为t+61发生的新事件后650秒断言通过，未修改求解器或削弱数值断言。同刻来源有分歧时应走结构化材料的显式冲突裁决，不把接收审计当成提取值已覆盖当前值。

## 视口与视觉检查

Chromium **140.0.7339.16**，Playwright1.55，真实headless浏览器、SwiftShader软件WebGL。标准检查DPR=1、默认浏览器缩放100%、`visualViewport.scale=1`、body computed zoom=1。报告保存innerWidth/innerHeight、document.scrollWidth、地图矩形及canvas数；三档首页均为3D，单页canvas数量最多1，退出销毁。

| 首页 | 1920×1080 | 1600×900 | 1366×768 |
|---|---|---|---|
| 政府 | [截图](../runs/frontend-v1/browser-verified/gov-1920x1080.png) | [截图](../runs/frontend-v1/browser-verified/gov-1600x900.png) | [截图](../runs/frontend-v1/browser-verified/gov-1366x768.png) |
| 企业 | [截图](../runs/frontend-v1/browser-verified/biz-1920x1080.png) | [截图](../runs/frontend-v1/browser-verified/biz-1600x900.png) | [截图](../runs/frontend-v1/browser-verified/biz-1366x768.png) |
| 个人终版 | [截图](../runs/frontend-v1/browser-edges-verified/personal-final-1920x1080.png) | [截图](../runs/frontend-v1/browser-edges-verified/personal-final-1600x900.png) | [截图](../runs/frontend-v1/browser-edges-verified/personal-final-1366x768.png) |

[门户](../runs/frontend-v1/browser-verified/portal-1920-full.png)、[采纳弹窗](../runs/frontend-v1/browser-verified/A-adoption-dialog-1366.png)、[冲突依据抽屉](../runs/frontend-v1/browser-verified/conflict-evidence-drawer.png)、[候选详情全页](../runs/frontend-v1/browser-verified/biz-maintenance-page.png)、[WebGL回退](../runs/frontend-v1/browser-verified/webgl-unavailable-fallback.png)、[撤回后提醒](../runs/frontend-v1/browser-edges-verified/revoked-inbox-hides-details.png)均有证据。

额外实际测试1440×900、1024×768、1920×947内容视口，以及个人390×844/360×800。1920×947是普通窗口扣减工具栏高度的内容区域模拟，**不是在有实体1920显示器的桌面Chrome上测量工具栏**。没有全页横向溢出；长表格局部横向滚动、长页面纵向滚动。抽屉/弹窗限制最大高度并内部滚动，采纳按钮可见/可滚动到达。弹窗Shift+Tab、Escape及焦点回到触发控件专项检查通过。

登录到本地3D可用实测：企业2.790s、政府1.425s、个人1.179s；同浏览器缓存可能存在，属于一次本地验收记录，不是稳定FPS或冷启动基准。未实测其他浏览器、实体GPU、OS125%缩放/高DPI、读屏器完整可访问性或大型园区性能；不能用本报告代替这些条件的验收。

## 可复制检查

```bash
.venv/bin/python -m unittest discover -s tests -v
export PLAYWRIGHT_BROWSERS_PATH=/root/autodl-tmp/chengkong-data/browser
export LD_LIBRARY_PATH=/root/autodl-tmp/chengkong-data/browser-libs/extracted/usr/lib/x86_64-linux-gnu
export FONTCONFIG_FILE=/root/autodl-tmp/chengkong-data/browser-libs/fonts.conf
.venv/bin/python scripts/browser_frontend.py --output runs/frontend-v1/browser-reproduction-001
.venv/bin/python scripts/browser_frontend_edges.py --output runs/frontend-v1/browser-edges-reproduction-001
.venv/bin/python scripts/demo_recovery.py --output runs/frontend-v1/recovery-reproduction-001 \
  --model-dir /root/autodl-tmp/chengkong-models/round3
```

使用新输出目录以保留旧证据。两个浏览器脚本自启独立本地HTTP/SQLite并关闭，不能拿旧报告目录覆盖重跑。round3/round4旧浏览器脚本属于其历史UI版本，不再作为当前三端入口。

## 未接入与所需条件

本轮可独立完成的16页与最小服务已交付。真实多模态调用、真实园区地图/测量输入、生产身份服务与第三方数据均未接入；需要项目范围明确的服务凭据/预算、身份组织权限映射、许可和基准完整的真实材料。模型保持原合成实验结果，未因此获得新的现实有效性证据。现场飞控、法定审批、外部消息、多实例和复杂设备动力学不在本轮实施范围，原研究路线没有删减。
