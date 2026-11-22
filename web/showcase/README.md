# ai2research 研究工作台

轻量 HTML/CSS/JS，无构建步骤、CDN、在线字体或前端依赖。页面分四个独立视图，导航切换内容，支持地址直达、刷新与浏览器返回：

- 总览：当前项目状态、完成环节、实验计数与最近动态。
- 研究进度：从选题到交付的 12 个环节，以及完成依据、相关材料与下一步。
- 成果：论文、结构图、实验图表与逐次实验记录。
- 工具与资料：参考论文 PDF、GitHub 地址和版本，以及 Skill、MCP、插件、执行与模型清单。

工具卡片提供已核实的 GitHub 来源；官方插件使用已登记的说明或官网链接，本地技能缺少公开来源时明确标注。

顶部始终保留项目选择器；使用帮助只有一个入口。支持手机布局、键盘操作、减少动态效果设置和研究指令复制，复制失败时显示可选文字。

## 服务器静态部署

导出研究记录与当前已核验的论文、图表，再以用户级服务提供静态文件：

```bash
.venv/bin/python tools/export_showcase_static.py
.venv/bin/python tools/deploy_showcase.py --host SERVER_IP --port 8765 --static-directory .local/showcase-static
```

浏览器访问 `http://SERVER_IP:8765/`。服务独立于 SSH 会话，异常退出自动重启。只提供静态前端，无研究后端；研究进展变化后重新运行导出命令，再刷新页面。页脚显示快照导出时间，切换项目时读取该项目的静态快照。


## 本机实时查看

```bash
./ai2research showcase
./ai2research showcase --host SERVER_IP --port 8765
```

默认仅本机 `127.0.0.1:8765`；指定服务器网卡地址后可同网段访问。实时模式每 30 秒读取已保存的记录，静态部署每 30 秒重新读取导出的快照。打开说明弹窗时暂缓刷新。读取失败保留上次数据并提示。

展示服务以 SQLite `mode=ro` 读取一致快照，不经过写维护状态的 service 包装，不调用模型或提交实验，不改变暂停状态。空状态库不初始化项目。

## 进度与证据

12 个环节分别为选题入口、文章筛选、代码核查、可行课题、实验准备、实验运行、复核消融、实验审查、论文初稿、图表制作、稿件审查、成果交付。按已有证据计数，不预测剩余时间，不把运行预算上限当成必做次数。

实验准备以有效任务交接，或已核实的正式双审及交付验收作为完成依据。后一情况要求已有冻结方案、批准和成功回收结果，且正式归档及其当前文件摘要一致。历史任务交接缺项仍在阶段说明中展示，原任务板保持原状态。仅有实验结果或正面评语不足以替代正式验收；新审查或交付文件变化后重新展示待核对。

审查显示当前轮次的意见与验收，高分不清除重大问题。参考资料来自摘要匹配的已登记清单；工具清单区分文件存在、配置登记和实际可用性，不读取密钥或发起付费调用。

成果提供当前登记且摘要一致的 `manuscript_pdf`、`diagram_pdf`、`figure_png`、`figure_pdf`，每类一个；正式稿件验收后标为“论文终稿”。拒绝旧版本、修改文件、跨项目路径和符号链接。实时服务资源采用白名单。普通输出最多 32 MiB；正式归档采用流式摘要校验（归档最多 128 MiB、成员最多 32 MiB、累计最多 128 MiB），不解压或执行。

## 验证

```bash
.venv/bin/python -B -m unittest tests.test_showcase tests.test_progress tests.test_showcase_catalog -q
```

可选真实浏览器检查使用 Node 24 WebSocket 与 Firefox WebDriver BiDi，无 npm 依赖。创建临时 profile 后启动：

```bash
firefox --headless --no-remote --profile /absolute/path/to/temporary-profile --remote-debugging-port 9222 about:blank
SHOWCASE_URL=http://SERVER_IP:8765/ node tests/showcase_browser.mjs
```

验证四页路由、刷新/返回、单一帮助入口、完成依据、实际文件 SHA256、论文与代码链接、工具筛选、暂停保护、空项目、复制兜底与 1440/390/320 布局。截图与回执保存在 `research/showcase/`。完成后关闭临时浏览器并删除 profile；保留正式静态服务。
