# 空路通桌面 v1.1 实施记录

实际起点为 main / `66cb3e6`，仅用户提供的设计规格未跟踪；没有回退。完整读取 v1.1 和项目依据，`docs/source/`仍只有 README，没有最终3.2。保留 Python 三层计算、CLI、SQLite、原模型和实验；本轮没有训练、模型下载、全局环境改动或远端推送。

## 工程与技术选择

| 位置 | 实际职责 |
|---|---|
| `frontend/src/app`、`api` | 16条桌面路由、身份/场景上下文、同源请求、取消请求与过期响应保护 |
| `frontend/src/portals/{biz,gov,personal}`、`Portal.tsx` | 企业六页、政府五页、个人四页及统一门户 |
| `frontend/src/shared`、`features` | 主题组件、Cesium/二维、时间预览、导入审查、影响详情、回放及报告 |
| `frontend/src/styles/theme.css`、`layout.css` | 原规格三套 token；Grid/Flex、局部滚动与导航折叠，未使用全页缩放 |
| `src/chengkong/portal.py`、`configs/portal-demo.json` | 会话、角色/组织范围、共享/公开白名单、核查、关注/提醒/记录/报告、公开撤回 |
| `src/chengkong/web.py`、`presentation.py` | 复用真实计算接口，SPA/本地资源服务，后端同版本几何和冻结前缀投影 |
| `scripts/browser_frontend*.py`、`tests/test_portal.py` | 实际浏览器全流程及迟到响应/撤回专项，HTTP权限和事务边界 |

React 19.3.1 / TypeScript 7.0.2 / Vite 8.3.5 / Cesium 1.146.0 / lucide-react 1.61.0，准确依赖由 package-lock 固定。旧单页脚本已从当前服务移除，历史代码仍在 Git；不维护两套互相漂移的业务界面。原 Python 包名、CLI命令和算法契约保持 `chengkong`。

本服务器系统没有 Node，使用数据盘独立 Node 22.18.0 / npm 10.9.3；官方发行包SHA256：`c1bfeecf1d7404fa74728f9db72e697decbd8119ccc6f5a294d795756dfcfca7`。不覆盖系统工具链。复用已有 `.venv`、Playwright、Chromium与字体/动态库。数据盘实际 `/dev/nvme0n1` 50G，目前约48G可用。

## 构建、运行与访问

在仓库根执行，首次构建或依赖变更时安装锁定依赖：

```bash
cd /root/autodl-tmp/chengkong-zhihu
export PATH=/root/autodl-tmp/chengkong-data/frontend-toolchain/node-v22.18.0-linux-x64/bin:$PATH
npm --prefix frontend ci --no-fund --no-audit
npm --prefix frontend run build
.venv/bin/chengkong serve --input examples/frontend-v1/motion.json \
  --db-dir runs/frontend-v1/live-motion-001 --host 127.0.0.1 --port 8765 \
  --demo-identities --action-policy keep_detour \
  --model-dir /root/autodl-tmp/chengkong-models/round3
```

其他电脑：使用符合本机环境的 Node 22.18+，无需上述服务器绝对路径；Python依赖安装沿用 `scripts/bootstrap.sh`，学习是可选依赖。规则演示可以省略 `--model-dir`，界面明确“未加载”，但同一已绑定数据库重启不能擅自改变模型身份。

在本机建立 `ssh -N -L 8765:127.0.0.1:8765 -p <SSH端口> <用户>@<服务器>`，访问 `http://127.0.0.1:8765/`。Python同源提供 `frontend/dist` 和API，不需另启Vite；启动后不依赖Node进程。深层路由刷新返回index；API错误保持JSON/HTTP状态，绝不回退成页面。dist缺失时明确503并提示构建。本轮按仓库检出部署，未将SPA与演示配置打包成独立Python wheel分发。

`--demo-identities` 显式打开本地预置身份，界面可选择运营员、研判员、两个独立个人用户和空范围机构。未加开关时身份服务未配置，私有接口拒绝，旧演示会话清除；不是生产登录系统。监听仍为127.0.0.1，不提供公网部署。

