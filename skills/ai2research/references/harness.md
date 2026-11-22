# ai2research 项目适配

本文件只定义现有执行接口。科研方法、证据与行文要求以 [通用工作流](../../research-workflow/SKILL.md) 及当前阶段参考为准，避免并行两套共同规则。

## 定位与接续

使用本技能所在仓库根目录，读AGENTS.md、最新RESEARCH_PLAN.md和当前自动交接。MCP与CLI使用同一后端；MCP尚未发现新工具时使用 `<repo>/ai2research <tool> '<JSON>'`，复杂参数用 `@arguments.json`，不放凭据。

先用 `list_projects` 查项目，`project_overview`看摘要。已有方向不重新创建；执行状态与预算从状态库核查，暂停项保留。`maintenance_status`提供自动checkpoint路径，交接供接续导航，科研判断回到证据。`research/FRAMEWORK_GUIDE.md`用于具体接口查阅，

## 入场与任务

- 领域+要求：`start_from_request(project_id, domain, requirements, as_of=客户端日期)`；该项目默认的近期论文数量和核验范围由输入契约决定，不覆盖通用技能适用范围。
- 已有论文：`start_project(project_id, seed_papers, title)`。
- `record_paper_candidates`登记原文、正式发表与代码核验声明；登记不等于阅读或执行。主控实际核查后通过 `research_workflow(action="next")` 领取可做项。
- 用具体task ID/prompt_path分配子代理。`claim_task`拿租约，长任务 `update_task(state="running")`续租，产物先 `register_artifact`，完成时交 `artifact_ids`；失败保存原因。已有产物要核实后交接，不能因任务板pending而重复实验。
- 候选核查完成后 `research_workflow(action="select", task_id, reason)`建立方法、实现、评价、搜索、复核与消融依赖。旧证据改变时按 `repair_changed_task_evidence`处理，不绕过交接校验。

文献优先 `fetch_paper/read_paper` 与官方原文；本地PDF可用 `extract_paper_text`并记录提取器/表图覆盖范围。`parse_pdf`是可选Docling入口，返回unavailable时不要宣称已接入。

## 实验和恢复

`prepare_experiment`冻结代码、数据/协议、评价器及资源/次数范围。沿用用户实际授权登记约束，不反复索要已给授权；禁止编造批准或将 `record_decision`当批准。换目标、评价器或扩预算时形成具体变更。

`submit_experiment` / `research_cycle`使用已有worker。主控依据结果判断下一方法改动；队列执行不等于模型独立完成方法搜索。一次授权覆盖的有界调参可继续，暂停状态、累计次数和失败记录保持。

`resume_project`省略offset默认跨进程轮转，跳过已回收作业，以原job ID核查；`has_more`时按正常节奏再次调用，无需人工翻页。提交响应不确定用 `retry_job_dispatch`确认同一作业，不新起替代运行。暂停项目可查询/回收/归档，不重新提交。

`collect_job_results`后以 `attach_job_evidence(phase="baseline"|"search"|"verification"|"ablation"|"rebuttal")`关联固定评价产物。`build_experiment_matrix`整理真实比较，`export_results_figure`为基础柱图入口；若需误差线/曲线，用项目数据与脚本生成并登记，不宣称该接口已支持所有图型。

## 独立复核、稿件与交付

`prepare_review(stage="experiment", artifact_ids=...)`冻结证据包。独立子代理通过真实身份 `record_review(reviewer="agent", model, provider, review)`提交；DeepSeek用 `run_external_review`真实调用和回执，不手填外部意见。以 `review_budget`查询预算，材料过长按明确范围分包，不能静默截断后声称全审。

`review_status`检查当前包和阻断问题，`create_revision_tasks`转修订；新证据登记新版本并重审。实验与稿件双评审按现有项目要求保留，内部过线不等于录用。

实验评审完成后 `publication_plan`建逐章任务，`paper/context.json`协调术语、符号与主张。`assemble_manuscript`整合并编译，`export_project_diagram`导出已登记可编辑图。图和PDF实际打开检查，稿件冻结包含图/源码版本的 `render_manifest`，再以stage="manuscript"双评审。最后 `export_delivery`核对版本/证据/评审覆盖并打包；不完整状态照实保留。

## 自动维护和开销

`get_project_state` / `research_workflow`默认精简，必要时 `include_details=true`；日常用摘要与增量 `process_log`。CLI/MCP外层操作后自动保存checkpoint、handoff和最近50行日志，普通维护30秒限频；过期租约和任务板脱节只提示核对。

新回收stage正常结束清理，异常残留按owner/期限/进程状态自动回收；实验数据和证据保留。维护失败单独记录退避，不覆盖主操作。没有额度定时续跑/模型池常驻驱动；本适配不启动第二套守护或自动解除暂停。
