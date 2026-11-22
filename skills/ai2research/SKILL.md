---
name: ai2research
description: "Operate or continue an existing ai2research workspace through its CLI/MCP, durable tasks, frozen experiments, independent reviews and paper delivery. Use when the user names ai2research or works in its research repository; use research-workflow for general research methods and multi-stage planning."
---

# ai2research 项目入口

本技能只负责项目适配，通用科研方法维护在 [research-workflow](../research-workflow/SKILL.md)。

1. 读仓库AGENTS.md与最新决策，先查已有项目、执行状态和自动交接；不因断线、换模型或新会话重新开题。
2. 读取 [项目适配](references/harness.md)，使用已有CLI/MCP、状态库和worker。具体接口只按需查项目指南。
3. 当前工作需要方法指导时，从通用入口只读对应阶段；已有段落润色或draw.io编辑调用相应专用技能。
4. 依真实证据和现有授权推进，交接引用作业ID/产物ID，保持暂停、预算与历史记录。

科研阶段和项目工具分别维护一份规则，不把全部上游技能再次加载到上下文。
