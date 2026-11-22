import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

const ws=new WebSocket('ws://127.0.0.1:9222/session');
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
async function loaded(){await until(`document.querySelector('main')?.getAttribute('aria-busy')==='false'`);}
async function click(selector){
  const point=await evaluate(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.scrollIntoView({block:'center',behavior:'instant'});const r=n.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})()`);
  await send('input.performActions',{context,actions:[{type:'pointer',id:'mouse',parameters:{pointerType:'mouse'},actions:[{type:'pointerMove',x:point.x,y:point.y},{type:'pointerDown',button:0},{type:'pointerUp',button:0}]}]});
}
const output='research/showcase';
await mkdir(output,{recursive:true});
const base = process.env.SHOWCASE_URL || 'http://127.0.0.1:8765/';
const visiblePage = name => `document.querySelector('[data-page="${name}"]').hidden===false && [...document.querySelectorAll('[data-page]')].filter(n=>!n.hidden).length===1`;
async function page(name) {
  await click(`.nav a[href="#${name}"]`);
  await until(visiblePage(name));
  assert.equal(await evaluate(`document.querySelector('.nav [aria-current="page"]').hash`),'#'+name);
}
async function capture(name) {
  await evaluate(`(()=>{scrollTo({top:0,behavior:'instant'});return true})()`);
  await send('input.performActions',{context,actions:[{type:'pointer',id:'mouse',parameters:{pointerType:'mouse'},actions:[{type:'pointerMove',x:2,y:2}]}]});
  await evaluate(`document.fonts.ready.then(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))).then(()=>true)`);
  await new Promise(resolve=>setTimeout(resolve,900));
  const shot = await send('browsingContext.captureScreenshot',{context,origin:'document'});
  await writeFile(output+'/'+name+'.png',Buffer.from(shot.data,'base64'));
}
try {
  const session = await send('session.new',{capabilities:{alwaysMatch:{acceptInsecureCerts:true}}});
  await send('session.subscribe',{events:['log.entryAdded']});
  context = (await send('browsingContext.getTree')).contexts[0].context;
  await send('browsingContext.setViewport',{context,viewport:{width:1440,height:1000},devicePixelRatio:1});
  await send('browsingContext.navigate',{context,url:base,wait:'complete'});
  await loaded();
  const isStatic = await evaluate(`document.documentElement.dataset.static==='true'`);
  const payload = await (await fetch(new URL(isStatic?'snapshot.json':'api/snapshot',base))).json();
  const data = isStatic ? payload.snapshots[payload.default_project] || payload.empty : payload;
  const project = data.project;
  assert.equal(await evaluate(visiblePage('overview')),true);
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
  for(const kind of ['skill','mcp','plugin']) {
    await click(`[data-tool-filter="${kind}"]`);
    assert.equal(await evaluate(`document.querySelectorAll('.tool-card').length`),data.tools.items.filter(t=>t.kind===kind).length);
  }
  await click('[data-tool-filter="all"]');await capture('tools');
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
    for(const name of ['overview','progress','results','resources']) {
      await page(name);
      assert.equal(await evaluate(`document.documentElement.scrollWidth<=window.innerWidth`),true,`${width} ${name}`);
      assert.equal(await evaluate(`document.querySelector('.guide-trigger').getBoundingClientRect().width>0`),true);
      if(width===390) await capture('mobile-'+name);
    }
  }
  pass('All four views at 1440/390/320 px without horizontal overflow, help remains accessible');
  await page('overview');
  await evaluate(`(()=>{window.qaFetch=window.fetch;window.qaRequests=0;window.fetch=(...args)=>{window.qaRequests++;return window.qaFetch(...args)};return true})()`);
  for(let i=0;i<35;i++) {if(await evaluate(`window.qaRequests>0`))break;await new Promise(r=>setTimeout(r,1000));}
  await loaded();assert.ok(await evaluate(`window.qaRequests>0`));
  pass('Visible view refreshes snapshot automatically');
  assert.deepEqual(browserErrors,[]);pass('No browser script errors');
  await writeFile(output+'/browser-checks.json',JSON.stringify({generated_at:new Date().toISOString(),browser:session.capabilities.browserVersion,static:isStatic,url:base,project_id:project.id,checks,browser_errors:browserErrors},null,2)+'\n');
} finally {try{await send('session.end');}catch{} ws.close();}
