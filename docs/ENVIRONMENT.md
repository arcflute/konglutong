# 实际环境记录

核验日期：2026-10-23（Asia/Shanghai）。本文件仅记录实际执行所得，不把讨论中的服务器配置视为事实。

| 项目 | 实际结果 |
|---|---|
| 项目实际路径 | `/root/autodl-tmp/chengkong-zhihu`，无重复同名嵌套目录 |
| 系统 | Ubuntu 22.04.1 LTS，Linux 5.15.0-136-generic，x86_64 |
| 数据盘 | `findmnt -T` 与 `df -hT`：`/root/autodl-tmp` 位于 `/dev/nvme1n1` 的 XFS 数据卷，rw；50G，初始化时可用50G，使用248K |
| inode | `df -ih`：716M总量，693M可用；不是inode耗尽 |
| 宿主可见CPU/内存 | Xeon Gold 6330；112逻辑CPU；629Gi总内存、591Gi可用（检查时刻） |
| 容器实际配额 | cgroup `cpu.max=1400000 100000`，相当于14核CPU配额；`memory.max=96636764160`，90GiB内存上限；不能按宿主全部资源安排训练 |
| GPU | NVIDIA GeForce RTX 3090，24576MiB；检查时仅1MiB占用，无运行GPU进程 |
| 驱动与CUDA | nvidia-smi：595.71.05，驱动支持CUDA 13.2；nvcc实际工具包11.8（V11.8.89），二者含义不同；未执行GPU计算验证 |
| Python | `/root/miniconda3/bin/python3`，3.10.8；原Conda环境保持不变 |
| 工具 | Git 2.34.1，Conda 22.11.1；Node/npm、uv、rg不可用；未为本轮安装这些无关工具，文本检索用现有工具 |
| 网络 | Python urllib访问 `https://pypi.org/simple/pip/` 返回HTTP 200；项目venv按已有pip索引下载wheel成功 |
| 独立环境 | `/root/autodl-tmp/chengkong-zhihu/.venv`；`sys.prefix`为该目录、`sys.base_prefix=/root/miniconda3` |
| 已核验项目依赖 | Python标准库运行时；pip22.2.2、setuptools63.2.0、wheel0.45.1，editable安装chengkong-zhihu0.1.0 |
| 版本管理 | 原目录不是Git仓库，已执行 `git init -b main`；不设置远端、不推送 |

执行过的主要核验：`df -hT`、`df -ih`、`findmnt -T`、`uname -a`、`lscpu`、`free -h`、cgroup配额读取、`python3 --version`、`git --version`、`conda --version`、`nvidia-smi`、`nvcc --version`、PyPI HTTP探测，以及独立环境的pip版本/包清单。

一次安装失败及处理：`.venv/bin/python -m pip install --no-build-isolation --no-deps -e .` 返回 `invalid command 'bdist_wheel'`，原因是新venv只有pip/setuptools，没有wheel。随后仅在该venv执行 `pip install --no-cache-dir wheel==0.45.1`（下载72.5kB），再执行相同editable安装命令成功。没有更换系统Python、驱动、全局CUDA或升级全局软件。

仓库与venv均位于已核验数据盘。合成运行产物在 `runs/`，被Git忽略；后续大数据与权重仍按 `.env.example` 建议映射到 `/root/autodl-tmp/chengkong-data/`、`/root/autodl-tmp/chengkong-models/`，本轮未创建空权重目录或下载模型。CLI只读取进程环境变量 `CHENGKONG_RUN_DIR` 作为演示默认输出位置，不自动读取 `.env`，VLM配置不会被解释为已有可用适配器。
