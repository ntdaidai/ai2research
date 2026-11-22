# ai2research 框架使用

当前工作方式：用户提供几篇论文，活跃 Codex 对话担任主控，使用宿主子代理并行执行任务；CLI/MCP 保存任务与证据，原有 worker 执行批准范围内的实验。没有新增后台自动科研服务。

## 开始与接续

在对话里说“用 ai2research 研究这几篇论文”并给链接、arXiv ID 或可读文件路径即可。入口技能安装在 `~/.codex/skills/ai2research`，源码在仓库 `skills/ai2research/`。宿主尚未发现新技能时可直接读该 `SKILL.md`。新 MCP 工具通过新服务连接发现；当前对话可以立即使用相同后端 CLI。

终端入口也可直接使用，不要求先填研究问题、数据和基线：

```bash
cd <repository>
./ai2research start 'https://arxiv.org/abs/1706.03762' '另一篇论文链接或本地PDF路径' --project my-study
./ai2research research_workflow '{"action":"next","project_id":"my-study"}'
./ai2research list_projects
./ai2research toolchain_status
```

论文引用会保存为筛选任务输入；创建项目不代表已下载、读完论文或启动实验。主控按任务主动获取原文、代码并继续行动。`start @papers.json` 接受论文字符串数组或包含 `seed_papers/project_id/title` 的对象；同 ID、同输入幂等接续，不覆盖已有产物。

## 主控的调用顺序

| 阶段 | 调用 | 结果 |
| --- | --- | --- |
| 论文输入 | `start_project` / `research_workflow(action="start")` | 每篇论文一个核查任务、项目目录与交接说明 |
| 并行分发 | `research_workflow(action="next")`、宿主子代理 | 列出依赖就绪任务，按槽位并行 |
| 代理交接 | `claim_task` → 写文件 → `register_artifact` → `update_task` | 原子领取、租约续期、产物摘要与完成状态 |
| 选题 | `research_workflow(action="select",task_id=...,reason=...)` | 方法、基线、评价器、搜索、复核、消融任务图 |
| 实验执行 | 原有 `prepare_experiment/research_cycle/resume_project` | 在真实批准范围内派发、回收、比较；不重复提交 |
| 实验证据 | `attach_job_evidence` / `build_experiment_matrix` | 原始指标、代码、配置、独立评价、MLflow 与作业来源 |
| 双评审 | `prepare_review` → 子代理 `record_review` + `run_external_review` → `review_status` | 同一版本材料，两份独立意见 |
| 补工作 | `create_revision_tasks` | 幂等创建补实验、复核或修稿任务 |
| 逐章写作 | `publication_plan` | 8 个独立章节任务与依赖，共享术语、主张和锁定证据 |
| 图与稿 | `export_results_figure/export_project_diagram/assemble_manuscript` | 可复现数据图、可编辑方法图、分章 TeX 与新编译 PDF |
| 出稿后双评审 | `prepare_review(stage="manuscript")`，重复双评审与限次修改 | 当前稿件版本的意见、处理记录与覆盖范围 |
| 交付 | `export_delivery` | 摘要校验、来源与双评审门槛、ZIP 和完整文件清单 |

任务参数较长时主控将 JSON 存到项目内参数文件，用 `./ai2research <tool> @文件.json` 调用；不需要用户手写这些文件。被领取任务返回令牌，后续只由持有令牌的代理更新；长任务用 `update_task(state="running")` 续租。跨对话读取项目状态和任务提示，不靠复制全部聊天历史。

## 实验、复核与消融

用户要求暂存一个方向时，用`set_project_execution_state(project_id, state="paused", user_instruction="实际原话", reason="具体原因")`持久暂停。`project_status`/`research_workflow(next)`显示paused，任务不再就绪，新的prepare/submit/claim/redispatch会被拒绝；已有作业不隐式取消，读记录/回收/归档可继续。用户明确恢复后用state="active"登记其原话，原预算和证据不重置。`resume_project`只回收旧作业，不解除暂停。

每次`collect_job_results`自动保存带已验证来源SHA的`job_experience`观察，重复回收幂等，也可回填历史已收集任务。不调用模型、不自动重试或改实验源码；主控据日志定位后修复并把实际验证记入`research/EXPERIENCE_LEDGER.md`。新worker分开记录资源排队、GPU租约和训练/评价wall时间，SIGXCPU明确为全部线程累计的每进程CPU时间上限；训练和评价共享wall deadline。历史缺失计时保持未知。

基线实现与独立评价设计可以并行，完成搜索后复核与消融可以并行。GPU 实际并发由既有资源与租约决定，不能由代理数量推断。

`attach_job_evidence` 必须在 `collect_job_results` 后调用。phase 取 `baseline/search/verification/ablation/rebuttal`，它完整校验来源后复制原始产物；空日志保留但不能充当完成产物。研究证据必须对应冻结提案与独立评价，来源和阶段历史会保留。`validate_result_artifact` 可单独诊断来源。

完整交付需要真实主结果、复核和消融；复核与消融不能复用搜索/基线作业，也不能共用同一次作业。说明文档用于解释，不能代替真实指标。实际方法、数据依赖和适当统计协议由课题任务建立，不预装所有候选课题环境。

## 评审与额度

