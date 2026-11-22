"use strict";

const $ = (id) => document.getElementById(id);
const fmt = new Intl.NumberFormat("zh-CN");
const dateFormat = new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false});
const stateLabels = {completed:"已完成", timeout:"超时", failed:"失败", resource_unavailable:"资源不足", running:"执行中", evaluating:"评价中", queued:"排队中", starting:"启动中", waiting_resources:"等待资源", submission_reserved:"已登记", submitted:"已提交", cancelled:"已取消", canceled:"已取消"};
let current = null;
let selectedStage = null;
let selectedProject = null;
let requestId = 0;
let activeRequest = null;
let lastRingKey = "";
let lastChartKey = "";
let toastTimer;
let resourceTab = "references";
let toolFilter = "all";
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}
function date(value) { return value ? dateFormat.format(new Date(value)) : "暂无记录"; }
function number(id, value) { $(id).textContent = fmt.format(value); }
function showNotice(message) { $("notice").textContent = message; $("notice").hidden = !message; }

function renderStage(stageId) {
  const stage = current?.project?.progress?.stages.find(s => s.id === stageId);
  if (!stage) return;
  selectedStage = stageId;
  document.querySelectorAll("[data-stage]").forEach(node => node.setAttribute("aria-pressed", String(node.dataset.stage === stageId)));
  $("stage-title").textContent = stage.title;
  $("stage-state").textContent = stage.state_label;
  $("stage-state").className = `phase-badge ${stage.state}`;
  $("stage-description").textContent = stage.summary;
  $("stage-checks").replaceChildren(...stage.checks.map(check => {
    const row = element("li", check.done ? "criterion done" : "criterion");
    row.append(icon(check.done ? "check" : "clock"), element("span", "", check.label));
    if (check.detail) row.append(element("small", "", check.detail));
    return row;
  }));
  $("stage-notes").replaceChildren(...stage.notes.map(note => element("p", "", note)));
  $("stage-notes").hidden = !stage.notes.length;
  $("stage-notes-wrap").hidden = !stage.notes.length;
  const items = stage.items || [];
  $("stage-items").replaceChildren(...items.map(item => {
    const row = element("div", "stage-item");
    let label = element("span", "", item.label);
    try {
      const url = new URL(item.url);
      if (["https:", "http:"].includes(url.protocol)) {
        label = element("a", "", item.label);
        label.href = url.href;
        label.target = "_blank";
        label.rel = "noopener";
      }
    } catch {}
    row.append(label, element("small", "", item.detail));
    return row;
  }));
  $("stage-items").hidden = !items.length;
  $("stage-next").textContent = current.project.progress.paused ? "项目已暂停，保留已有进展，等待恢复。" : stage.next_action;
}

function renderProgress(project) {
  const progress = project.progress;
  const stages = progress?.stages || [];
  $("progress-count").textContent = progress ? `${progress.completed_stages} / ${progress.total_stages} 环节已完成` : "等待研究项目";
  $("overall-progress").max = progress?.total_stages || 12;
  $("overall-progress").value = progress?.completed_stages || 0;
  $("progress-attention").textContent = progress?.paused ? "项目已暂停" : progress?.attention_stages ? `${progress.attention_stages} 个环节待处理` : "";
  $("progress-caption").textContent = progress ? "点击任意环节，查看已经做了什么、还需要什么。" : "开始一个研究项目后，这里就会显示完整进展。";
  const children = [];
  const groups = [["发现方向", "book"], ["实验验证", "flask"], ["形成成果", "file"]];
  stages.forEach((stage, index) => {
    if (index % 4 === 0) {
      const [label, glyph] = groups[Math.floor(index / 4)];
      const caption = element("div", "phase-caption");
      const title = element("span");
      title.append(icon(glyph), document.createTextNode(label));
      const members = stages.slice(index, index + 4);
      caption.append(title, element("small", "", `${members.filter(s => s.state === "completed").length} / ${members.length} 已完成`));
      children.push(caption);
    }
    const node = element("button", `map-node progress-node ${stage.state}`);
    node.type = "button";
    node.dataset.stage = stage.id;
    node.setAttribute("aria-pressed", "false");
    node.setAttribute("aria-label", `${stage.title}，${stage.state_label}，${stage.completed_checks}/${stage.total_checks} 项条件已满足`);
    const glyph = element("span", "node-icon");
    glyph.append(icon(stage.icon));
    node.append(glyph, element("span", "node-name", stage.title), element("span", `phase-badge ${stage.state}`, stage.state_label));
    node.addEventListener("click", () => renderStage(stage.id));
    children.push(node);
  });
  $("progress-nodes").replaceChildren(...children);
  const desired = selectedProject === project.id && stages.some(s => s.id === selectedStage) ? selectedStage : progress?.focus_stage;
  selectedProject = project.id;
  $("progress-detail").hidden = !stages.length;
  if (desired) renderStage(desired);
  $("goto-current").disabled = !stages.length;
  $("jump-current").disabled = !stages.length;
  renderJourney(project);
}

