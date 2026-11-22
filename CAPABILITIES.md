# ai2research 当前能力

ai2research 是由研究助手驱动的科研工作台。当前主要以 Codex 对话负责阅读、判断、改代码和分配子代理；项目提供 62 个 MCP 工具入口、对应 CLI、持久状态、实验执行与证据验收。

## 已实现

| 模块 | 当前能力 | 主要入口 |
| --- | --- | --- |
| 研究入口 | 从领域与要求，或已有论文建立项目；记录近期论文、正式发表依据、代码许可和固定提交 | `start`、`start_from_request`、`record_paper_candidates` |
| 文献与代码 | arXiv 检索、获取与分段读取、BibTeX，GitHub 代码发现与固定版本阅读，本地 PDF 文本提取 | `search_papers`、`read_paper`、`find_code`、`read_code`、`extract_paper_text` |
| 任务协作 | 多项目、依赖任务、领取租约、产物交接、暂停、跨对话接续；子代理由宿主创建 | `research_workflow`、`claim_task`、`update_task`、`set_project_execution_state` |
| 实验执行 | 冻结代码、参数、评价器及资源范围；SSH CPU/GPU worker，日志、超时、取消、回收和同 ID 恢复 | `prepare_experiment`、`submit_experiment`、`collect_job_results`、`resume_project` |
| 证据与比较 | 来源摘要核验、主实验/复核/消融分类、独立评价、结果矩阵、MLflow 对比 | `attach_job_evidence`、`validate_result_artifact`、`build_experiment_matrix` |
| 双重评审 | 实验与稿件分别冻结审查包，登记独立代理意见、调用外部评审、限额记账、生成修订任务；文件变化后旧意见失效 | `prepare_review`、`run_external_review`、`review_status`、`create_revision_tasks` |
| 论文与图表 | 分章任务、共享术语与主张、TeX 整合、通用或 IEEE 会议格式、PDF 编译、可编辑 draw.io 图和数据图 | `publication_plan`、`assemble_manuscript`、`export_project_diagram`、`export_results_figure` |
| 正式交付 | 核对当前代码、数据来源、图稿与双审覆盖，生成带清单和摘要的交付 ZIP | `export_delivery` |
| 持久记录 | SQLite 事件、任务与产物状态，交接快照、增量日志和失败经验；断开对话后保留 | `project_overview`、`process_log`、`maintenance_status` |
| 展示页面 | 总览、研究进度、成果、工具与资料四页；12 环节及完成依据、论文 PDF/GitHub、工具来源、手机布局 | `showcase`、`tools/export_showcase_static.py` |
| 部署与迁移 | 独立环境、锁定依赖、技能安装、stdio MCP 配置、实时页面或纯静态快照、Linux 用户级常驻服务 | `tools/bootstrap_linux.sh`、`tools/configure_harness_mcp.py`、`tools/deploy_showcase.py` |

## 使用前需要接入什么

- **研究主控**：当前默认用法依赖活跃 Codex 对话。CLI/MCP 可以供其他兼容宿主调用；其他宿主的完整科研流程需要单独适配和验证。
- **算力与依赖**：按自己的 SSH 账号、目录和 GPU 修改 `harness.toml`，为具体课题安装基线、数据与运行环境。
- **外部评审**：配置 OpenAI 兼容服务、模型、仓库外密钥和调用额度。公开版默认关闭外部评审调用。
- **文档工具**：PDF 文本提取、TeX 编译、draw.io 导出需要对应可执行程序；完整 PDF 结构解析的 Docling 接口仍属可选项。

## 当前产品范围

当前版本适合单用户或小团队在自己的服务器上，用研究助手推进可追溯的课题。网页用于查看；纯静态部署需重新导出以更新研究状态。实验启动、评审和写作由研究对话或 CLI/MCP 驱动。

尚未提供账号权限系统、多人在线编辑、浏览器内直接启动研究、无人值守科研主控，或免配置的一键多服务器迁移。Optuna 存在工具验收入口，具体方法搜索由研究助手组织。

公开仓库包含框架、技能、测试、模板与合成页面演示。私人密钥、运行状态库、数据集、模型权重和真实课题稿件由使用者单独保存。

验证命令与记录见 [README](README.md#validation) 和 [VALIDATION.md](VALIDATION.md)。
