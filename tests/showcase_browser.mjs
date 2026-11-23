import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

const ws=new WebSocket(process.env.BROWSER_BIDI_URL || 'ws://127.0.0.1:9222/session');
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let sequence=0;
const waiting=new Map();
const browserErrors=[];
ws.onmessage=event=>{
  const message=JSON.parse(event.data);
  if(message.method==='log.entryAdded' && message.params.level==='error') browserErrors.push(message.params.text);
  const request=waiting.get(message.id);
  if(!request)return;
  waiting.delete(message.id);
  if(message.type==='error')request.reject(new Error(message.error+': '+message.message));
  else request.resolve(message.result);
};
const send=(method,params={})=>new Promise((resolve,reject)=>{
  const id=++sequence;waiting.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));
});
const checks=[];
function pass(name){checks.push(name);process.stdout.write('PASS '+name+'\n');}
let context;
const evaluate=async expression=>{
  const result=await send('script.evaluate',{expression:`(async()=>JSON.stringify(await (${expression})))()`,target:{context},awaitPromise:true});
  if(result.type==='exception')throw new Error(JSON.stringify(result.exceptionDetails));
  return JSON.parse(result.result.value);
};
async function until(expression){
  for(let i=0;i<100;i++){
    if(await evaluate(expression))return;
    await new Promise(r=>setTimeout(r,100));
  }
  throw new Error('Timed out: '+expression);
}
async function loaded(){
  const ready=`document.readyState==='complete' && Boolean(document.querySelector('#assist-selected-count')) && document.querySelector('main')?.getAttribute('aria-busy')==='false'`;
  await until(ready);await new Promise(r=>setTimeout(r,120));await until(ready);
}
async function click(selector){
  const point=await evaluate(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.scrollIntoView({block:'center',behavior:'instant'});const r=n.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})()`);
  await send('input.performActions',{context,actions:[{type:'pointer',id:'mouse',parameters:{pointerType:'mouse'},actions:[{type:'pointerMove',x:point.x,y:point.y},{type:'pointerDown',button:0},{type:'pointerUp',button:0}]}]});
}
const output=process.env.SHOWCASE_OUTPUT || 'research/showcase';
await mkdir(output,{recursive:true});
const base = process.env.SHOWCASE_URL || 'http://127.0.0.1:8765/';
const visiblePage = name => `document.querySelector('[data-page="${name}"]').hidden===false && [...document.querySelectorAll('[data-page]')].filter(n=>!n.hidden).length===1`;
async function page(name) {
  await click(`.nav a[href="#${name}"]`);
  await until(visiblePage(name));
  assert.equal(await evaluate(`document.querySelector('.nav [aria-current="page"]').hash`),'#'+name);
}
async function capture(name) {
  await evaluate(`(()=>{document.querySelector('#toast').hidden=true;const preview=document.querySelector('#assist-document');if(preview){preview.setSelectionRange(0,0);preview.scrollTop=0;}scrollTo({top:0,behavior:'instant'});return true})()`);
  await send('input.performActions',{context,actions:[{type:'pointer',id:'mouse',parameters:{pointerType:'mouse'},actions:[{type:'pointerMove',x:2,y:2}]}]});
  await evaluate(`document.fonts.ready.then(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))).then(()=>true)`);
  await new Promise(resolve=>setTimeout(resolve,900));
  const dialogOpen=await evaluate(`Boolean(document.querySelector('dialog[open]'))`);
  const shot = await send('browsingContext.captureScreenshot',{context,origin:dialogOpen || process.env.SHOWCASE_CAPTURE==='viewport'?'viewport':'document'});
  await writeFile(output+'/'+name+'.png',Buffer.from(shot.data,'base64'));
}
try {
  const session = await send('session.new',{capabilities:{alwaysMatch:{acceptInsecureCerts:true}}});
  await send('session.subscribe',{events:['log.entryAdded']});
  context = (await send('browsingContext.getTree')).contexts[0].context;
  await send('browsingContext.setViewport',{context,viewport:{width:1440,height:1000},devicePixelRatio:1});
  await send('browsingContext.navigate',{context,url:base,wait:'complete'});
  await loaded();
  // Run only with a dedicated QA browser profile; initialize this feature's draft.
  await evaluate(`(()=>{localStorage.removeItem('ai2research.assisted-task.v1');return true})()`);
  await send('browsingContext.reload',{context,wait:'complete'});await loaded();
  const isStatic = await evaluate(`document.documentElement.dataset.static==='true'`);
  const payload = await (await fetch(new URL(isStatic?'snapshot.json':'api/snapshot',base))).json();
  const data = isStatic ? payload.snapshots[payload.default_project] || payload.empty : payload;
  const project = data.project;
  const library=await evaluate('window.AI2RESEARCH_LIBRARY');
  assert.equal(await evaluate(visiblePage('workspace')),true);
  assert.equal(await evaluate(`document.querySelectorAll('.mode-card').length`),2);
  assert.equal(await evaluate(`document.querySelectorAll('.mode-badge,.mode-capabilities,.mode-subtitle,.workspace-kicker,.human-loop,.assist-stage-tags').length`),0);
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.project-context')).display`),'none');
  await capture('workspace');
  await page('assist');
  assert.equal(await evaluate(`document.querySelector('[data-assist-phase="research"]').getAttribute('aria-pressed')`),'true');
  assert.equal(await evaluate(`document.querySelectorAll('.assist-tool-card').length`),12);
  assert.ok((await evaluate(`document.querySelector('#assist-catalog-count').textContent`)).endsWith(`/ ${data.tools.items.length+3+library.length} 项`));
  assert.equal(await evaluate(`document.querySelectorAll('#assist-domain option').length`),10);
  await click('#assist-show-more');
  assert.equal(await evaluate(`document.querySelectorAll('.assist-tool-card').length`),24);
  assert.equal(await evaluate(`document.querySelectorAll('.tool-status,.tool-evidence,.assist-tool-status').length`),0);
  await click('#assist-preview');
  assert.equal(await evaluate(`document.querySelector('#assist-document-dialog').open`),false);
  assert.ok((await evaluate(`document.querySelector('#assist-validation').textContent`)).includes('idea'));
  await click('[data-assist-phase="implement"]');
  assert.ok(await evaluate(`[...document.querySelectorAll('.assist-tool-card')].every(n=>n.dataset.phases.includes('implement'))`));
  await evaluate(`(()=>{const n=document.querySelector('#assist-kind');n.value='experiment';n.dispatchEvent(new Event('change'));return true})()`);
  assert.ok(await evaluate(`document.querySelectorAll('.assist-tool-card').length>=2`));
  await click('[data-assist-tool="experiment-kit"] input');
  assert.equal(await evaluate(`document.querySelector('#assist-selected-count').textContent`),'1');
  await click('[data-assist-phase="all"]');
  await evaluate(`(()=>{const n=document.querySelector('#assist-kind');n.value='all';n.dispatchEvent(new Event('change'));const q=document.querySelector('#assist-search');q.value='arxiv';q.dispatchEvent(new Event('input'));return true})()`);
  assert.equal(await evaluate(`document.querySelectorAll('.assist-tool-card').length`),2);
  assert.deepEqual(await evaluate(`[...document.querySelectorAll('.assist-tool-card')].map(n=>n.dataset.assistTool).sort()`),['arxiv-mcp','lib-arxiv']);
  await evaluate(`(()=>{const q=document.querySelector('#assist-search');q.value='';q.dispatchEvent(new Event('input'));return true})()`);
  await click('#assist-example');
  assert.equal(await evaluate(`document.querySelector('#assist-selected-count').textContent`),'4');
  assert.equal(await evaluate(`document.querySelector('#assist-attach').checked`),false);
  pass('Dual-mode home, assisted tool catalog, phase/type/search filters, selection and input validation');
  await click('.intervention-details summary');
  await evaluate(`(()=>{const n=document.querySelector('#assist-intervention');n.value='先保留 baseline；只比较现有结果，再决定新实验。';n.dispatchEvent(new Event('input'));return true})()`);
  await click('#assist-preview');
  const doc=await evaluate(`document.querySelector('#assist-document').value`);
  for(const text of ['登记状态','已检测','未检测到','待接入'])assert.ok(!doc.includes(text),text);
  for(const text of ['独立辅助科研任务','研究工作流','实验运行与结果回收','https://github.com/','先保留 baseline','进入本环节前暂停','## 资源与边界','## 期待的产出'])assert.ok(doc.includes(text),text);
  await writeFile(output+'/AGENT_TASK.example.md',doc);
  await evaluate(`(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.qaCopied=text}}});return true})()`);
  await click('#assist-copy');await until('Boolean(window.qaCopied)');
  assert.equal(await evaluate('window.qaCopied'),doc);
  await evaluate(`(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('QA clipboard unavailable')}}});return true})()`);
  await click('#assist-copy');
  assert.equal(await evaluate(`document.querySelector('#assist-document').selectionEnd-document.querySelector('#assist-document').selectionStart`),doc.length);
  await evaluate(`(()=>{window.qaAnchorClick=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){if(this.download){window.qaDownloadName=this.download;window.qaDownloadText=fetch(this.href).then(r=>r.text());}else window.qaAnchorClick.call(this)};return true})()`);
  await click('#assist-document-download');
  assert.equal(await evaluate('window.qaDownloadName'),'AGENT_TASK.md');
  assert.equal(await evaluate('window.qaDownloadText'),doc);
  await evaluate(`(()=>{HTMLAnchorElement.prototype.click=window.qaAnchorClick;return true})()`);
  await capture('agent-document');
  await click('[data-dialog="assist-document-dialog"]');
  pass('Agent document contains idea, tools, sources, resource bounds, human interventions and checkpoints; copying, fallback and actual download Blob match');
  await evaluate(`(()=>{const n=document.querySelector('#assist-start');n.value='iterate';n.dispatchEvent(new Event('input'));return true})()`);
  assert.equal(await evaluate(`document.querySelectorAll('#assist-checkpoints input:disabled').length`),3);
  await click('#assist-preview');
  const continuation=await evaluate(`document.querySelector('#assist-document').value`);
  assert.ok(continuation.includes('本轮从“迭代”开始'));
  assert.ok(!continuation.includes('### 实现 / 跑实验'));
  await click('[data-dialog="assist-document-dialog"]');
  await send('browsingContext.navigate',{context,url:new URL('#assist',base).href,wait:'complete'});
  await send('browsingContext.reload',{context,wait:'complete'});await loaded();
  assert.equal(await evaluate(`document.querySelector('#assist-start').value`),'iterate');
  assert.equal(await evaluate(`document.querySelector('#assist-selected-count').textContent`),'4');
  assert.ok((await evaluate(`document.querySelector('#assist-intervention').value`)).includes('baseline'));
  await evaluate(`load(current?.project?.id).then(()=>true)`);await loaded();
  assert.equal(await evaluate(`document.querySelector('#assist-start').value`),'iterate');
  pass('Mid-workflow entry excludes earlier checkpoints and draft selections/interventions survive reload and data refresh');
  await click('.custom-tool-details summary');
  const customName='<img src=x onerror=alert(1)> 自定义 MCP';
  await evaluate(`(()=>{for(const [id,value] of Object.entries({'custom-name':${JSON.stringify(customName)},'custom-description':'用户自己的文献整理接口','custom-url':'https://example.org/my-mcp'}))document.getElementById(id).value=value;document.querySelector('#custom-tool-form').requestSubmit();return true})()`);
  assert.equal(await evaluate(`document.querySelector('#assist-selected-count').textContent`),'5');
  assert.equal(await evaluate(`document.querySelectorAll('.assist-tool-card img').length`),0);
  assert.ok((await evaluate(`document.querySelector('#assist-selected').textContent`)).includes(customName));
  await click('#assist-preview');
  assert.ok((await evaluate(`document.querySelector('#assist-document').value`)).includes('https://example.org/my-mcp'));
  await click('[data-dialog="assist-document-dialog"]');
  await click('[data-assist-tool^="custom-"] .text-button');
  assert.equal(await evaluate(`document.querySelector('#assist-selected-count').textContent`),'4');
  const pausedAssist=data.projects.find(p=>p.execution_state==='paused');
  if(pausedAssist){
    await evaluate(`(()=>{const n=document.querySelector('#project');n.value=${JSON.stringify(pausedAssist.id)};n.dispatchEvent(new Event('change'));return true})()`);await loaded();
    await click('#assist-attach');await click('#assist-preview');
    const pausedDoc=await evaluate(`document.querySelector('#assist-document').value`);
    assert.ok(pausedDoc.includes(pausedAssist.id));assert.ok(pausedDoc.includes('保持暂停，不提交实验'));
    await click('[data-dialog="assist-document-dialog"]');await click('#assist-attach');
    await evaluate(`(()=>{const n=document.querySelector('#project');n.value=${JSON.stringify(project.id)};n.dispatchEvent(new Event('change'));return true})()`);await loaded();
  }
  await evaluate(`(()=>{const n=document.querySelector('#assist-start');n.value='survey';n.dispatchEvent(new Event('input'));return true})()`);
  await click('[data-assist-phase="research"]');
  await capture('assisted');
  pass('Custom tool create/select/export/delete uses literal text; linking a paused project preserves its execution state');
  const originalDraft=await evaluate(`localStorage.getItem('ai2research.assisted-task.v1')`);
  for(const [preset,start] of [['discover','survey'],['validate','screen'],['publish','figure']]) {
    await evaluate(`(()=>{localStorage.removeItem('ai2research.assisted-task.v1');return true})()`);
    await send('browsingContext.reload',{context,wait:'complete'});await loaded();
    await evaluate(`(()=>{document.querySelector('#starter-domain').value='cv';return true})()`);
    await click(`[data-assist-preset="${preset}"]`);
    assert.equal(await evaluate(`document.querySelector('#assist-start').value`),start);
    assert.equal(await evaluate(`document.querySelector('#assist-domain').value`),'cv');
    assert.ok((await evaluate(`document.querySelector('#assist-idea').value`)).includes('计算机视觉'));
    assert.ok(Number(await evaluate(`document.querySelector('#assist-selected-count').textContent`))>=6);
    await click('#assist-preview');
    const presetDoc=await evaluate(`document.querySelector('#assist-document').value`);
    assert.ok(presetDoc.includes('CVF Open Access'));
    assert.ok(presetDoc.includes('研究领域：计算机视觉'));
    await click('[data-dialog="assist-document-dialog"]');
    if(preset==='discover') {
      const idea=await evaluate(`document.querySelector('#assist-idea').value`);
      await click('[data-assist-preset="validate"]');
      assert.equal(await evaluate(`document.querySelector('#assist-idea').value`),idea);
      await capture('guided-starter');
    }
  }
  await evaluate(`(()=>{localStorage.setItem('ai2research.assisted-task.v1',${JSON.stringify(originalDraft)});return true})()`);
  await send('browsingContext.reload',{context,wait:'complete'});await loaded();
  pass('Three novice presets choose domain-specific papers/code/tools, preserve existing ideas and start at the appropriate stage');
  await page('overview');
  assert.equal(await evaluate(`document.querySelectorAll('.guide-trigger').length`),1);
  assert.equal(await evaluate(`document.querySelectorAll('.eyebrow,.quick-guide,.intro').length`),0);
  assert.equal(await evaluate(`document.querySelector('#stat-total').textContent`),String(project.jobs_total));
  assert.equal(await evaluate(`document.querySelector('#hero-progress').textContent`),`${project.progress.completed_stages} / ${project.progress.total_stages}`);
  if(project.progress.completed_stages===12) {
    assert.equal(await evaluate(`document.querySelector('#project-state').textContent`),'已交付');
    assert.equal(await evaluate(`document.querySelector('#overview-status').textContent`),'全过程已完成');
  }
  pass('Focused overview, one help entry, no slogans, actual counts and accepted delivery state');
  await capture('desktop');
  await page('progress');
  assert.equal(await evaluate(`document.querySelectorAll('.progress-node').length`),12);
  assert.equal(await evaluate(`document.querySelectorAll('.phase-caption').length`),3);
  assert.equal(await evaluate(`Number(document.querySelector('#overall-progress').value)`),project.progress.completed_stages);
  await click('[data-stage="preparation"]');
  const preparation=project.progress.stages.find(s=>s.id==='preparation');
  assert.equal(await evaluate(`document.querySelector('#stage-state').textContent`),preparation.state_label);
  if(preparation.notes.length) {
    await click('#stage-notes-wrap summary');
    assert.equal(await evaluate(`document.querySelector('#stage-notes').textContent`),preparation.notes.join(''));
  }
  await click('[data-stage="papers"]');
  assert.equal(await evaluate(`document.querySelectorAll('#stage-items .stage-item').length`),project.progress.stages.find(s=>s.id==='papers').items?.length||0);
  pass('Dedicated twelve-stage view with completion evidence and preserved legacy handoff notes');
  await capture('progress');
  await send('browsingContext.navigate',{context,url:new URL('?project='+project.id+'#progress',base).href,wait:'complete'});
  await loaded();
  assert.equal(await evaluate(visiblePage('progress')),true);
  await page('results');
  await evaluate(`(()=>{history.back();return true})()`);
  await until(visiblePage('progress'));
  await page('results');
  pass('Page deep links, reload, navigation highlight and browser back');
  assert.equal(await evaluate(`document.querySelectorAll('.output-link').length`),project.outputs.length);
  assert.equal(await evaluate(`document.querySelectorAll('.run-cell').length`),project.jobs_total);
  assert.equal(await evaluate(`document.querySelectorAll('#run-chart rect').length`),Object.keys(project.job_counts).length);
  if(project.jobs.length) {
    await click('#run-details summary');await click('.run-cell');
    assert.ok((await evaluate(`document.querySelector('#run-detail').textContent`)).includes('第 1 次'));
  }
  for(const artifact of project.outputs) {
    const response=await fetch(new URL(artifact.url,base));
    assert.equal(response.status,200);
    assert.equal(createHash('sha256').update(Buffer.from(await response.arrayBuffer())).digest('hex'),artifact.sha256);
  }
  pass('Separate results view, interactive real run records and all PDF/PNG SHA256 checks');
  await capture('results');
  await page('resources');
  assert.equal(await evaluate(`document.querySelectorAll('.reference-row').length`),project.references.items.length);
  const links=await evaluate(`[...document.querySelectorAll('.reference-actions a')].map(n=>n.href)`);
  for(const paper of project.references.items) {
    if(paper.pdf_url)assert.ok(links.includes(paper.pdf_url));
    if(paper.code_url)assert.ok(links.includes(paper.code_url));
  }
  await capture('references');await click('#tools-tab');
  assert.equal(await evaluate(`document.querySelector('#tools-panel').hidden`),false);
  assert.equal(await evaluate(`document.querySelector('#resource-stage').value`),'research');
  assert.ok(await evaluate(`[...document.querySelectorAll('#tool-grid .tool-card')].every(n=>n.dataset.phases.includes('research'))`));
  assert.ok(await evaluate(`[...document.querySelectorAll('#tool-grid .tool-card')].find(n=>n.querySelector('h3').textContent==='研究工作流').textContent.includes('已选择')`));
  await evaluate(`(()=>{const n=document.querySelector('#resource-stage');n.value='all';n.dispatchEvent(new Event('change'));return true})()`);
  for(const kind of ['skill','mcp','plugin']) {
    await evaluate(`(()=>{const n=document.querySelector('#resource-kind');n.value=${JSON.stringify(kind)};n.dispatchEvent(new Event('change'));return true})()`);
    const expected=data.tools.items.filter(t=>t.kind===kind).length+library.filter(t=>t.kind===kind).length+(kind==='mcp'?1:0);
    assert.equal(await evaluate(`document.querySelectorAll('#tool-grid .tool-card').length`),Math.min(18,expected));
  }
  await evaluate(`(()=>{const n=document.querySelector('#resource-kind');n.value='all';n.dispatchEvent(new Event('change'));const p=document.querySelector('#resource-stage');p.value='research';p.dispatchEvent(new Event('change'));return true})()`);await capture('tools');
  await evaluate(`(()=>{const n=document.querySelector('#resource-stage');n.value='all';n.dispatchEvent(new Event('change'));return true})()`);
  assert.equal(await evaluate(`document.querySelectorAll('.tool-status,.tool-evidence,.assist-tool-status').length`),0);
  await click('#tools-show-more');
  assert.equal(await evaluate(`document.querySelectorAll('#tool-grid .tool-card').length`),36);
  await click('#open-tab');
  assert.equal(await evaluate(`document.querySelectorAll('#open-library .tool-card').length`),library.filter(t=>t.kind==='resource').length);
  await evaluate(`(()=>{const n=document.querySelector('#open-domain');n.value='cv';n.dispatchEvent(new Event('change'));return true})()`);
  const resourceIds=await evaluate(`[...document.querySelectorAll('#open-library [data-resource-id]')].map(n=>n.dataset.resourceId)`);
  assert.ok(resourceIds.includes('lib-cvf'));assert.ok(resourceIds.includes('lib-openmmlab'));assert.ok(!resourceIds.includes('lib-pubmed'));
  await click('[data-resource-id="lib-cvf"] button');
  assert.ok((await evaluate(`document.querySelector('#assist-selected').textContent`)).includes('CVF Open Access'));
  await click('[data-resource-id="lib-cvf"] button');
  await capture('open-resources');
  await evaluate(`(()=>{const n=document.querySelector('#open-search');n.value='nonexistent-resource-qa';n.dispatchEvent(new Event('input'));return true})()`);
  assert.equal(await evaluate(`document.querySelector('#open-empty').hidden`),false);
  await evaluate(`(()=>{document.querySelector('#open-search').value='';document.querySelector('#open-search').dispatchEvent(new Event('input'));return true})()`);
  pass('Expanded tool directory and domain-specific paper/code/model/data sources, progressive loading and selection; no tool availability labels');
  pass('Dedicated references and tools view, registered PDF/GitHub links and type filters');
  await page('overview');
  await click('.guide-trigger');assert.equal(await evaluate(`document.querySelector('#guide-dialog').open`),true);
  await click('[data-dialog="guide-dialog"]');
  await click('#jump-current');await until(visiblePage('progress'));
  assert.equal(await evaluate(`document.querySelector('#stage-title').textContent`),project.progress.stages.find(s=>s.id===project.progress.focus_stage).title);
  await click('#about');assert.equal(await evaluate(`document.querySelector('#about-dialog').open`),true);await click('#close-about');
  pass('Single help dialog, current-stage shortcut and progress calculation explanation');
  await page('overview');
  await evaluate(`(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.qaCopied=text}}});return true})()`);
  await click('#copy-current');await until(`Boolean(window.qaCopied)`);
  assert.ok((await evaluate(`window.qaCopied`)).includes(project.id));
  await evaluate(`(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('QA clipboard unavailable')}}});return true})()`);
  await click('#copy-current');assert.equal(await evaluate(`document.querySelector('#copy-dialog').open`),true);await click('[data-dialog="copy-dialog"]');
  pass('Project instruction copying and manual fallback');
  const paused=data.projects.find(p=>p.execution_state==='paused');
  if(paused) {
    await evaluate(`(()=>{const n=document.querySelector('#project');n.value=${JSON.stringify(paused.id)};n.dispatchEvent(new Event('change'));return true})()`);await loaded();
    assert.equal(await evaluate(`document.querySelector('#project-state').textContent`),'已暂停');
    await click('#copy-current');assert.ok((await evaluate(`document.querySelector('#command-text').value`)).includes('保留暂停状态'));await click('[data-dialog="copy-dialog"]');
  }
  await evaluate(`(()=>{const n=document.querySelector('#project');n.value=${JSON.stringify(project.id)};n.dispatchEvent(new Event('change'));return true})()`);await loaded();
  pass('Project selection retains paused state and safe continuation instructions');
  await evaluate(`(()=>{window.qaFetch=window.fetch;window.fetch=()=>Promise.reject(new TypeError('QA connection failure'));return true})()`);
  await click('#refresh');await loaded();assert.equal(await evaluate(`document.querySelector('#notice').hidden`),false);
  assert.equal(await evaluate(`document.querySelector('#stat-total').textContent`),String(project.jobs_total));
  await evaluate(`(()=>{window.fetch=window.qaFetch;return true})()`);await click('#refresh');await loaded();
  assert.equal(await evaluate(`document.querySelector('#notice').hidden`),true);
  pass('Failed refresh keeps previous data and recovers');
  const empty={generated_at:new Date().toISOString(),projects:[],project:null};
  await evaluate(`(()=>{window.qaFetch=window.fetch;window.fetch=()=>Promise.resolve(new Response(JSON.stringify(${JSON.stringify(isStatic?{snapshots:{},default_project:null,empty}:empty)})));return true})()`);
  await click('#refresh');await loaded();assert.ok((await evaluate(`document.querySelector('#copy-label').textContent`)).includes('开始研究'));
  await click('#copy-current');assert.ok((await evaluate(`document.querySelector('#command-text').value`)).includes('研究领域'));await click('[data-dialog="copy-dialog"]');
  await evaluate(`(()=>{window.fetch=window.qaFetch;return true})()`);await click('#refresh');await loaded();
  pass('Empty project state provides a start template and restores data');
  for(const width of [1440,390,320]) {
    await send('browsingContext.setViewport',{context,viewport:{width,height:width===1440?1000:844},devicePixelRatio:1});
    for(const name of ['workspace','assist','overview','progress','results','resources']) {
      await page(name);
      assert.equal(await evaluate(`document.documentElement.scrollWidth<=window.innerWidth`),true,`${width} ${name}`);
      assert.equal(await evaluate(`document.querySelector('.guide-trigger').getBoundingClientRect().width>0`),true);
      if(width===390) await capture('mobile-'+name);
    }
  }
  pass('All six views at 1440/390/320 px without horizontal overflow, help remains accessible');
  if(!process.env.SHOWCASE_SKIP_AUTO_REFRESH) {
    await page('overview');
    await evaluate(`(()=>{window.qaFetch=window.fetch;window.qaRequests=0;window.fetch=(...args)=>{window.qaRequests++;return window.qaFetch(...args)};return true})()`);
    for(let i=0;i<35;i++) {if(await evaluate(`window.qaRequests>0`))break;await new Promise(r=>setTimeout(r,1000));}
    await loaded();assert.ok(await evaluate(`window.qaRequests>0`));
    pass('Visible view refreshes snapshot automatically');
  }
  assert.deepEqual(browserErrors,[]);pass('No browser script errors');
  await writeFile(output+'/browser-checks.json',JSON.stringify({generated_at:new Date().toISOString(),browser:session.capabilities.browserVersion,static:isStatic,url:base,project_id:project.id,checks,browser_errors:browserErrors},null,2)+'\n');
} finally {try{await send('session.end');}catch{} ws.close();}
