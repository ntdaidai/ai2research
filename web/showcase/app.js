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
let resourceTab = "tools";
let toolFilter = "all";
let resourcePhase = "research";
let resourceToolLimit = 18;
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
  $("progress-caption").textContent = progress ? "选择环节查看完成依据。" : "尚无研究项目。";
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
  renderJourney(project);
}

function renderJourney(project) {
  const progress = project.progress;
  const completed = progress?.completed_stages || 0;
  const total = progress?.total_stages || 12;
  $("hero-progress").textContent = progress ? `${completed} / ${total}` : "待开始";
  $("hero-progress-label").textContent = progress ? "环节已完成" : "尚无项目";
  const finished = progress && completed === total;
  $("overview-status").textContent = finished ? "全过程已完成" : progress?.paused ? "研究已暂停" : progress ? `${completed} 个环节已完成` : "等待开始";
  $("overview-caption").textContent = finished ? "实验与稿件双审通过，正式成果已交付。" : progress?.next_action || "在研究对话中给出方向或论文以创建项目。";
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
  const accepted = project.progress?.stages.find(s => s.id === "manuscript_review")?.state === "completed";
  const labels = {paper:[accepted ? "论文终稿" : "论文草稿","file"], diagram:["方法结构图","node"], figure:["实验图表","layers"]};
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
  const finished = project.progress?.completed_stages === project.progress?.total_stages && Boolean(project.progress);
  $("copy-label").textContent = project.id ? (finished ? "复制项目交接指令" : paused ? "复制查看进展指令" : "复制继续研究指令") : "复制开始研究指令";
  const rows = $("review-summary");
  rows.replaceChildren();
  const stageLabels = {experiment:"实验评审", manuscript:"稿件评审", framework:"框架评审"};
  const reviewLabels = {revision_requested:"有修订意见", awaiting_reviews:"等待评审", reviews_recorded:"意见已留存"};
  project.reviews.slice(0, 2).forEach(record => {
    const accepted = project.progress?.stages.find(s => s.id === `${record.stage}_review`)?.state === "completed";
    const row = element("div", "review-row");
    row.append(element("span", "", stageLabels[record.stage] || "评审记录"),
      element("span", `review-status${accepted ? " accepted" : record.recorded_status === "revision_requested" ? "" : " neutral"}`, accepted ? "已验收" : reviewLabels[record.recorded_status] || "待核对"));
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
    item.append(time, element("h3", "", `${entry.category} · ${entry.action}`));
    if (entry.message) {
      item.append(element("p", "", entry.message));
      item.title = entry.message;
    }
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
  document.body.dataset.resourceTab = name;
  document.querySelectorAll("[data-resource-tab]").forEach(button => {
    const selected = button.dataset.resourceTab === name;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    if (selected && focus) button.focus();
  });
  $("references-panel").hidden = name !== "references";
  $("tools-panel").hidden = name !== "tools";
  $("open-panel").hidden = name !== "open";
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
  const inventory = assistCatalog().filter(tool=>tool.kind!=="resource");
  $("tool-count").textContent = String(inventory.length);
  $("tools-note").textContent = "";
  const kinds = assistKinds;
  const query=$("resource-search").value.trim().toLocaleLowerCase();
  const visible = inventory.filter(tool => (toolFilter === "all" || tool.kind === toolFilter || (toolFilter === "runtime" && tool.kind === "model")) && (resourcePhase==="all" || tool.stages.some(id=>assistSteps.find(s=>s.id===id)?.phase===resourcePhase)) && `${tool.title} ${tool.description}`.toLocaleLowerCase().includes(query));
  $("tool-grid").replaceChildren(...visible.slice(0,resourceToolLimit).map(tool => {
    const card = element("article", "tool-card");
    card.dataset.toolKind = tool.kind;
    card.dataset.phases = [...new Set(tool.stages.map(id=>assistSteps.find(s=>s.id===id)?.phase))].join(" ");
    const heading = element("div", "tool-heading");
    heading.append(element("h3", "", tool.title));
    card.append(element("div", "tool-kind", kinds[tool.kind] || "工具"), heading,
      element("p", "tool-description", tool.description));
    const sources = element("div", "tool-sources");
    for (const source of tool.links || []) {
      const link = resourceLink(source.label, source.url, source.label === "GitHub" ? "code" : "book");
      if (link) {
        link.setAttribute("aria-label", `${tool.title} · ${source.label}，新窗口`);
        sources.append(link);
      }
    }
    const choose = element("button", "text-button", assistDraft.selected.includes(tool.id) ? "已选择" : "选择");
    choose.type="button"; choose.addEventListener("click",()=>{toggleAssistTool(tool.id,!assistDraft.selected.includes(tool.id));renderResources(current || {});});
    sources.append(choose);
    card.append(sources);
    return card;
  }));
  $("tools-show-more").hidden=visible.length<=resourceToolLimit;
  $("tools-show-more").textContent=`查看更多工具（还有 ${Math.max(0,visible.length-resourceToolLimit)} 项）`;
  renderOpenLibrary();
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
    showNotice("");
  } else {
    picker.value = project.id;
  }
  $("project-id").textContent = project.id || "尚无项目";
  const state = $("project-state");
  state.className = "state-tag";
  const finished = project.progress && project.progress.completed_stages === project.progress.total_stages;
  state.textContent = !project.id ? "等待开始" : project.execution_state === "paused" ? "已暂停" : finished ? "已交付" : "研究中";
  if (project.execution_state === "paused") state.classList.add("paused");
  if (project.evidence_kind === "toolchain_test") {state.textContent = "工具验收"; state.classList.add("test");}
  number("stat-total", project.jobs_total);
  number("stat-completed", project.job_counts.completed || 0);
  number("stat-artifacts", project.artifact_count);
  number("stat-running", project.running_jobs);
  $("running-note").textContent = project.running_jobs ? "含排队与评价" : "暂无运行任务";
  renderProgress(project);
  renderRuns(project);
  renderOutputs(project);
  renderReviews(project);
  renderActivity(project);
  renderResources(data);
  updateAssistContext();
  $("sync-time").textContent = `本地记录 · ${date(data.generated_at)} · 每30秒同步`;
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
    const response = await fetch(`/api/snapshot${query}`, {signal:controller.signal, cache:"no-store"});
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
  location.hash = "progress";
  setNavigation();
  renderStage(focus);
  $("progress-detail").scrollIntoView({behavior:reducedMotion.matches ? "instant" : "smooth", block:"start"});
  $("stage-title").setAttribute("tabindex", "-1");
  $("stage-title").focus({preventScroll:true});
}
function setNavigation() {
  const aliases = {"research-progress":"progress", outputs:"results", experiments:"results"};
  let page = location.hash.slice(1) || "workspace";
  page = aliases[page] || page;
  if (!["workspace", "assist", "overview", "progress", "results", "resources"].includes(page)) page = "workspace";
  document.body.dataset.view = page;
  if(page === "resources") renderResources(current || {});
  document.querySelectorAll("[data-page]").forEach(view => { view.hidden = view.dataset.page !== page; });
  document.querySelectorAll(".nav a").forEach(link => {
    const active = link.hash === `#${page}`;
    link.classList.toggle("nav-active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const heading = document.querySelector(`[data-page="${page}"] h1`);
  document.title = `${heading.textContent} · ai2research`;
  window.scrollTo({top:0, behavior:"instant"});
}
window.addEventListener("hashchange", setNavigation);
document.querySelectorAll("[data-resource-tab]").forEach(button => {
  button.addEventListener("click", () => selectResourceTab(button.dataset.resourceTab));
  button.addEventListener("keydown", event => {
    if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const tabs=["references","tools","open"];
    const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[2] : tabs[(tabs.indexOf(resourceTab)+(event.key==="ArrowRight"?1:2))%3];
    selectResourceTab(next, true);
  });
});
document.querySelectorAll("[data-tool-filter]").forEach(button => button.addEventListener("click", () => {
  toolFilter = button.dataset.toolFilter;
  resourceToolLimit = 18;
  renderResources(current || {});
}));
function researchCommand() {
  const project = current?.project;
  if (!project) return "我想开展一个研究项目。研究领域：[填写领域]。目标与要求：[填写研究问题、可用算力和预期成果]。请先核查已有项目，再从选题、论文与官方代码筛选开始，给出可推进的实验方案；按真实记录更新研究进展。";
  if (project.execution_state === "paused") return `请查看 ai2research 项目“${project.title}”（${project.id}）的进展，保留暂停状态，先总结已有成果、缺项与下一步，不启动新的实验。`;
  if (project.progress?.completed_stages === project.progress?.total_stages) return `请查看已交付的 ai2research 项目“${project.title}”（${project.id}），核对当前正式交付和双审记录，提供成果与复现材料的位置。保留已有研究和历史任务记录，无需重新开展实验。`;
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
$("jump-current").addEventListener("click", jumpToCurrent);
document.querySelectorAll(".guide-trigger").forEach(button => button.addEventListener("click", () => $("guide-dialog").showModal()));
document.querySelectorAll(".dialog-close").forEach(button => button.addEventListener("click", () => $(button.dataset.dialog).close()));
$("copy-current").addEventListener("click", copyResearchCommand);
$("present").addEventListener("click", async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch { document.body.classList.toggle("screen-focus"); }
});
document.addEventListener("fullscreenchange", () => {document.body.classList.toggle("screen-focus", Boolean(document.fullscreenElement)); $("present").setAttribute("aria-label", document.fullscreenElement ? "退出全屏展示" : "全屏展示");});
// The assisted branch composes a local task; execution stays in the user's Agent.
const assistSteps = [
  {id:"survey", title:"调研", phase:"research", output:"文献清单、原文证据与研究问题"},
  {id:"screen", title:"筛选", phase:"research", output:"候选对照、基线代码与方法选择理由"},
  {id:"experiment", title:"跑实验", phase:"implement", output:"实验方案、基线结果、代码版本与运行记录"},
  {id:"iterate", title:"迭代", phase:"implement", output:"受控改进、对照与消融，保留负结果"},
  {id:"figure", title:"出图", phase:"produce", output:"来自真实结果的图表、数据与生成脚本"},
  {id:"draft", title:"出稿", phase:"produce", output:"与证据一致的论文稿、引用与源码"},
  {id:"polish", title:"润色", phase:"produce", output:"保留事实和术语的修订稿与修改说明"}
];
const assistPhases = {research:"调研", implement:"实现", produce:"产出"};
const assistKinds = {skill:"Skill", mcp:"MCP", experiment:"实验工具包", runtime:"执行与绘图", plugin:"应用与插件", model:"评审模型",resource:"论文 / 代码 / 数据"};
const assistDomains = window.AI2RESEARCH_DOMAINS || {general:"通用科研"};
const researchLibrary = window.AI2RESEARCH_LIBRARY || [];
const assistMapping = {
  "ai2research":["survey","screen","experiment","iterate","figure","draft","polish"],
  "research-workflow":["survey","screen","experiment","iterate","figure","draft","polish"],
  "nature-polishing":["polish"], "anti-defensive-writing":["draft","polish"],
  "drawio-skill":["figure"], "ai2research-mcp":["survey","screen","experiment","iterate","figure","draft"],
  "arxiv-mcp":["survey","screen"], sites:["figure"], visualize:["figure"], pages:["survey","draft"],
  tectonic:["draft","polish"], drawio:["figure"], matplotlib:["figure"], "external-review":["screen","iterate","draft","polish"]
};
const assistCalls = {
  "ai2research-mcp":"由已连接的 ai2research MCP 调用所需入口；先读取项目状态，按所选环节工作。",
  "arxiv-mcp":"使用论文检索适配，检索、下载和读取原文，记录原文位置及代码来源。",
  "research-workflow":"先读取 research-workflow 的 SKILL.md，再按当前环节加载对应参考。",
  "ai2research":"先读取 ai2research 的 SKILL.md 与项目交接；复用已有项目、作业与证据。",
  "nature-polishing":"读取 nature-polishing 的 SKILL.md；保留事实、证据边界、术语与引用。",
  "anti-defensive-writing":"读取 anti-defensive-writing 的 SKILL.md，精简重复与防御性表达。",
  "drawio-skill":"读取 drawio-skill 的 SKILL.md，保留可编辑 .drawio 源文件。",
  matplotlib:"从已核实的实验结果生成图表，同时保存数据与复现脚本。",
  tectonic:"将已有 LaTeX 源码编译成 PDF，保留源码与编译日志。",
  drawio:"导出可编辑结构图与矢量图，检查导出结果。",
  "external-review":"核对评审模型和现有调用预算，按已有授权进行实质审查，保存真实回执。"
};
const assistBundles = [
  {id:"experiment-kit", kind:"experiment", title:"实验运行与结果回收", description:"冻结方案、提交实验、查询状态、回收结果，把代码与指标留在同一条证据链。", stages:["experiment"], status:"项目工具包", evidence:"已有 CLI / MCP 入口；由 Agent 检查运行环境与资源。", links:[{label:"GitHub",url:"https://github.com/ntdaidai/ai2research/blob/main/tools/harness/experiments.py"}], usage:"通过 ai2research 的 prepare_experiment、submit_experiment、get_job_status、collect_job_results 执行。先核对正式实验授权、资源与评价方案，记录真实 job ID，不重复提交已有任务。"},
  {id:"iteration-kit", kind:"experiment", title:"对照、消融与迭代", description:"对比已有实验，整理消融矩阵，在同一评价协议下验证你的下一步想法。", stages:["iterate"], status:"项目工具包", evidence:"已有结果比较与证据整理入口；不自动运行新实验。", links:[{label:"GitHub",url:"https://github.com/ntdaidai/ai2research/blob/main/tools/harness/evidence.py"}], usage:"使用 compare_jobs、attach_job_evidence、build_experiment_matrix 整理真实结果；新方法先明确变动、对照、消融及停止条件，再在已有授权范围内实施。"},
  {id:"publication-kit", kind:"mcp", title:"论文组织与稿件编译", description:"组织逐章写作任务、汇总稿件并编译，把结果、图表和引用带入论文。", stages:["draft","polish"], status:"项目工具包", evidence:"已有 publication_plan、assemble_manuscript、compile_paper 入口。", links:[{label:"GitHub",url:"https://github.com/ntdaidai/ai2research/blob/main/tools/harness/publication.py"}], usage:"通过 ai2research 的 publication_plan、assemble_manuscript、compile_paper 整理稿件；先检查实验与引用证据，缺失结果保留待补标记。"}
];
const assistStorageKey = "ai2research.assisted-task.v1";
const assistFields = {title:"assist-title-input",idea:"assist-idea",start:"assist-start",budget:"assist-budget",materials:"assist-materials",intervention:"assist-intervention",deliverable:"assist-deliverable"};
let assistPhase = "research";
let assistToolLimit = 12;
let assistDraft = {title:"",idea:"",start:"survey",budget:"",materials:"",intervention:"",deliverable:"",domain:"all",attach:false,selected:[],checkpoints:["screen","experiment","draft"],custom:[]};
let assistStorageAvailable = true;
function safeAssistURL(value) {
  try { const url = new URL(value); return ["http:","https:"].includes(url.protocol) && !url.username && !url.password ? url.href : ""; } catch { return ""; }
}
function loadAssistDraft() {
  try {
    const saved = JSON.parse(localStorage.getItem(assistStorageKey) || "null");
    if (!saved || typeof saved !== "object") return;
    for (const name of Object.keys(assistFields)) if (typeof saved[name] === "string") assistDraft[name] = saved[name].slice(0, $(assistFields[name]).maxLength > 0 ? $(assistFields[name]).maxLength : 12000);
    if (!assistSteps.some(s=>s.id===assistDraft.start)) assistDraft.start = "survey";
    assistDraft.attach = saved.attach === true;
    if(saved.domain==="all" || assistDomains[saved.domain])assistDraft.domain=saved.domain;
    if (Array.isArray(saved.selected)) assistDraft.selected = [...new Set(saved.selected.filter(s=>typeof s==="string"))].slice(0,200);
    if (Array.isArray(saved.checkpoints)) assistDraft.checkpoints = saved.checkpoints.filter(s=>assistSteps.some(step=>step.id===s));
    if (Array.isArray(saved.custom)) assistDraft.custom = saved.custom.filter(t=>t && typeof t.id==="string" && t.id.startsWith("custom-") && typeof t.title==="string" && typeof t.description==="string" && safeAssistURL(t.links?.[0]?.url) && assistKinds[t.kind] && Array.isArray(t.stages) && t.stages.length && t.stages.every(s=>assistSteps.some(step=>step.id===s))).slice(0,100).map(t=>({id:t.id,kind:t.kind,title:t.title.slice(0,100),description:t.description.slice(0,2000),stages:t.stages,links:[{label:"文档 / 仓库",url:safeAssistURL(t.links[0].url)}],status:"自定义 · 待检查",evidence:"用户提供；Agent 使用前检查连接与调用方式。",usage:t.description.slice(0,2000)}));
  } catch { assistStorageAvailable = false; }
}
function saveAssistDraft() {
  $("assist-validation").hidden = true;
  try { localStorage.setItem(assistStorageKey, JSON.stringify(assistDraft)); assistStorageAvailable = true; }
  catch { assistStorageAvailable = false; }
  $("assist-save-state").textContent = assistStorageAvailable ? "草稿已保存" : "请下载保存草稿";
}
function assistCatalog() {
  const inventory = (current?.tools?.items || []).map(t=>({...t,stages:assistMapping[t.id] || assistSteps.map(s=>s.id),usage:assistCalls[t.id] || `按 ${t.title} 的官方说明或技能入口使用，检查连接与所需环境。`}));
  const featured=["research-workflow","lib-elicit","lib-connectedpapers","lib-github-mcp","lib-context7","experiment-kit","lib-mlflow","lib-optuna","matplotlib","publication-kit","lib-overleaf","lib-scientific-skills"];
  const rank=id=>{const index=featured.indexOf(id);return index<0?featured.length:index;};
  return [...inventory, ...assistBundles, ...researchLibrary, ...assistDraft.custom].sort((a,b)=>rank(a.id)-rank(b.id));
}
function selectedAssistTools() {
  const catalog = assistCatalog();
  return assistDraft.selected.map(id=>catalog.find(t=>t.id===id) || {id,title:`未找到的工具：${id}`,kind:"runtime",stages:[],missing:true});
}
function toggleAssistTool(id, selected) {
  assistDraft.selected = selected ? [...new Set([...assistDraft.selected,id])] : assistDraft.selected.filter(s=>s!==id);
  saveAssistDraft(); renderAssistTools();
}
function matchesDomain(tool,domain) {
  const domains=tool.domains || ["general"];
  return domain==="all" || domains.includes(domain) || domains.includes("general");
}
function fillDomainSelect(id,includeAll=true) {
  const select=$(id);
  select.replaceChildren();
  if(includeAll){const option=element("option","","全部领域");option.value="all";select.append(option);}
  Object.entries(assistDomains).forEach(([value,title])=>{const option=element("option","",title);option.value=value;select.append(option);});
}
function renderOpenLibrary() {
  const resources=researchLibrary.filter(t=>t.kind==="resource");
  const domain=$("open-domain").value || "all";
  const query=$("open-search").value.trim().toLocaleLowerCase();
  const filtered=resources.filter(t=>matchesDomain(t,domain) && `${t.title} ${t.description}`.toLocaleLowerCase().includes(query));
  $("open-count").textContent=String(resources.length);
  $("open-empty").hidden=filtered.length>0;
  $("open-library").replaceChildren(...filtered.map(tool=>{
    const card=element("article","tool-card open-resource-card");card.dataset.resourceId=tool.id;
    card.append(element("h3","",tool.title),element("p","tool-description",tool.description));
    const sources=element("div","tool-sources");
    tool.links.forEach(source=>{const link=resourceLink(source.label,source.url,"book");if(link)sources.append(link);});
    const chosen=assistDraft.selected.includes(tool.id);
    const button=element("button","text-button",chosen?"已选择":"选择");button.type="button";
    button.addEventListener("click",()=>{toggleAssistTool(tool.id,!assistDraft.selected.includes(tool.id));renderOpenLibrary();});
    sources.append(button);card.append(sources);return card;
  }));
}
function applyAssistPreset(name) {
  const domain=$("starter-domain").value;
  const label=assistDomains[domain];
  const domainSources={general:["lib-arxiv","lib-github-topics"],cv:["lib-cvf","lib-openmmlab"],nlp:["lib-acl","lib-sentence-transformers"],llm:["lib-hf-papers","lib-hf-models"],fl:["lib-flower","lib-fedml"],systems:["lib-usenix","lib-artifact-eval"],rl:["lib-gymnasium","lib-lerobot"],data:["lib-openml","lib-uci"],bio:["lib-pubmed","lib-scverse"]};
  const presets={
    discover:{title:`${label} · 发现研究问题`,start:"survey",idea:`我目前还没有明确的 idea，希望围绕${label}寻找适合开展的研究问题。请先调研相关论文与开源实现，比较研究空缺、复现成本和可用资源，提出三个可验证的候选想法及其依据，等我选择后再进入实验。`,tools:["research-workflow","arxiv-mcp","lib-connectedpapers","lib-zotero"],deliverable:"文献与开源代码清单、三个候选研究问题、可行性对照及建议下一步。"},
    validate:{title:`${label} · 验证我的 idea`,start:"screen",idea:`我在${label}领域已经有一个想法，希望先梳理相关工作，确认问题和差异，再制定基线、对照、消融与资源方案。我会补充具体假设和已有材料，请先列出需要补充的关键信息。`,tools:["research-workflow","lib-github-mcp","experiment-kit","iteration-kit","lib-mlflow"],deliverable:"相关工作对照、可执行实验方案、baseline与消融记录、可复现结果和结论。"},
    publish:{title:`${label} · 整理研究成果`,start:"figure",idea:`我在${label}领域已有实验结果，希望整理成图表和论文。请先盘点结果、日志、方法代码与引用证据，给出图表和章节安排，再按证据完成写作与润色。`,tools:["matplotlib","drawio-skill","publication-kit","nature-polishing","lib-overleaf"],deliverable:"有数据依据的结果图、可编辑方法图、论文章节与引用、论文PDF和必要源码。"}
  };
  const preset=presets[name];if(!preset)return;
  document.querySelectorAll("[data-assist-preset]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.assistPreset===name)));
  const hadIdea=Boolean(assistDraft.idea.trim());
  for(const key of ["title","idea","deliverable"])if(!assistDraft[key].trim()){assistDraft[key]=preset[key];$(assistFields[key]).value=preset[key];}
  if(!hadIdea){assistDraft.start=preset.start;$("assist-start").value=preset.start;}
  assistDraft.domain=domain;$("assist-domain").value=domain;
  assistDraft.selected=[...new Set([...assistDraft.selected,...preset.tools,...(domainSources[domain] || [])])];
  saveAssistDraft();renderAssistCheckpoints();assistPhase=assistSteps.find(s=>s.id===preset.start).phase;assistToolLimit=12;$("assist-search").value="";$("assist-kind").value="all";renderAssistTools();
  $("starter-feedback").textContent=`已选择${name==="discover"?"调研":name==="validate"?"验证":"产出"}工具组合。`;
  $("starter-feedback").hidden=false;
}
function renderAssistTools() {
  const catalog = assistCatalog();
  const query = $("assist-search").value.trim().toLocaleLowerCase();
  const kind = $("assist-kind").value;
  const domain=$("assist-domain").value;
  const visible = catalog.filter(t=>(assistPhase==="all" || t.stages.some(id=>assistSteps.find(s=>s.id===id)?.phase===assistPhase)) && (kind==="all" || t.kind===kind) && matchesDomain(t,domain) && `${t.title} ${t.id} ${t.description}`.toLocaleLowerCase().includes(query));
  $("assist-catalog-count").textContent = `${visible.length} / ${catalog.length} 项`;
  $("assist-show-more").hidden=visible.length<=assistToolLimit;
  $("assist-show-more").textContent=`查看更多工具与资源（还有 ${Math.max(0,visible.length-assistToolLimit)} 项）`;
  $("assist-no-tools").hidden = visible.length > 0;
  $("assist-catalog").replaceChildren(...visible.slice(0,assistToolLimit).map(tool=>{
    const selected = assistDraft.selected.includes(tool.id);
    const card = element("article", `assist-tool-card${selected ? " is-selected" : ""}`);
    card.dataset.assistTool = tool.id;
    card.dataset.phases = [...new Set(tool.stages.map(id=>assistSteps.find(s=>s.id===id)?.phase))].join(" ");
    const heading = element("label", "assist-tool-heading");
    const checkbox = document.createElement("input"); checkbox.type="checkbox"; checkbox.checked=selected; checkbox.setAttribute("aria-label",`选用 ${tool.title}`);
    checkbox.addEventListener("change", ()=>{toggleAssistTool(tool.id,checkbox.checked); const replacement = [...$("assist-catalog").querySelectorAll("[data-assist-tool]")].find(n=>n.dataset.assistTool===tool.id)?.querySelector("input"); replacement?.focus({preventScroll:true});});
    heading.append(element("h3","",tool.title), checkbox);
    card.append(heading,element("p","assist-tool-description",tool.description));
    const links = element("div","tool-sources");
    (tool.links || []).forEach(source=>{const link=resourceLink(source.label,source.url,"code"); if(link)links.append(link);});
    if(tool.id.startsWith("custom-")) {
      const remove = element("button","text-button","删除自定义工具"); remove.type="button";
      remove.addEventListener("click",()=>{assistDraft.custom=assistDraft.custom.filter(t=>t.id!==tool.id);if($("assist-search").value===tool.title)$("assist-search").value="";toggleAssistTool(tool.id,false);}); links.append(remove);
    }
    const footer=element("div","tool-footer");footer.append(element("span","tool-kind",assistKinds[tool.kind] || "工具"),links);
    card.append(footer); return card;
  }));
  $("assist-selected-count").textContent = String(assistDraft.selected.length);
  const picks = selectedAssistTools();
  $("assist-selected").replaceChildren(...(picks.length ? picks.map(tool=>{
    const row=element("div","selected-tool"); const description=element("div");
    description.append(element("strong","",tool.title));
    const remove=element("button","icon-button");remove.type="button";remove.setAttribute("aria-label",`移除 ${tool.title}`);remove.append(icon("close"));remove.addEventListener("click",()=>toggleAssistTool(tool.id,false));row.append(description,remove);return row;
  }) : [element("p","selected-empty","选择工具加入此处。") ]));
  document.querySelectorAll("[data-assist-phase]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.assistPhase===assistPhase)));
}
function renderAssistCheckpoints() {
  const start = assistSteps.findIndex(s=>s.id===assistDraft.start);
  $("assist-checkpoints").replaceChildren(...assistSteps.map((step,index)=>{
    const row=element("label",`check-row${index<start ? " inactive-step" : ""}`);
    const check=document.createElement("input");check.type="checkbox";check.value=step.id;check.checked=assistDraft.checkpoints.includes(step.id);check.disabled=index<start;
    check.addEventListener("change",()=>{assistDraft.checkpoints=check.checked ? [...new Set([...assistDraft.checkpoints,step.id])] : assistDraft.checkpoints.filter(s=>s!==step.id);saveAssistDraft();});
    row.title=index<start ? "不在本轮范围" : "进入本环节前确认";
    row.append(check,element("span","",step.title));return row;
  }));
}
function updateAssistContext() {
  const project=current?.project;
  const catalog=assistCatalog().filter(t=>!t.id.startsWith("custom-"));
  $("library-total").textContent=`${catalog.length} 项`;
  document.body.classList.toggle("assist-attached",assistDraft.attach);
  $("assist-attach").disabled=!project;
  $("assist-project-label").textContent=project ? `${project.title}（${project.id}）${project.execution_state==="paused" ? " · 已暂停" : ""}` : "当前没有项目，可创建独立任务文档";
  renderAssistTools();
}
function assistDocument() {
  const tools = selectedAssistTools();
  const start = assistSteps.findIndex(s=>s.id===assistDraft.start);
  const scope = assistSteps.slice(start);
  const project = assistDraft.attach ? current?.project : null;
  const lines = ["# Agent 科研任务文档", "", `任务：${assistDraft.title.trim() || "辅助科研任务"}`, "模式：辅助科研（人主导，Agent 按所选工具与本轮范围协作）", "", "## 研究问题与 idea", assistDraft.idea.trim(), "", "## 项目与上下文"];
  if(project) {
    lines.push(`关联项目：${project.title}（${project.id}）`, `当前执行状态：${project.execution_state || "未记录"}`, "先读取已有项目状态、交接、作业与证据，从当前记录接续，避免重复运行。");
    if(project.execution_state==="paused")lines.push("该项目已暂停。本轮只整理方案与已有材料；保持暂停，不提交实验。恢复执行须由用户明确要求。");
    else lines.push("关联现有项目不表示扩展已有实验授权；沿用已有预算、固定评价与暂停约束。");
  } else lines.push("独立辅助科研任务。先核查是否已有同题项目；本任务未指定恢复或更改任何现有项目。");
  lines.push(`研究领域：${assistDomains[assistDraft.domain] || "按研究问题确定"}`, "", "已有材料：",assistDraft.materials.trim() || "尚未提供。先盘点可用论文、代码、数据与实验记录，缺项集中反馈。", "", "## 资源与边界",assistDraft.budget.trim() || "尚未指定实验资源与预算。先给出具体方案和资源需求，待明确实验范围后执行。", "", "## 本轮人工干预",assistDraft.intervention.trim() || "暂无新增干预，按上述 idea 推进。", "若本轮干预改变已有目标、固定评价或资源边界，先说明具体变更并等待确认；保留历史结果与原始记录。", "", "## 已选工具与调用要求");
  tools.forEach((tool,index)=>{
    lines.push("",`### ${index+1}. ${tool.title}（${assistKinds[tool.kind]} / ${tool.id}）`,tool.description,`适用环节：${tool.stages.map(id=>assistSteps.find(s=>s.id===id).title).join(" / ")}`,`使用要求：${tool.usage}`);
    if(tool.links?.length)tool.links.forEach(link=>{const url=safeAssistURL(link.url);if(url)lines.push(`来源（${link.label}）：${url}`);});
    else lines.push(`来源：${tool.source_note || "本地技能，无公开链接"}；从当前环境的技能入口读取。`);
  });
  lines.push("", "## 执行顺序与人工确认点",`本轮从“${scope[0].title}”开始，后续按任务所需推进；已完成的前置环节只核查已有交接。`);
  scope.forEach(step=>{
    const matched=tools.filter(t=>t.stages.includes(step.id));
    lines.push("",`### ${assistPhases[step.phase]} / ${step.title}`,`应交接：${step.output}。`, `对应工具：${matched.length ? matched.map(t=>t.title).join("、") : "本环节未选专用工具。先说明能力缺口，再决定补充工具或使用 Agent 已具备的能力。"}`,assistDraft.checkpoints.includes(step.id) ? "人工确认点：进入本环节前暂停，汇报拟执行内容、依据与待决问题，等待用户确认后继续。" : "按已明确的任务范围与授权推进，结束后简报结果。");
  });
  lines.push("", "## 期待的产出",assistDraft.deliverable.trim() || "交付本轮范围内的材料、工具使用记录与下一步建议。", "", "## 交接约定", "1. 按所选工具、Skill、MCP与资料来源的官方说明执行任务，保留调用方式、配置和产物。需要账户或用户操作时说明具体步骤。", "2. 论文主张引用可核查的原文；实验指标对应真实代码、配置、数据和运行记录；保留失败与负结果。", "3. 每次阶段交接记录：当前结论、依据或产物路径、代码/作业版本、人工决定、未完成项与下一步。工具包提供流程能力，不保证实验改进或论文质量。", "4. 接到用户的新 idea 或干预后，先保存当前状态，明确影响范围，更新计划后接续；人工确认点与项目暂停状态持续有效。", "5. 本文档作为Agent任务上下文，执行中反馈实际进展并保存阶段交接。", "");
  return lines.join("\n");
}
function validateAssist() {
  let message="";
  if(!assistDraft.idea.trim())message="先填写你的 idea / 研究问题。";
  else if(!assistDraft.selected.length)message="至少选择一个工具，再生成任务文档。";
  else if(selectedAssistTools().some(t=>t.missing))message="所选清单中有当前未找到的工具，请移除后重新选择。";
  else if(assistDraft.attach && !current?.project)message="暂时无法读取关联项目，请刷新记录或取消关联。";
  $("assist-validation").textContent=message;$("assist-validation").hidden=!message;
  if(message){$("assist-validation").scrollIntoView({block:"center",behavior:"instant"});return false;}return true;
}
function downloadAssistDocument() {
  if(!validateAssist())return;
  const blob = new Blob([assistDocument()],{type:"text/markdown;charset=utf-8"});
  const url=URL.createObjectURL(blob);const link=document.createElement("a");link.href=url;link.download="AGENT_TASK.md";document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function initAssist() {
  loadAssistDraft();
  fillDomainSelect("starter-domain",false);fillDomainSelect("assist-domain");fillDomainSelect("open-domain");
  $("assist-domain").value=assistDraft.domain;
  $("starter-domain").value=assistDraft.domain==="all"?"general":assistDraft.domain;
  for(const [name,id] of Object.entries(assistFields)) {
    $(id).value=assistDraft[name];
    $(id).addEventListener("input",()=>{assistDraft[name]=$(id).value;saveAssistDraft();if(name==="start")renderAssistCheckpoints();});
  }
  $("assist-attach").checked=assistDraft.attach;
  $("assist-attach").addEventListener("change",()=>{assistDraft.attach=$("assist-attach").checked;document.body.classList.toggle("assist-attached",assistDraft.attach);saveAssistDraft();});
  if(!assistStorageAvailable)$("assist-save-state").textContent="当前无法读取草稿，请及时下载文档";
  document.querySelectorAll("[data-assist-phase]").forEach(b=>b.addEventListener("click",()=>{assistPhase=b.dataset.assistPhase;assistToolLimit=12;renderAssistTools();}));
  $("assist-search").addEventListener("input",()=>{assistToolLimit=12;renderAssistTools();});$("assist-kind").addEventListener("change",()=>{assistToolLimit=12;renderAssistTools();});
  $("assist-domain").addEventListener("change",()=>{assistDraft.domain=$("assist-domain").value;$("starter-domain").value=assistDraft.domain==="all"?"general":assistDraft.domain;assistToolLimit=12;saveAssistDraft();renderAssistTools();});
  $("assist-show-more").addEventListener("click",()=>{assistToolLimit+=12;renderAssistTools();});
  $("tools-show-more").addEventListener("click",()=>{resourceToolLimit+=18;renderResources(current || {});});
  $("resource-kind").addEventListener("change",()=>{toolFilter=$("resource-kind").value;resourceToolLimit=18;renderResources(current || {});});
  $("resource-stage").addEventListener("change",()=>{resourcePhase=$("resource-stage").value;resourceToolLimit=18;renderResources(current || {});});
  $("resource-search").addEventListener("input",()=>{resourceToolLimit=18;renderResources(current || {});});
  document.querySelectorAll("[data-home-phase]").forEach(link=>link.addEventListener("click",()=>{assistPhase=link.dataset.homePhase;assistToolLimit=12;$("assist-search").value="";$("assist-kind").value="all";renderAssistTools();}));
  $("open-domain").addEventListener("change",renderOpenLibrary);$("open-search").addEventListener("input",renderOpenLibrary);
  document.querySelectorAll("[data-assist-preset]").forEach(b=>b.addEventListener("click",()=>applyAssistPreset(b.dataset.assistPreset)));
  $("assist-preview").addEventListener("click",()=>{if(!validateAssist())return;$("assist-document").value=assistDocument();$("assist-copy-status").textContent="";$("assist-document-dialog").showModal();});
  $("assist-download").addEventListener("click",downloadAssistDocument);$("assist-document-download").addEventListener("click",downloadAssistDocument);
  $("assist-copy").addEventListener("click",async()=>{try{await navigator.clipboard.writeText($("assist-document").value);$("assist-copy-status").textContent="完整文档已复制，粘贴到 Agent 对话即可。";}catch{$("assist-document").focus();$("assist-document").select();$("assist-copy-status").textContent="当前浏览器未允许直接复制，已选中全文，可按 Ctrl+C 或长按复制。";}});
  $("assist-example").addEventListener("click",()=>{
    const example={title:"视觉异常检测 · 想法验证（演示任务）",idea:"我想验证：在冻结视觉特征上加入轻量特征校正，能否在计算开销基本不变的前提下改善异常检测？请先调研相关方法、筛选可复现基线，再给出实验方案。此处仅为任务示例，尚未执行。",budget:"先调研和设计实验；拟用单卡、最多 2 小时。正式提交前确认数据、评价协议与具体预算。",deliverable:"文献与基线对照表、实验方案、可复现的运行记录、结果图与结论草稿。"};
    for(const [name,value] of Object.entries(example))if(!assistDraft[name].trim()){assistDraft[name]=value;$(assistFields[name]).value=value;}
    assistDraft.selected=[...new Set([...assistDraft.selected,"research-workflow","arxiv-mcp","experiment-kit","matplotlib"])];saveAssistDraft();renderAssistTools();
  });
  $("custom-tool-form").addEventListener("submit",event=>{
    event.preventDefault();const url=safeAssistURL($("custom-url").value);const title=$("custom-name").value.trim();const description=$("custom-description").value.trim();
    if(!url || !title || !description){$("custom-error").textContent="填写名称、简介，并提供 http 或 https 文档链接。";$("custom-error").hidden=false;return;}
    const phase=$("custom-phase").value;const tool={id:`custom-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,8)}`,kind:$("custom-kind").value,title,description,usage:description,stages:assistSteps.filter(s=>s.phase===phase).map(s=>s.id),status:"自定义 · 待检查",evidence:"用户提供；Agent 使用前检查连接与调用方式。",links:[{label:"文档 / 仓库",url}]};
    assistDraft.custom.push(tool);assistDraft.selected.push(tool.id);$("custom-tool-form").reset();$("custom-error").hidden=true;assistPhase="all";assistToolLimit=12;$("assist-search").value=tool.title;$("assist-kind").value="all";$("assist-domain").value="all";assistDraft.domain="all";saveAssistDraft();renderAssistTools();
  });
  renderAssistCheckpoints();updateAssistContext();renderOpenLibrary();
}
initAssist();
setNavigation();
load();

// Only synchronize visible local records; no remote polling or model calls.
setInterval(() => {
  if (!document.hidden && $("main").getAttribute("aria-busy") === "false" && !document.querySelector("dialog[open]")) load(current?.project?.id);
}, 30000);
