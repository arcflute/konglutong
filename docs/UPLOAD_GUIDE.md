# 上传到 AutoDL 与开始使用

## 路径

项目文件：`/root/autodl-tmp/chengkong-zhihu/`。

后续大数据：`/root/autodl-tmp/chengkong-data/`；后续模型：`/root/autodl-tmp/chengkong-models/`。现在只传启动包，不需要下载模型。

先在服务器执行 `df -h /root/autodl-tmp` 检查实际挂载和可用空间。如路径不存在，先核实平台的数据盘路径，不要直接在系统盘创建同名目录假装数据盘。

## 方式一：压缩包上传

以下按最近提供的连接 `ssh -p 27457 root@connect.nmb2.seetacloud.com` 编写。如果本项目使用另一实例，替换主机和端口；下载目录不同也替换本地路径。

1. 在 Windows 将压缩包保存到 `D:\Download\chengkong-zhihu-starter.zip`。
2. 在 Windows PowerShell 执行（`scp` 的端口参数是大写 `-P`）：

```powershell
scp -P 27457 "D:\Download\chengkong-zhihu-starter.zip" root@connect.nmb2.seetacloud.com:/root/autodl-tmp/
```

3. 登录服务器：

```powershell
ssh -p 27457 root@connect.nmb2.seetacloud.com
```

4. 在服务器终端解压。下面的命令会拒绝覆盖已经存在的同名项目目录：

```bash
cd /root/autodl-tmp
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile
target = Path('/root/autodl-tmp/chengkong-zhihu')
if target.exists():
    raise SystemExit('目标目录已存在；请检查原工程后逐项合并，不要直接覆盖。')
with ZipFile('/root/autodl-tmp/chengkong-zhihu-starter.zip') as archive:
    for member in archive.infolist():
        path = Path(member.filename)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'chengkong-zhihu':
            raise SystemExit('压缩包路径不符合预期，停止解压。')
    archive.extractall('/root/autodl-tmp')
print('已解压到', target)
PY
```

5. 在 VSCode 连接该服务器，打开文件夹 `/root/autodl-tmp/chengkong-zhihu/`。将 `docs/CODEX_FIRST_TASK.md` 全文发给此目录中的 Codex。

## 方式二：VSCode 文件栏上传

在本地解压压缩包，得到 `chengkong-zhihu` 文件夹。VSCode Remote SSH 打开服务器的 `/root/autodl-tmp/`，把该文件夹拖进远程文件栏。注意同步上传 `.gitignore` 与 `.env.example`。目标目录若已存在，先检查内容再合并。

## 最终原稿

如有你手里已改好的 3.2，放入项目的 `docs/source/`。推荐文件名 `SECTION_3_2_FINAL.md`；Word 原件也可以，需注明最终版。当前启动包的技术路线是工程整理版，不是对该原稿的逐字复制。
