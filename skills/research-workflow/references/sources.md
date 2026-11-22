# 整合来源与取舍

核查日期：2026-11-19。下列为阅读过的上游快照和设计参考，本技能及阶段文件为本地原创整合，没有整包复制、安装依赖或引入第二套研究执行器。许可证是所读版本的记录，不代表未来版本或所有依赖。

| 名称 / 固定提交 | 可用部分 | 本次处理 |
|---|---|---|
| [Imbad0202/academic-research-skills](https://github.com/Imbad0202/academic-research-skills/tree/74fe22717ac7b9ea740861f4a120aa476b40dc08)；CC BY-NC 4.0 | 文献到稿件/复审的阶段组织 | 参考一般流程，不复制非商用内容；不继承每阶段确认、固定角色或只提问不作答 |
| [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills/tree/ec59a29a03d5c40f73ab40878df04860bc00e876)；MIT | literature-review、experimental-design、statistical-analysis、scientific-visualization的证据/设计意识 | 精选原则；不全装领域包、不强制自引或把人工核验当所有代理工作的前提 |
| [Yuan1z0825/nature-skills](https://github.com/Yuan1z0825/nature-skills/tree/1d1b9dd00a9e5fde1fdc0e15b03919d9c256cfc4)；根Apache-2.0、个别技能另标许可 | 按需片段、稿件功能、图件检查 | 保留本机nature-polishing/nature-shared专用资源；不把Nature风格当通用格式 |
| [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw/tree/be4ba4755bf1b52220f25e13b2293b5956590070)；MIT | 有界修正、来源登记、真实代码/结果复审 | 现有harness继续执行；不合并23阶段执行器、模板回退评审或宽松数字匹配验收 |
| [Orchestra-Research/AI-Research-SKILLs](https://github.com/Orchestra-Research/AI-Research-SKILLs/tree/773a52944ba4747a18bd4ae9ade53fff041adcbc)；LICENSE为MIT，根package.json另标ISC | 方法构思、ML/系统写作、ARA主张与原始/派生证据区分 | 本机四个入口与该版本逐字节相同；改为兼容入口，共同指导只维护一份，旧模板保留 |
| [LY2260789/scientific-illustrator-agent](https://github.com/LY2260789/scientific-illustrator-agent/tree/a2b4120486986ced89936be2019c982c9be97da9)；未选开源许可 | 编辑源、结构图说明、导出图检查的一般思路 | 不复制实现；Windows/Adobe COM运行时不适配当前Linux，继续使用draw.io/SVG和数据绘图 |

`academic-research-skills`还有 [VincenzoImp](https://github.com/VincenzoImp/academic-research-skills) 和 [kinqsradio](https://github.com/kinqsradio/academic-research-skills) 的同名仓库，均有不同目录/流程。用户未给owner，本次以Imbad0202为主候选，并明确保留歧义；两个同名项目只核查README/许可，不作为复制源。

## 具体审查依据

- AutoResearchClaw的 [`verified_registry.py`](https://github.com/aiming-lab/AutoResearchClaw/blob/be4ba4755bf1b52220f25e13b2293b5956590070/researchclaw/pipeline/verified_registry.py) 依靠已登记数字及近似值匹配，不能证明数字属于正确指标/数据集/条件。因此本整合保留语义明确的主张→运行→评价输出关系。
- Orchestra的 [`0-autoresearch-skill/SKILL.md`](https://github.com/Orchestra-Research/AI-Research-SKILLs/blob/773a52944ba4747a18bd4ae9ade53fff041adcbc/0-autoresearch-skill/SKILL.md) 包含常驻循环指令，ARA compiler有探索节点下限；不将这些约束迁入有预算、可暂停的现有研究。
- Nature的ref-verifier含Crossref 404即DOI错误的规则；[Crossref官方接口](https://github.com/CrossRef/rest-api-doc)区分Crossref与其他注册机构，文献模块已修正此推断。数字页码差的固定阈值也不替代元数据核对。
- 两个构思技能的思考方法合并进ideas；ML/系统写作合并进writing，仍按稿件类型区分问题。nature-polishing处理已有段落，drawio-skill处理可编辑图文件，继续作为专用技能保留。

## 维护

上游只按明确缺口更新，不自动覆盖本地整理或批量导入全库。执行 `tools/install_local_skill.py` 可幂等安装入口/兼容层；既有被替换文件先备份，遇到安装后用户另改的内容会保留并报告冲突。旧名称只在明确调用时使用，共同内容按阶段加载。

本机入口链接到仓库源码；需要迁移仓库路径时，先在原位置执行安装脚本的 `--restore`，移动后从新位置重新安装。技能内容不绑定模型/API提供商；运行接口与凭据仍由目标项目单独配置。
