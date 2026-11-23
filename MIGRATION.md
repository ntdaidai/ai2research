# 迁移与分发

## 给同学使用

发送 `ai2research-windows-20261123.zip` 和 `WINDOWS_QUICKSTART.md`。对方解压后按“安装、启动、接入 Codex、自检”四个入口操作。每位同学使用自己的目录、状态库、凭据与实验账号。

迁移版包含完整程序源码、最新六页前端、111项工具资源目录、CLI/MCP、研究工作流技能、安装/检查脚本、依赖锁定文件、模板、测试与公开空白目录演示。

源码包不携带 `.venv`、`.local`、个人 Agent 配置、密钥、研究数据库、数据集、权重或私人论文。工具目录提供来源和选择入口，实际调用按自己的 Agent、账户和执行环境配置。

## 自己更换电脑

程序与个人状态分开迁移：

1. 在新电脑解压干净源码，重新安装环境。
2. 如果需要接续自己的旧课题，停止旧服务后备份 `.local` 下的项目状态目录，包括 SQLite 主文件及对应项目产物；不要只复制数据库而漏掉产物。
3. 在新电脑设置 `RESEARCH_STATE_DIR` 指向完整备份目录，重新配置 SSH、模型和文档工具。
4. 用 `list_projects` 和 `project_overview` 核对旧作业、暂停状态、预算和产物，再由 Agent 接续。跨机器路径改变时先核查依赖，不重复提交旧实验。

浏览器中的辅助科研草稿保存在原浏览器 localStorage。需要带走时先下载 `AGENT_TASK.md`；它不会自动随源码 ZIP 移动。

## Linux

发送 `ai2research-linux-20261124.tar.gz`，解压后按 [Linux 上手说明](LINUX_QUICKSTART.md) 操作。包内包含同一套完整源码和最新前端，需要联网安装依赖。

仍可使用 `bash tools/bootstrap_linux.sh` 和 `./ai2research`。运行在 Linux 研究服务器时，按实际用户与目录配置 `[remote]`；运行实验前沿用具体授权、评价与预算。Windows 客户端通过 SSH 调用 Linux worker，页面本身不提交实验。

## 再次打包

```powershell
.\.venv\Scripts\python.exe tools\build_public_demo.py
.\.venv\Scripts\python.exe tools\package_ai2research.py ai2research-clean.zip
```

打包无需 Git。输出包含顶层 `ai2research/` 目录和逐文件 SHA256 的 `RELEASE_MANIFEST.json`；归档使用显式源码目录和文档白名单，不会扫描或打入个人运行目录。已有同名归档不会被覆盖。

Windows 安装将运行时与缓存放在本项目 `.local`，依赖放在 `.venv` 和 `.venv-arxiv`，不改系统 Python 或全局 PATH。技能与 MCP 仅在明确运行接入入口时安装。