实验、稿件分别冻结证据包，当前配置每包最多 110000 字符，外部请求最多 120000 字符，不能静默截断。超长材料按章节/完整代码函数分包，每包分别取得两位评审意见；交付核查当前稿件所有章节与参考文献的覆盖，新意见优先于同源旧意见。稿件评审还须覆盖自动生成的 `paper/render_manifest.json`，把图、样式和额外源码的当前版本绑定到评审。PDF/图像由子代理实际查看并记录观察，外部文本评审不声称审阅图片内容。`stage="framework"` 用于工程验收并保留工具测试标签，不能代替科研阶段评审。

同一包调用外部评审幂等，进程中断可回收已持久化回执，不自动付费重试。明确重审用 `round_key`；同阶段、同材料集合最多 3 轮。`review_status` 要求两位评审都达到量表阈值且无未解决重大问题，材料摘要变化则旧意见过期。实验门槛还会检查真实独立评测来源。

密钥存于仓库外 `~/.config/ai2research/review.key`（0600），本轮已更换为用户新密钥并沿用确认的阿里云地址/模型。配置初始设 Linux 累计 32 次调用、每次最多 4096 输出 tokens，保留全部历史用量。用 `review_budget` 查询真实余量；这是本地默认调用边界，不能据此推断货币余额。

该混合模型在较小输出预算下可能只输出思考过程而没有评审正文。短篇结构化评审现明确使用 `enable_thinking=false`；可在 `harness.toml` 中调整，响应的实际模型、模式、用量和停止原因均保存。依据：[阿里云兼容 API 参数](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)。不完整响应不会放行评审。

## 复用的现有能力

- FastMCP/CLI、SQLite、GPU worker、MLflow、Optuna继续复用。
- arXiv/代码检索继续使用现有入口；本地 PDF 的 `extract_paper_text` 明确使用本机 `pdftotext`，保留原文件与来源摘要，只提取文本层。Docling仍为可选接口，未安装时明确返回 unavailable。
- 选题/写作/润色路由到既有科研技能，`toolchain_status` 可查看实际安装路径。
- drawio-skill用于编辑图源，`export_project_diagram` 使用已安装 draw.io；已登记图源修改后先重新 `register_artifact`，再导出新版本。
- Tectonic完成 TeX 编译；默认模板为通用 article，投稿前按课题换成核验过的会议官方模板。

## 验证与源码迁移

```bash
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python tools/verify_framework.py
python3 tools/install_local_skill.py
```

串联验收使用独立 `.local/framework-acceptance/` 状态与 `toolchain_test` 项目，不调用外部模型、不执行科研实验。它通过真实 MCP、两个独立任务进程、8 章整合、实际绘图/编译/文本提取和 ZIP 校验检查工具行为；测试材料不能进入正式成果评审或完整论文交付门槛。科研效果须在后续真实课题验收。

源码打包会包含 `skills/ai2research`，不包含凭据、运行数据库或用户课题产物。迁移后安装相应运行时并执行 `tools/install_local_skill.py`；共享多个对话时始终指向同一状态库。复用已有作业，不恢复被移除的后台自动接续。

CPU小模型实验同样使用冻结提案：`gpu_memory_mb=0`，选隔离CPU解释器并保持`evaluation_on_gpu=false`。它复用原worker的CPU环境/内存与时间限制，执行单独冻结评价，回收和研究证据校验与GPU相同。CPU/GPU结果分别记录设备；同种子比较须匹配设备，不能把CPU补跑直接拼成GPU配对。

## 已观测内核页开销的显式运行选项（2026-11-19）

`prepare_experiment(..., disable_transparent_hugepages=True)`只在新子进程启用，并冻结在资源manifest；默认False不增加key，保留旧批准资源摘要。worker在exec前调用进程局部prctl并确认状态，失败明确报错；不修改系统THP配置或其他任务。`phase_history.resource_sample.THP_enabled`记录实际值。该选项是运行时资源策略，不放进方法超参数，不用于改变数据、模型或评分。

本轮真实像素metric诊断发现某些进程的system CPU远大于user CPU，禁THP后同一candle200图ROC112.03→8.25秒、值完全一致；AP/PRO与原独立评价差≤2.22e-16。证据`research/studies/cv/analysis/PIXEL_METRIC_RUNTIME_DIAGNOSIS.md`。单NUMA亲和并未解决所有开销，不从一个绝对NUMA计数推断本phase迁移量。先查实际CPU/faults/THP样本，再决定新冻结scope是否采用该选项；不要热改已经批准运行中的作业或盲目重训。

已完整完成模型生产阶段但独立评价超时的作业，可用课题内的具体checkpoint适配器保留原字节和来源SHA，在新job中重新绑定模型manifest路径，再由原冻结独立评价器完整运行。原timeout不改为completed；新job记录恢复来源、拷贝耗时和独立评价指标，失败仍计入历史预算。

## IEEE会议格式

在已登记`paper/context.json`设置`"latex_format": "ieee_conference"`，assemble使用标准IEEEtran conference模式及IEEEtran参考文献样式；省略仍为generic。可选paper/keywords.tex作为关键词并绑定到render输入。未知或非字符串格式拒绝，不能注入任意TeX命令。原版模板/来源SHA保存在reproduction/templates/ieee，编译器使用其发行版标准包并在log记录版本；不通过geometry、缩正文字体或改页边距伪装格式。修改格式后必须重新编译、实际看PDF并审当前版本。
