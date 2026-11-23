# ai2research

面向人机协同的科研工作台，覆盖调研、筛选、跑实验、迭代、出图、出稿和润色。支持全流程科研（AutoSearch）与研究者主导的辅助科研，共用CLI/MCP、持久记录和工具资源目录。

## Windows 同学使用

完整解压后：

1. 双击 `1_install.cmd`，安装本项目的Python和依赖。
2. 双击 `2_start.cmd`，打开 `http://127.0.0.1:8765/`。
3. 需要Codex调用科研工具时，运行 `3_connect_codex.cmd`，然后刷新MCP连接。
4. 运行 `4_check.cmd`，查看 `.local/install-check.json` 自检回执。

详细操作见 [Windows 上手说明](WINDOWS_QUICKSTART.md)，迁移自己的状态与重新打包见 [迁移说明](MIGRATION.md)。安装不需要管理员权限，不修改系统Python；本机实验接口依赖Linux worker，可连接Linux服务器或WSL。

## 工作台

六个页面为工作台、辅助科研、全流程科研、研究进度、成果、工具与资料。前端无需构建、CDN或前端依赖：

- 辅助科研支持“还没有idea / 已有idea / 已有结果”三个起点，选择领域、研究范围、工具和人工确认点，生成可交给Agent的任务文档。
- 工具与资源目录含111个入口，按调研、实现、产出和9个领域浏览；支持搜索、类型筛选、论文代码入口与自定义工具。
- 研究进度和成果读取自己的项目记录，显示12个研究里程碑、实验、图表、稿件和完成依据。

网页提供工具选配、文档交接与记录查看；研究执行由活跃Agent通过CLI/MCP组织。

## 核心工具

62个stdio MCP工具与对应CLI，共用SQLite状态：

- 领域或论文入口、文献/代码发现、原文读取与BibTeX。
- 多项目、依赖任务、领取租约、产物交接、暂停与接续。
- 冻结方案与批准范围、Linux CPU/GPU实验、独立评价、回收与有界恢复。
- 结果比较、对照/复核/消融矩阵、评审与修订任务。
- 分章写作、TeX整合、图表、draw.io导出与有摘要清单的交付包。

能力与依赖见 [CAPABILITIES.md](CAPABILITIES.md)，研究操作见 [框架指南](research/FRAMEWORK_GUIDE.md)。单用户工作台由宿主Agent推进判断和代码修改；持久worker可以继续执行已提交任务。

## Linux

解压源码发行包后，按 [Linux 上手说明](LINUX_QUICKSTART.md) 安装；安装入口需要可用的 uv。

```bash
bash tools/bootstrap_linux.sh
./ai2research showcase
```

默认使用锁定的Python 3.14.4主环境与Python 3.12.2 arXiv环境。也可用Python标准库独立启动页面：

```bash
.venv/bin/python tools/run_workbench.py
```

开启实验前，按自己的SSH账号、目录与GPU范围修改 `harness.toml`；外部评审默认调用额度为0，按自己的服务、模型、密钥环境变量与预算启用。研究批准、任务暂停与历史记录持续保留。

## MCP 与技能

```powershell
.\.venv\Scripts\python.exe tools\install_local_skill.py --copy --dry-run
.\.venv\Scripts\python.exe tools\configure_harness_mcp.py --dry-run
```

Windows技能采用复制，Linux默认软链接；安装器可恢复原件。MCP使用当前虚拟环境Python的绝对路径，支持含中文与空格的目录，不覆盖不同配置的同名服务。通用密钥环境变量按照 `harness.toml` 的 `key_env` 转发，密钥不写入项目。

## 静态目录与部署

`docs/` 是没有研究记录的公共目录演示，可试用工具选择与文档生成：

```bash
python tools/build_public_demo.py
python -m http.server 8080 --directory docs
```

导出自己项目的只读快照：

```bash
.venv/bin/python tools/export_showcase_static.py --output .local/showcase-static
```

Linux可用 `tools/deploy_showcase.py` 建立用户级服务；只读展示不启动实验或模型调用。绑定局域网地址时，由部署者决定访问范围。

## 验证与分发

```bash
.venv/bin/python tools/verify_install.py --full
.venv/bin/python -B -m unittest discover -s tests -q
.venv/bin/python tools/package_ai2research.py ai2research-clean.zip
```

Windows命令中的Python路径改为 `.venv\Scripts\python.exe`。安装自检不提交实验、不访问SSH、不调用付费模型；完整回归含Linux worker边界测试，以Linux环境运行。

源码包使用白名单和逐文件SHA256清单，不包含个人状态、凭据、数据集、模型和私人课题。验证结果与原生Windows验收范围见 [VALIDATION.md](VALIDATION.md)。

## 许可

[MIT](LICENSE)，依赖与参考来源说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