function renderJourney(project) {
  const progress = project.progress;
  const completed = progress?.completed_stages || 0;
  const total = progress?.total_stages || 12;
  $("hero-progress").textContent = progress ? `${completed} / ${total}` : "待开始";
  $("hero-progress-label").textContent = progress ? "环节已有完成依据" : "从一个好问题出发";
  const ring = $("hero-ring");
  const circumference = 2 * Math.PI * 65;
  const offset = circumference * (1 - completed / total);
  const before = Number(ring.getAttribute("stroke-dashoffset"));
  ring.setAttribute("stroke-dasharray", String(circumference));
  ring.setAttribute("stroke-dashoffset", String(offset));
  const key = `${project.id}:${completed}:${total}`;
  if (lastRingKey !== key && !reducedMotion.matches && ring.animate) {
    ring.animate([{strokeDashoffset:String(before)}, {strokeDashoffset:String(offset)}], {duration:850, easing:"cubic-bezier(.22,.61,.36,1)"});
  }
  lastRingKey = key;
}

function renderRuns(project) {
  const grid = $("run-grid");
  grid.replaceChildren();
  const fragment = document.createDocumentFragment();
  project.jobs.forEach((job, index) => {
    const state = stateLabels[job.state] || "其他记录";
    const button = element("button", "run-cell");
    if (Object.hasOwn(stateLabels, job.state)) button.classList.add(job.state);
    button.type = "button";
    button.title = `第 ${index + 1} 次 · ${state} · ${job.id}`;
    button.setAttribute("aria-label", button.title);
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => {
      grid.querySelectorAll("[aria-pressed=true]").forEach(node => node.setAttribute("aria-pressed", "false"));
      button.setAttribute("aria-pressed", "true");
      $("run-detail").replaceChildren(element("span", "mini-label", `第 ${index + 1} 次 · ${state}`),
        element("span", "mono", job.id.slice(0, 12)), element("span", "", job.collected ? "已回收记录" : "尚未回收"));
    });
    fragment.append(button);
  });
  grid.append(fragment);
  $("runs-empty").hidden = Boolean(project.jobs.length);
  $("attempt-count").textContent = `${fmt.format(project.jobs_total)} 次尝试`;
  $("run-detail").replaceChildren(element("span", "mini-label", "实验足迹"), element("span", "", "点击色块，查看这次尝试的记录。"));
  const legend = $("run-legend");
  legend.replaceChildren();
  const entries = Object.entries(project.job_counts).sort((a, b) => (a[0] === "completed" ? -1 : b[0] === "completed" ? 1 : b[1] - a[1]));
  entries.forEach(([state, count]) => {
    const item = element("span", "legend-item");
    const square = element("span", "legend-square");
    if (Object.hasOwn(stateLabels, state)) square.classList.add(state);
    item.append(square, document.createTextNode(`${stateLabels[state] || "其他"} ${count}`));
    legend.append(item);
  });
  const chart = $("run-chart");
  const chartKey = `${project.id}:${JSON.stringify(entries)}`;
  chart.classList.toggle("run-chart-enter", lastChartKey !== chartKey && !reducedMotion.matches);
  chart.replaceChildren();
  chart.setAttribute("aria-label", project.jobs_total ? `${project.jobs_total}次实验：${entries.map(([state,count]) => `${stateLabels[state] || "其他"}${count}次`).join("，")}` : "尚无实验记录");
  let x = 0;
  entries.forEach(([state, count]) => {
    const width = 600 * count / project.jobs_total;
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", "0");
    rect.setAttribute("width", String(Math.max(0, width - 2)));
    rect.setAttribute("height", "45");
    rect.setAttribute("rx", "4");
    rect.classList.add("run-segment");
    if (Object.hasOwn(stateLabels, state)) rect.classList.add(state);
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${stateLabels[state] || "其他"} · ${count} 次`;
    rect.append(title);
    chart.append(rect);
    x += width;
  });
  lastChartKey = chartKey;
  $("run-details").hidden = !project.jobs.length;
}

function renderOutputs(project) {
  const list = $("output-list");
  list.replaceChildren();
  const labels = {paper:["论文草稿","file"], diagram:["方法结构图","node"], figure:["实验图表","layers"]};
  project.outputs.forEach(output => {
    const [label, glyph] = labels[output.category];
    const link = element("a", "output-link");
    link.href = output.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.title = `${label} · ${output.path}\n登记 SHA256: ${output.sha256}`;
    link.setAttribute("aria-label", `打开${label}，新窗口`);
    const fileIcon = element("span", "file-icon");
    fileIcon.append(icon(glyph));
    const title = element("span", "file-name");
    const size = output.bytes >= 1048576 ? `${(output.bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(output.bytes / 1024))} KB`;
    const metadata = element("small", "file-meta");
    const basename = element("span", "file-basename", output.path.split("/").pop());
    basename.title = output.path.split("/").pop();
    metadata.append(basename, element("span", "file-size", `· ${size}`));
    title.append(element("strong", "", label), metadata);
    link.append(fileIcon, title, element("span", "file-format", output.path.endsWith(".pdf") ? "PDF" : "PNG"), icon("eye"));
    list.append(link);
  });
  $("outputs-empty").hidden = Boolean(project.outputs.length);
}

function renderReviews(project) {
  const review = project.reviews.find(r => r.stage === "experiment") || project.reviews[0];
  const paused = project.execution_state === "paused";
  let title = "从一个研究问题开始";
  let description = "选择项目后，查看当前进展和接下来值得做的事。";
  if (paused) {
    title = "研究暂歇，记录仍在";
    description = "此项目已暂停，已有实验与研究成果保留在这里。";
  } else if (review?.recorded_status === "revision_requested") {
    title = "沿着问题，继续打磨";
    description = "已保存实验与评审意见，研究在复核和修订中向前。";
  } else if (project.jobs_total) {
    title = "从尝试，走向证据";
    description = "实验结果已留存，方法与稿件沿着证据持续完善。";
  }
  const focus = project.progress?.stages.find(stage => stage.id === project.progress.focus_stage);
  if (focus && !paused) { title = `${focus.title} · ${focus.state_label}`; description = focus.summary; }
  $("focus-title").textContent = title;
  $("focus-description").textContent = description;
  $("focus-next").textContent = project.progress?.next_action || "在研究对话中给出一个领域或几篇论文，开始第一个项目。";
  $("copy-label").textContent = project.id ? (paused ? "复制查看进展指令" : "复制继续研究指令") : "复制开始研究指令";
  $("guide-start").textContent = project.id ? "带我查看当前进展" : "复制开始研究模板";
  const rows = $("review-summary");
  rows.replaceChildren();
  const stageLabels = {experiment:"实验评审", manuscript:"稿件评审", framework:"框架评审"};
  const reviewLabels = {revision_requested:"有修订意见", awaiting_reviews:"等待评审", reviews_recorded:"意见已留存"};
  project.reviews.slice(0, 2).forEach(record => {
    const row = element("div", "review-row");
    row.append(element("span", "", stageLabels[record.stage] || "评审记录"),
      element("span", `review-status${record.recorded_status === "revision_requested" ? "" : " neutral"}`, reviewLabels[record.recorded_status]));
    rows.append(row);
  });
  if (!project.reviews.length) {
    const row = element("div", "review-row");
    row.append(element("span", "", "评审记录"), element("span", "review-status neutral", "尚未登记"));
    rows.append(row);
  }
  $("last-event").textContent = date(project.last_event_at);
  $("last-event").dateTime = project.last_event_at || "";
}

function renderActivity(project) {
  const list = $("activity-list");
  list.replaceChildren();
  project.activity.forEach(entry => {
    const item = element("article", "activity-entry");
    const time = element("time", "", date(entry.time));
    time.dateTime = entry.time;
    item.append(time, element("h3", "", `${entry.category} · ${entry.action}`), element("p", "", entry.message || "一条新的研究记录已保存。"));
    if (entry.message) item.title = entry.message;
    list.append(item);
  });
  if (!project.activity.length) list.append(element("p", "empty-inline", "还没有研究足迹。"));
}

function resourceLink(label, value, glyph) {
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return null;
    const link = element("a", "resource-link");
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    if (glyph) link.append(icon(glyph));
    link.append(document.createTextNode(label));
    return link;
  } catch { return null; }
}

function selectResourceTab(name, focus=false) {
  resourceTab = name;
  document.querySelectorAll("[data-resource-tab]").forEach(button => {
    const selected = button.dataset.resourceTab === name;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    if (selected && focus) button.focus();
  });
  $("references-panel").hidden = name !== "references";
  $("tools-panel").hidden = name !== "tools";
}

function renderResources(data) {
  const references = data.project?.references;
  const papers = references?.items || [];
  $("reference-count").textContent = String(papers.length);
  $("references-note").textContent = references?.note || "选择研究项目后，在这里查看参考论文与代码。";
  $("references-list").replaceChildren(...papers.map(paper => {
    const row = element("article", "reference-row");
    const content = element("div", "reference-content");
    const heading = element("h3", "reference-title", paper.title);
    const meta = element("div", "reference-meta");
    if (paper.venue) meta.append(element("span", "reference-venue", paper.venue));
    if (paper.date) meta.append(element("span", "", paper.date));
    if (paper.license) meta.append(element("span", "", `代码许可：${paper.license}`));
    content.append(heading, meta);
    if (paper.code_url) {
      const repository = element("div", "reference-repository");
      const link = resourceLink(paper.code_url.replace(/^https?:\/\//, ""), paper.code_url, "code");
      if (link) repository.append(link);
      if (paper.commit) repository.append(element("span", "reference-commit mono", paper.commit.slice(0, 12)));
      content.append(repository);
    }
    const actions = element("div", "reference-actions");
    const links = [resourceLink(paper.pdf_url ? "论文 PDF" : "论文原文", paper.pdf_url || paper.paper_url, "file"),
      resourceLink("GitHub", paper.code_url, "code"), resourceLink("发表页面", paper.venue_url)];
    links.filter(Boolean).forEach(link => actions.append(link));
    row.append(content, actions);
    return row;
  }));
  const inventory = data.tools?.items || [];
  $("tool-count").textContent = String(inventory.length);
  $("tools-note").textContent = data.tools?.note || "尚未读取到本机工具信息。";
  const kinds = {skill:"Skill",mcp:"MCP",plugin:"插件",runtime:"执行与绘图",model:"外部模型"};
  const visible = inventory.filter(tool => toolFilter === "all" || tool.kind === toolFilter || (toolFilter === "runtime" && tool.kind === "model"));
  $("tool-grid").replaceChildren(...visible.map(tool => {
    const card = element("article", "tool-card");
    card.dataset.toolKind = tool.kind;
    const heading = element("div", "tool-heading");
    heading.append(element("h3", "", tool.title), element("span", `tool-status${tool.status.includes("未") ? " undetected" : ""}`, tool.status));
    card.append(element("div", "tool-kind", `${kinds[tool.kind] || "工具"} · ${tool.id}`), heading,
      element("p", "tool-description", tool.description), element("p", "tool-evidence", tool.evidence));
    return card;
  }));
  document.querySelectorAll("[data-tool-filter]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.toolFilter === toolFilter)));
  selectResourceTab(resourceTab);
}

function render(data) {
  current = data;
  const picker = $("project");
  picker.replaceChildren();
  data.projects.forEach(project => {
    const option = element("option", "", project.title);
    option.value = project.id;
    picker.append(option);
  });
  picker.disabled = !data.projects.length;
  const project = data.project || {id:"", execution_state:"", artifact_kinds:{}, job_counts:{}, jobs_total:0, running_jobs:0,
    artifact_count:0, outputs:[], reviews:[], activity:[], jobs:[], task_counts:{}};
  if (!data.project) {
    picker.append(element("option", "", "等待第一个研究项目"));
    showNotice("还没有研究项目。打开使用指南，复制开始研究模板，在研究对话中填入你的方向即可。");
  } else {
    picker.value = project.id;
  }
  $("project-id").textContent = project.id || "尚无项目";
  const state = $("project-state");
  state.className = "state-tag";
  state.textContent = !project.id ? "等待开始" : project.execution_state === "paused" ? "已暂停" : "研究中";
  if (project.execution_state === "paused") state.classList.add("paused");
  if (project.evidence_kind === "toolchain_test") {state.textContent = "工具验收"; state.classList.add("test");}
  number("stat-total", project.jobs_total);
  number("stat-completed", project.job_counts.completed || 0);
  number("stat-artifacts", project.artifact_count);
  number("stat-running", project.running_jobs);
  $("running-note").textContent = project.running_jobs ? "含排队与评价" : "暂无运行记录";
  renderProgress(project);
  renderRuns(project);
  renderOutputs(project);
  renderReviews(project);
  renderActivity(project);
  renderResources(data);
  $("sync-time").textContent = `公开合成示例 · 非真实科研结果`;
}

async function load(projectId) {
  const thisRequest = ++requestId;
  activeRequest?.abort();
  const controller = new AbortController();
  activeRequest = controller;
  const timer = setTimeout(() => controller.abort(), 10000);
  $("main").setAttribute("aria-busy", "true");
  $("refresh").classList.add("loading");
  $("refresh").disabled = true;
  const requested = projectId || new URLSearchParams(location.search).get("project");
  const query = requested ? `?${new URLSearchParams({project:requested})}` : "";
  try {
    const response = await fetch(document.documentElement.dataset.demo === "true" ? "./demo-data.json" : `/api/snapshot${query}`, {signal:controller.signal, cache:"no-store"});
    if (!response.ok) throw new Error("读取失败");
    const data = await response.json();
    if (thisRequest !== requestId) return;
    showNotice("");
    document.body.classList.remove("offline");
    render(data);
    if (data.project) history.replaceState(null, "", `?${new URLSearchParams({project:data.project.id})}${location.hash}`);
  } catch (error) {
    if (thisRequest !== requestId) return;
    document.body.classList.add("offline");
    showNotice(current ? "暂时未能刷新，当前保留上次读取的记录。请稍后再试。" : "暂时无法读取项目记录，请检查展示台服务后刷新。");
    if (current?.project) $("project").value = current.project.id;
    $("sync-time").textContent = current ? `上次读取 · ${date(current.generated_at)} · 刷新未成功` : "尚未连接到项目记录";
  } finally {
    clearTimeout(timer);
    if (thisRequest === requestId) {
      $("main").setAttribute("aria-busy", "false");
      $("refresh").classList.remove("loading");
      $("refresh").disabled = false;
    }
  }
}

$("project").addEventListener("change", event => load(event.target.value));
$("refresh").addEventListener("click", () => load(current?.project?.id));
$("about").addEventListener("click", () => $("about-dialog").showModal());
$("close-about").addEventListener("click", () => $("about-dialog").close());
$("about-dialog").addEventListener("click", event => {if (event.target === $("about-dialog")) {const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close();}});

function jumpToCurrent() {
  const focus = current?.project?.progress?.focus_stage;
  if (!focus) return;
  setNavigation("#research-progress");
  renderStage(focus);
  $("progress-detail").scrollIntoView({behavior:reducedMotion.matches ? "instant" : "smooth", block:"start"});
  $("stage-title").setAttribute("tabindex", "-1");
  $("stage-title").focus({preventScroll:true});
}
function setNavigation(href) {
  document.querySelectorAll(".nav a").forEach(link => link.classList.toggle("nav-active", link.getAttribute("href") === href));
}
document.querySelectorAll(".nav a").forEach(link => link.addEventListener("click", () => setNavigation(link.getAttribute("href"))));
document.querySelectorAll("[data-resource-tab]").forEach(button => {
  button.addEventListener("click", () => selectResourceTab(button.dataset.resourceTab));
  button.addEventListener("keydown", event => {
    if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? "references" : event.key === "End" ? "tools" : resourceTab === "references" ? "tools" : "references";
    selectResourceTab(next, true);
  });
});
document.querySelectorAll("[data-tool-filter]").forEach(button => button.addEventListener("click", () => {
  toolFilter = button.dataset.toolFilter;
  renderResources(current || {});
}));
function researchCommand() {
  const project = document.documentElement.dataset.demo === "true" ? null : current?.project;
  if (!project) return "我想开展一个研究项目。研究领域：[填写领域]。目标与要求：[填写研究问题、可用算力和预期成果]。请先核查已有项目，再从选题、论文与官方代码筛选开始，给出可推进的实验方案；按真实记录更新研究进展。";
  if (project.execution_state === "paused") return `请查看 ai2research 项目“${project.title}”（${project.id}）的进展，保留暂停状态，先总结已有成果、缺项与下一步，不启动新的实验。`;
  const focus = project.progress.stages.find(s => s.id === project.progress.focus_stage);
  return `继续 ai2research 项目“${project.title}”（${project.id}）。请先核对已有状态。当前重点：${focus?.title || "研究进展"}；${focus?.summary || ""}。接下来：${project.progress.next_action}请沿用已有授权和预算，保留已登记的作业与证据，完成后更新真实研究进展。`;
}
async function copyResearchCommand() {
  const text = researchCommand();
  try {
    await navigator.clipboard.writeText(text);
    clearTimeout(toastTimer);
    $("toast").textContent = "指令已复制，粘贴到研究对话即可继续。";
    $("toast").hidden = false;
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4000);
  } catch {
    $("command-text").value = text;
    $("copy-dialog").showModal();
    $("command-text").focus();
    $("command-text").select();
  }
}
$("goto-current").addEventListener("click", jumpToCurrent);
$("jump-current").addEventListener("click", jumpToCurrent);
document.querySelectorAll(".guide-trigger").forEach(button => button.addEventListener("click", () => $("guide-dialog").showModal()));
document.querySelectorAll(".dialog-close").forEach(button => button.addEventListener("click", () => $(button.dataset.dialog).close()));
$("guide-start").addEventListener("click", () => {
  $("guide-dialog").close();
  if (current?.project) jumpToCurrent();
  else copyResearchCommand();
});
$("copy-current").addEventListener("click", copyResearchCommand);
$("copy-guide").addEventListener("click", copyResearchCommand);
$("present").addEventListener("click", async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch { document.body.classList.toggle("screen-focus"); }
});
document.addEventListener("fullscreenchange", () => {document.body.classList.toggle("screen-focus", Boolean(document.fullscreenElement)); $("present").setAttribute("aria-label", document.fullscreenElement ? "退出全屏展示" : "全屏展示");});
load();

// Only synchronize visible local records; no remote polling or model calls.
setInterval(() => {
  if (!document.hidden && $("main").getAttribute("aria-busy") === "false" && !document.querySelector("dialog[open]")) load(current?.project?.id);
}, 30000);