## 数据、空间与权限

- Cesium Assets/Workers/Widgets随build复制到dist；`baseLayer:false`、椭球地形，无ion令牌/在线影像/城市模型。外部服务故障不阻断本地园区。几何从后端取，前端只画图，未复制冲突或规划算法。
- 坐标为局部ENU米制、相对地面z=0，虚构经纬锚点(0,0,0)仅用于显示，不是园区真实位置或坐标转换。塔身、臂、索、吊物、活动保护和路径来自同版本；活动保护随后端运动姿态变化。未来预览不接收未来事件，也不更新知识时钟。
- 路径、候选变更、已执行前缀和等待位置都有独立图例。个人不接收航路、成本、补证材料或原始来源；公共几何按已批准实体ID白名单重建。WebGL失败回退同后端数据二维与列表。
- 会话是12小时不透明令牌，服务端SQLite只保存哈希；HttpOnly/SameSite=Strict、POST专用头及同源/Host检查。身份、组织和权限从服务端配置解析，URL/body中的role和org不赋权。
- 政府仅授权园区与共享task_1/task_2，无企业采纳接口权限；核查备注不修改世界。允许撤下/恢复预先批准的合成活动摘要，不能发布任意企业新对象；撤回有审计，旧提醒与保存查询读取时重新过滤。独立获准公开的设施实体仍显示。
- 企业每次采纳提交候选与依据版本；后端检查客户端依据，再由原PlanStore事务重核环境/计划/预约/规则/模型及独立复核，原子写计划与预约。成功后统一重读上下文；失败不会“前端先成功”。
- 场景、身份、回放时点及运动预览切换都有取消/序列保护；90秒无响应时显示“请求未得到确认，刷新核对”，不推测服务器是否已提交。

## 恢复、备份与迁移边界

新 `_portal.sqlite3` 保存会话哈希、核查、公开版本/可见控制及审计、个人记录和报告；使用CREATE IF NOT EXISTS进行可加式建表。它绑定权限配置摘要，配置改变拒绝复用，防止范围变化后仍沿用旧权限。与计算库一样使用单实例目录。

既有world/计划/预约/checkpoint及 `_runtime.sqlite3` 保留。修复了起点真实失败：JSON sort_keys改变候选字典次序，现显式保存并校验 candidate_order；没有削弱原恢复断言。

正常Ctrl+C后以相同输入、Python代码、模型、权限配置和db-dir重启。旧代码数据库不会被新代码自动迁移；保留原目录，用新目录开始。备份工具会包含 `_portal.sqlite3`，但不会复制代码、输入、模型和构建资源：

```bash
# 先正常停止，两个destination都必须不存在
.venv/bin/python -m chengkong.recovery backup \
  --source runs/frontend-v1/live-motion-001 --destination runs/frontend-v1/backup-001
.venv/bin/python -m chengkong.recovery restore \
  --source runs/frontend-v1/backup-001 --destination runs/frontend-v1/restored-001
# 用上面的同一启动命令，db-dir改为restored-001
```

详细恢复语义见[RECOVERY](RECOVERY.md)。门户会话备份虽不含明文密钥，仍含用户记录，应按项目数据保管。没有新增多实例或自动迁移协议。

## 实际检查与限制

[验收记录](FRONTEND_ACCEPTANCE.md)给出逐页状态、截图、测试与未验证条件；[演示步骤](FRONTEND_DEMO.md)给出三端闭环。[API映射](FRONTEND_API_MAP.md)列出接口及范围。

Cesium懒加载块约4.93MB（gzip1.36MB），整个dist约13MB，Vite大小警告仍保留。实测本地软件WebGL正常，但没有低端机器FPS/跨GPU或跨浏览器性能承诺。浏览器资源本地化后无外部地图请求；真实多模态服务、真实园区和历史施工学习仍未接入。本轮不改变既有学习结果或研究边界。
