# Linux 上手说明

这是可迁移的源码包，包含最新前端、111 项工具与资源目录、CLI/MCP、科研工作流技能、模板和测试。解压后重新安装依赖；个人密钥、研究记录、数据集和模型权重由使用者自行配置。

## 1. 解压与安装

```bash
tar -xzf ai2research-linux-20261124.tar.gz
cd ai2research
uv --version
bash tools/bootstrap_linux.sh
```

安装需要联网，使用锁定的 Python 3.14.4 主环境和 Python 3.12.2 arXiv 环境。已有可用 uv 时可直接安装；uv 不在 PATH 中时，用 `AI2RESEARCH_UV=/你的路径/uv bash tools/bootstrap_linux.sh`。

如果尚未安装 uv，且系统提供 Python 3 与 venv，可在项目内安装：

```bash
python3 -m venv .local/installer
.local/installer/bin/python -m pip install uv==0.11.25
export AI2RESEARCH_UV="$PWD/.local/installer/bin/uv"
export UV_CACHE_DIR="$PWD/.local/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.local/python"
bash tools/bootstrap_linux.sh
```

无需管理员权限。若系统缺少 venv，可使用已有 uv 可执行文件；安装报错后修复具体问题并重跑即可。

## 2. 启动页面

```bash
.venv/bin/python tools/run_workbench.py
```

浏览器访问 `http://127.0.0.1:8765/`。保持终端开启，按 Ctrl+C 停止。

没有桌面的服务器可这样运行，并在自己的电脑建立 SSH 转发：

```bash
# 服务器上
.venv/bin/python tools/run_workbench.py --no-browser
# 自己电脑的另一个终端；替换账号与服务器地址
ssh -L 8765:127.0.0.1:8765 用户名@服务器地址
```

然后在自己电脑访问上述地址。端口被占用时用 `--port 8766`，同时调整转发端口。

## 3. 接入 Agent（可选）

使用 Codex 的同学可运行：

```bash
.venv/bin/python tools/install_local_skill.py
.venv/bin/python tools/configure_harness_mcp.py
```

这两步会将本项目技能和 MCP 登记到自己的 Codex 配置；完成后重启对应会话。其他兼容 MCP 的 Agent 可配置 stdio：命令为本项目 `.venv/bin/python` 的绝对路径，参数为 `tools/harness_mcp.py` 的绝对路径，工作目录为项目根目录。

网页可以选工具、填写 idea 和介入要求、下载 Agent 任务文档。实验与评审由 Agent 或 CLI/MCP 驱动。CLI 示例：

```bash
./ai2research list_projects
```

首次使用返回空项目列表是正常结果。

## 4. 安装自检

```bash
.venv/bin/python tools/verify_install.py --full
```

检查前端资源、空项目页面、核心依赖、CLI、真实 stdio MCP 工具及隔离状态读写，结果保存到 `.local/install-check.json`。自检不会提交科研实验、连接 SSH 或调用付费模型。

## 5. 配置自己的科研环境

- 修改 `harness.toml` 的服务器账号、目录和 GPU 范围；实验 worker 使用 Linux。若只查看页面、选配工具和生成任务文档，无需配置算力。
- 课题代码、数据和基线依赖按具体任务安装。
- 外部评审按自己的模型服务、密钥环境变量和额度配置；源码默认评审调用额度为 0。
- PDF 提取、LaTeX 编译、draw.io 导出和 Docling 解析按需要准备对应工具。
- 个人项目与产物放在自己的状态目录；迁移已有课题见 [MIGRATION.md](MIGRATION.md)。浏览器草稿需要先下载任务文档保存。

本包在 Linux x86_64 环境验证；它是联网安装的源码发行包，不包含 Python 运行时、依赖离线包、GPU 驱动或课题数据。
