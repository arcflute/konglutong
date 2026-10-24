# 空路通新 LOGO 接入与验收

本次从 `1cd35a9` 的实际工作区继续；原有未跟踪文件为用户提供的 `docs/LOGO.png`，原件保留并纳入提交。没有修改后端算法、权限、模型、实验数据或既有数据库。

## 素材与实现

- 原件为 1364×1065、8 位 RGB PNG，776,547 字节，无透明通道。包含蓝/青渐变图形及近白背景，无文字；现有“空路通”及三端名称继续显示。
- 原件 SHA256：`61d10321154d4d5949ea7ca160bb43b387cdc7dab85032593af75901fec06636`。`frontend/src/assets/brand-logo-source.png` 是逐字节相同的构建资源，不替换或修改原件。
- 统一由 `frontend/src/shared/BrandLogo.tsx` 和 `BrandLogo.css` 渲染。共享应用页头覆盖政府5页、企业6页、个人4页；门户页头与页脚也使用同一组件。原品牌返回入口保留。
- 原图不能直接作为透明蒙版。组件通过 SVG 的 sRGB 颜色矩阵生成轮廓透明度，剔除近白及中性背景；第二层蒙版读取原图蓝/青色差，将原有局部渐变映射到主题颜色。没有反色、矩形底色冒充换色、重画轮廓或新增图形。
- 轮廓 alpha 为 `clamp((245-R)/235) × clamp((B-R-3)/8)`，其中 R/B 是原图字节值；色调层为 `clamp(1.7×(G-R)/255-0.12)`。这些参数针对本次已核对素材，不是任意照片的通用去底算法。将来更换原图必须重新检查。
- 默认/企业/个人使用现有 `--ui-primary-hover` 和 `--ui-primary` 派生的专用变量 `--brand-logo-ink/highlight`；政府使用亮蓝 `--ui-primary` 与 `--ui-selected-fg`。图形本身分别呈青蓝、亮蓝、靛紫色，保留渐变层次。
- 原始完整 viewBox、留白及比例保留；桌面宽60px、高约46.84px，小屏宽44px。SVG内在尺寸和CSS比例在资源解码前预留空间。多实例使用独立蒙版ID，避免门户页头/页脚冲突。
- Vite将PNG输出为带内容哈希的 `/assets/brand-logo-source-*.png`，生产服务、深层路由及刷新均使用相同资源。未开放 docs 目录；实际请求 `/docs/LOGO.png` 返回404。
- 旧 `.brand-symbol` 样式及品牌用 Route 图标引用已清除；门户功能卡仍使用的 Route 图标属于业务图标，继续保留。

## 实际检查

| 检查 | 实际结果 / 证据 |
|---|---|
| 类型与生产构建 | `tsc`、Vite及Cesium复制成功；`runs/logo-v1/build.txt`。既有Cesium约4.93MB块大小提示仍存在 |
| 浏览器环境 | Chromium 140.0.7339.16 / Playwright 1.55，headless SwiftShader，DPR 1，默认100%浏览器缩放 |
| 视口 | 1920×1080、1600×900、1366×768 CSS像素；没有全页transform/zoom |
| 页面及状态 | 全部16路由，三端身份切换、直接访问、刷新、品牌返回门户；政府/企业展开和折叠，个人完整桌面横向导航。共138次布局状态检查通过 |
| 资源与布局 | PNG解码尺寸、比例、页头边界、加载前后位置、页面横向溢出、唯一蒙版ID检查通过；所有记录的LOGO资源请求200，0页面脚本异常、0console错误 |
| 透明度与主题 | 四种页面主题实际SVG渲染像素检查：1,077,711个近白背景像素无漏底；346,926个高饱和核心像素全部保留。与明确alpha公式的最大差异约0.495/255；多种不透明颜色确认渐变层次仍存在 |
| 原件与发布资源 | 原件、前端源资源、HTTP返回资源SHA256一致；docs访问404 |
| 原HTTP静态服务检查 | `test_static_ui_and_state_use_live_backend_without_optional_models` 通过；`runs/logo-v1/http-static-check.txt` |
| 证据位置 | `runs/logo-v1/source-inspection.json`、`browser-first/report.json`、`details/report.json`；截图保留在对应目录 |

首轮门户页脚截图因平滑滚动尚未结束，没有拍到页脚，保留原记录。随后改用真实页脚元素截图，补拍结果为 `runs/logo-v1/details/portal-footer.png`；三端另保存了实际品牌区域近景。重现脚本也采用元素截图，避免同样问题。没有为此重复整套业务实验。

主要截图：

- 政府：[1920×1080](../runs/logo-v1/browser-first/gov-1920x1080.png)、[1600×900](../runs/logo-v1/browser-first/gov-1600x900.png)、[1366×768](../runs/logo-v1/browser-first/gov-1366x768.png)。
- 企业：[1920×1080](../runs/logo-v1/browser-first/biz-1920x1080.png)、[1600×900](../runs/logo-v1/browser-first/biz-1600x900.png)、[1366×768](../runs/logo-v1/browser-first/biz-1366x768.png)。
- 个人：[1920×1080](../runs/logo-v1/browser-first/personal-1920x1080.png)、[1600×900](../runs/logo-v1/browser-first/personal-1600x900.png)、[1366×768](../runs/logo-v1/browser-first/personal-1366x768.png)。
- 门户：[页头](../runs/logo-v1/browser-first/portal-1920x1080.png)、[页脚补拍](../runs/logo-v1/details/portal-footer.png)。三端近景为 `details/gov-brand.png`、`biz-brand.png`、`personal-brand.png`。

验收范围是本次品牌替换；未重跑此前227项全量算法/业务回归，也未把旧结果计为本轮执行。未验证Safari、Firefox、实体显示器OS缩放或其它DPR下的视觉效果。浏览器检查使用真实本地后端、生产前端及合成园区，无模拟成功API、外部地图或新模型训练。

## 构建、启动与复现

已有依赖无需重新安装。项目根目录执行：

```bash
export PATH=/root/autodl-tmp/chengkong-data/frontend-toolchain/node-v22.18.0-linux-x64/bin:$PATH
npm --prefix frontend run build

.venv/bin/chengkong serve --input examples/frontend-v1/motion.json \
  --db-dir runs/logo-v1/live-demo-001 --host 127.0.0.1 --port 8765 \
  --demo-identities --action-policy keep_detour
```

通过既有SSH转发访问 `http://127.0.0.1:8765/`，依次进入三端查看品牌。新演示目录避免影响旧库；同一代码和输入的正常重启继续用相同目录。没有修改启动参数或远端部署。

```bash
PLAYWRIGHT_BROWSERS_PATH=/root/autodl-tmp/chengkong-data/browser \
LD_LIBRARY_PATH=/root/autodl-tmp/chengkong-data/browser-libs/extracted/usr/lib/x86_64-linux-gnu \
FONTCONFIG_FILE=/root/autodl-tmp/chengkong-data/browser-libs/fonts.conf \
.venv/bin/python scripts/browser_logo.py --output runs/logo-v1/browser-reproduction

PYTHONPATH=tests .venv/bin/python -m unittest \
  test_web_api.WebAPITests.test_static_ui_and_state_use_live_backend_without_optional_models -v
```

浏览器脚本要求新的输出目录，自动启动并关闭loopback临时服务；已有截图与数据库不会被覆盖。构建产物与运行证据留本地，原始素材、前端源资源、代码及本文档纳入Git。本次只做本地提交。
