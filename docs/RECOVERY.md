# 单实例恢复与备份

第四轮新增，适用于本地软件仿真。`SimulationApp` 的每个正常完成操作在返回前保存 SQLite checkpoint；正常 Ctrl+C 或 `server_close()` 刷盘并释放目录锁。相同 `--db-dir` 同时只允许一个服务实例，不是多实例协议。

## 恢复范围与身份

每个场景自己的 `*.sqlite3` 保存当前世界、模拟 `as_of_s`、已知事件、已采纳任务、预约、历史决策与候选。新增 checkpoint 保存未接受材料/Agent提议及审计、字段补证请求与查询账本、完成状态补证结果、规则/学习和在途模式、当前计算结果。`_runtime.sqlite3` 保存最后选择的场景与外部服务调用次数。外部调用在发出 HTTP 之前预扣次数，超时或进程失败后重启不重置预算；只保存配置指纹和计数，不保存密钥或端点原文。

恢复绑定以下身份：输入文件完整 SHA256、规则版本、Python运行实现文件指纹、模型manifest和其中全部权重校验值。输入文件可搬到其他路径，但内容必须完全相同；模型文件可搬到其他目录，但内容和版本必须匹配。模型从“已加载”变为缺失/损坏/未加载不会自动降级后继续使用旧候选。输入缺失、内容改变、代码/规则或模型变化均明确拒绝旧会话，要求恢复对应输入/运行版本/模型，或为不同实验指定新目录。它们不自动迁移已有会话。

旧round2/round3数据库没有恢复绑定，不能可信判断当时的输入身份。本轮不猜测或覆盖这些库；访问时报 `recovery_binding_missing`，可只读备份，继续旧实验请使用其对应历史代码。需要新功能则使用新 `--db-dir`。

`GET /api/scenarios` 的 `active_scenario` 让界面恢复最后选择的场景；`GET /api/state` 的 `recovery_status` 区分新会话、正常恢复和因版本不一致而丢弃结果缓存后重新计算。导出增加恢复身份与未接受工作流，历史回放仍只读。

world/采纳事务与checkpoint是两个事务：若异常退出恰好发生在world提交后、checkpoint提交前，重启发现revision或world不匹配后会丢弃结果缓存并重算，绝不复用旧结果。尚未成功返回的本地审查操作不保证恰好一次恢复；本轮保证正常停止/启动恢复，外部调用预算另有预扣保护。SQLite自身事务与采纳计划/预约原子更新保持不变。不宣称完整抗电源故障协议。

## 正常停止和恢复

```bash
cd /root/autodl-tmp/chengkong-zhihu
.venv/bin/chengkong serve --input examples/park_crane_synthetic.json \
  --db-dir runs/web-round4-demo --host 127.0.0.1 --port 8765
# 操作后 Ctrl+C 正常停止；相同输入、代码和模型再次执行同一条命令。
```

如果首次启动指定了 `--model-dir`，重启必须继续提供匹配模型；它不是可以在同一数据库里省略的可选项。只在项目内显式配置的多模态服务读取凭据，恢复过程不搜索其他目录。故障信息不输出密钥。

## 停服备份与恢复到新目录

服务停止后操作；备份工具也取得同一目录锁，活服务存在时拒绝备份，防止多个场景数据库在不同操作阶段被混合。每个文件使用 `sqlite3.Connection.backup`，不是直接复制可能仍有日志的数据库；生成SHA256、字节数及manifest，并执行SQLite完整性检查。

```bash
.venv/bin/python -m chengkong.recovery backup \
  --source runs/web-round4-demo --destination runs/backups/round4-demo-001
.venv/bin/python -m chengkong.recovery restore \
  --source runs/backups/round4-demo-001 --destination runs/web-round4-restored-001
.venv/bin/chengkong serve --input examples/park_crane_synthetic.json \
  --db-dir runs/web-round4-restored-001 --host 127.0.0.1 --port 8765
```

两个destination都必须不存在。恢复先验证所有文件校验值与完整性，不会覆盖现有演示库。备份包含SQLite与绑定信息；输入文件、模型和代码需单独保留其原件/本地提交。本工具不复制模型或环境。若首次使用模型，恢复启动同样追加原 `--model-dir`。

## 已执行检查

`tests/test_recovery.py` 在临时目录验证：采纳计划/预约与事件位置；字段与完成补证预算；未接受结构化材料与Agent提议；最后场景；历史回放/导出；旧候选失效；checkpoint故障回滚与提交后失配重算；同目录第二实例拒绝；缺/改输入和模型拒绝；停服备份、SHA256损坏拒绝与禁止覆盖；外部服务预扣预算与秘密不落库。

实际连同原Web/事务专项共26项通过，日志 `runs/round4/recovery-targeted.txt`。这部分验证正常单实例恢复；真实外部多模态服务仍未接入，外部失败预算用显式本地模拟适配器验证。

补充专项：输入声明的初始时刻（包括0秒）被实际采用，不再硬编码60秒。启动传入的 `initial_policy` 只用于新会话；恢复时保留已保存策略，显式切换仍走版本化策略API。该边界独立通过，日志 `runs/round4/recovery-initial-policy.txt`。

另已实际执行三个独立服务进程的正常SIGINT停启、15次HTTP操作及SQLite备份/恢复，包含完整 `visible_world`、历史、计划版本、未接受材料和预算的强相等断言；日志、前后导出和数据库位于 `runs/round4/recovery-process-verified/`。最初演示的导出世界字段断言写成了不存在的`world`，审查后改为直接索引`visible_world`、新增历史和计划版本断言，并用新目录重跑；旧演示保留供审计，不将其弱断言作为最终证据。

```bash
# destination必须是新的目录；脚本自动正常停止自己启动的三个本地服务。
.venv/bin/python scripts/demo_recovery.py --output runs/round4/recovery-reproduction-001
# 若要验证已配置本地模型身份，可另在新目录增加 --model-dir /root/autodl-tmp/chengkong-models/round3
```

独立审查追加修复：会话只在绑定与checkpoint完整验证成功后加入缓存。此前单独复制其他输入的场景SQLite时，第一次请求虽然拒绝，却可能留下未验证缓存；现用连续两次真实HTTP请求验证均拒绝，且缓存为空。

预约持久化同时补齐：初始化/环境替换写入完整 `check_joint_tasks.reservations`；显式采纳使用同一事务内强制复核已生成的完整预约，包含起降、命名地面等待区及同机周转/补能驻留。存储使用 `reservation-snapshot-v2`，保存联合检查状态与未检查原因，不能把原本存在冲突的计划预约解读为采纳通过。旧list读取明确标为`legacy-pad-only`，不冒称完整。必要的同机模型缺失时，完整性标记为false。

追加9项专项实际通过（`runs/round4/recovery-audit-fixes.txt`），覆盖连续请求拒绝、等待时段95→100秒、同机驻留开始249→254秒、初始化/采纳/环境替换/重开数据库、注入事务失败全部回滚，以及既有版本和并发写入事务。

上述审查修复后的完整进程级演示已再次在新目录实际通过，最终证据为 `runs/round4/recovery-process-final/`（3进程、15次HTTP、所有服务正常退出）；原目录保留不覆盖。
