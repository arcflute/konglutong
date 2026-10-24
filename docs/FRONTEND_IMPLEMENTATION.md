# 空路通桌面 v1.1 实施记录

起点：66cb3e6，main；仅用户提供的设计规格未跟踪。完整读取 v1.1；没有最终 3.2 原稿（source 仅 README）。保留三层 Python / CLI / SQLite 算法与历史实验；本轮不训练模型。

采用 frontend/ React + TypeScript + Vite；Python 同源提供 dist，history 路由回退仅适用于页面。16 页拆分共享组件和角色模块。沿用单实例锁和恢复绑定，新增角色持久化 SQLite；本地演示身份必须显式启用。三端桌面主视口 1920×1080 / 1600×900 / 1366×768，不做整页缩放。

工具链：数据盘本地 Node 22.18.0，官方 SHA256 c1bfeecf1d7404fa74728f9db72e697decbd8119ccc6f5a294d795756dfcfca7；不改全局。依赖版本锁在 package-lock。浏览器使用现有 Python Playwright。磁盘实际 /dev/nvme0n1，约49GiB可用。

基线实跑215项，214通过、恢复候选列表顺序1失败（22.328s）。原因：checkpoint JSON sort_keys 重排候选字典，恢复后 values 顺序不一致；修复显式保存 candidate_order，不削弱原断言。原始日志 runs/frontend-v1/baseline-tests.txt。

Cesium 使用本地 Assets/Workers/Widgets，无 ion、影像或在线地形；Viewer(baseLayer:false, EllipsoidTerrainProvider)，已查询官方 Viewer / terrain 文档，最终以安装版本源码/类型复核。局部米制 ENU 映射到明确虚构锚点，不改求解器；未知几何仍未知。WebGL失败提供同数据二维图与列表。

实施顺序：共享服务/组件→企业六页→政府五页与个人四页→门户与视觉→端到端、权限和必要回归。尚未完成部分不作完成声明。
