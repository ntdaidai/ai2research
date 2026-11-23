# Windows 本机使用

## 三步开始

1. 将 ZIP 完整解压到你自己的目录，例如 `D:\Research\ai2research`。不要在压缩包里直接双击脚本。
2. 双击 **`1_install.cmd`**，等待 Python 和依赖安装完成。首次需要联网，不需要管理员权限。
3. 双击 **`2_start.cmd`**，浏览器会打开 `http://127.0.0.1:8765/`。使用期间保留终端窗口，结束时按 Ctrl+C。

新安装没有旧课题。可以直接进入“辅助科研”，选择领域与起点，配置工具，生成自己的 Agent 任务文档。目录按调研、实现、产出浏览，包含111个工具与资源入口、9个领域。

## 让 Codex 使用科研工具

在 Codex 中打开解压后的 `ai2research` 目录，然后双击 **`3_connect_codex.cmd`**。它会复制项目技能，并登记本机 stdio MCP。完成后刷新 MCP 连接或重启 Codex。

技能安装使用复制方式，无需 Windows 开发者模式或软链接权限。已有同名且不同的 MCP 配置会保留并提示冲突，不覆盖其他工具。

也可以先预览配置：

```powershell
.\.venv\Scripts\python.exe tools\configure_harness_mcp.py --dry-run
.\.venv\Scripts\python.exe tools\install_local_skill.py --copy --dry-run
```

其他支持 MCP 的客户端可以使用 `.venv\Scripts\python.exe` 作为命令，参数为 `tools\harness_mcp.py` 的绝对路径，工作目录为解压目录，并设置 `PYTHONUTF8=1`。

## 检查与命令行

双击 **`4_check.cmd`**，检查依赖、页面、CLI 和真实 stdio MCP，报告位于 `.local\install-check.json`。检查使用临时状态，不运行实验、不连接 SSH、不调用付费模型。

```powershell
.\ai2research.cmd list_projects
.\ai2research.cmd doctor
.\ai2research.cmd start --domain "计算机视觉" --requirements "先调研并筛选低资源可验证的想法" --as-of 2026-11-23 --project my-study
```

`--as-of` 填你开展调研时的日期。复杂参数写入 UTF-8 JSON 文件再调用：

```powershell
.\ai2research.cmd project_overview "@arguments.json"
```

## 只使用辅助科研页面

如果暂时只需要工具目录和 Agent 文档，可以在 PowerShell 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\bootstrap_windows.ps1 -WorkbenchOnly
.\2_start.cmd
```

这只准备 Python 与前端，不安装科研后端依赖。需要 MCP 和完整检查时，重新双击 `1_install.cmd`。

## 实验、模型与文档工具

Windows 运行工作台、CLI、项目记录与 MCP；现有实验 worker 在 Linux 上运行。跑框架实验时，在 `harness.toml` 的 `[remote]` 配置自己的 Linux 服务器或 WSL SSH 服务：

```toml
[remote]
host = "YOUR_SERVER"
port = 22
user = "YOUR_USER"
allowed_home = "/home/YOUR_USER"
workspace = "/home/YOUR_USER/.research-harness"
python = "python3"
```

Windows 需有 `ssh.exe`，并已配置你自己的 SSH 登录方式。克隆和阅读代码时按需安装 Git；实验项目自己的 PyTorch、数据与基线环境由 Agent 根据课题准备。

外部评审默认不调用。需要时设置自己的 `[review]` 地址、模型与有界调用次数，密钥通过对应环境变量提供。例如在当前 PowerShell 会话设置 `RESEARCH_REVIEW_API_KEY`，再从同一会话启动需要它的进程；不要把密钥写入 TOML 或项目文件。另一个已经运行的 Codex 进程需要重新启动才能继承新环境变量。

PDF 编译、draw.io 导出、PDF 文本提取等能力，按需安装对应工具并配置可执行路径；这不影响先使用科研工具目录和任务文档。

## 常见情况

- **端口已占用**：运行 `2_start.cmd --port 8766`。
- **下载失败**：检查当前网络后重新运行安装脚本；已有环境会继续使用。官方 uv 安装说明：https://docs.astral.sh/uv/getting-started/installation/ 。
- **目录有中文或空格**：启动脚本使用带引号的绝对路径，并设置 Python UTF-8 模式。
- **更换安装目录**：先移动源码，重新安装环境并重新登记 MCP；不要把旧 `.venv` 当作可移动的 Python 环境。
- **移除程序**：退出页面服务后删除项目目录即可；若登记过 MCP 或技能，先删除本工具对应的 MCP 项或用技能安装器 `--restore` 恢复。

本轮已完成 Linux 上的隔离安装与运行检查、Windows x64依赖解析及跨平台路径/配置测试；原生 Windows 双击流程由接收者运行 `4_check.cmd` 生成实机回执。
