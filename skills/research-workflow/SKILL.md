---
name: research-workflow
description: "Run or improve evidence-based research across literature, idea selection, experiments, analysis, figures, writing and review. Use for general research planning, integrated scientific workflows, or coordinating several research stages. Existing ai2research workspaces use their project adapter; standalone prose editing and editable draw.io diagrams keep their specialist skills."
---

# 通用科研工作流

从当前问题、已有材料和真实进度接续。完成用户需要的阶段；已有结果从分析或写作开始，已有课题不重新开题。默认中文沟通，稿件语言沿用用户或目标期刊要求。

## 共同规则

- 主张指向可定位原文、代码或运行产物。区分已观察事实、推测机制和待检验假设；文件存在、哈希一致、模型高分和进程成功各自只说明对应状态。
- 已授权范围内主动推进。只在缺少关键输入、授权或有实质取舍时提问；普通实现选择和可查信息自行解决。已有实验范围与预算跨会话保留。
- 当前目标、方法和评价协议以用户与项目记录为准，不预设顶刊、SOTA、固定论文篇数或普适提升；不把模型评分当作成果质量或录用概率。
- 一项任务、一套真实状态。复用现有执行器、作业ID、数据和缓存；工具缺失写明并采用可验证的替代路径。技能指令不等于已安装运行时。
- 失败保留原因、改动、复验与下一步。常规维护靠程序生命周期实现；技能提醒不能替代自动恢复、清理或资源计量。
- 编程围绕实际边界保持简单；写作直接陈述证据支持的结论。必要条件与局限集中说明一次，不堆叠防御性表达或掩盖负结果。

## 按阶段读取

只读取与当前工作有关的参考文件，通常先读一项；跨阶段完成时再切换，避免把所有技能放入同一上下文。

| 当前工作 | 读取 | 需要留下的结果 |
|---|---|---|
| 找论文、读源码、核实引文 | [literature.md](references/literature.md) | 带位置与版本的证据记录 |
| 从领域/论文找突破口 | [ideas.md](references/ideas.md) | 可证伪机制、区分实验和选择理由 |
| 基线、方法、复现、消融 | [experiments.md](references/experiments.md) | 固定协议、运行产物和公平比较 |
| 数字图、方法图、可编辑插图 | [figures.md](references/figures.md) | 数据/图元来源、复现源码和检查后的导出 |
| 新稿、重构论证、结果叙述 | [writing.md](references/writing.md) | 主张与证据一致的章节 |
| 审稿、修订、交付核查 | [review.md](references/review.md) | 有证据的问题及修复验证 |

已有 `ai2research` 项目或用户明确调用该技能时，再读其 [项目适配](../ai2research/references/harness.md)。适配层负责现有CLI/MCP、状态、授权和执行接口，共同科研规则只维护在本技能中。其他项目用自己的等价工具与路径；迁移时保留原项目事实，不据此新建替代实验。

独立润色使用 `nature-polishing`，draw.io文件编辑使用 `drawio-skill`；只在工作需要时调用。旧构思与ML/系统写作名称是本技能相应阶段的兼容入口，按一次请求只加载一份共同规则。

## 长任务交接

每个完成或中断的阶段保存：当前目标、主要决定及理由、代码/数据/运行ID、证据路径、失败与未决项、下一步和剩余资源。优先使用现有状态库与自动检查点；无执行器时用项目内一份简短交接文件，不复制长日志或原文。

分配子代理时给具体问题、必要文件和验收依据。实施与独立评审分开，按资源和信息依赖并行；不按固定代理数量或固定辩论轮数扩张。接续前先看摘要，执行/验收时再核查原证据；不能把摘要当作新的科研证据。

本技能的整合来源和适用边界见 [sources.md](references/sources.md)，仅在审查来源或更新技能时读取。
