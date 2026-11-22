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
let capabilities;
try{
  const session=await send('session.new',{capabilities:{alwaysMatch:{acceptInsecureCerts:true}}});
  capabilities=session.capabilities;
  await send('session.subscribe',{events:['log.entryAdded']});
  const tree=await send('browsingContext.getTree',{});
  context=tree.contexts[0].context;
  await send('browsingContext.setViewport',{context,viewport:{width:1440,height:1100},devicePixelRatio:1});
  await send('browsingContext.navigate',{context,url:'http://127.0.0.1:8765',wait:'complete'});
  await loaded();
  const data=await(await fetch('http://127.0.0.1:8765/api/snapshot')).json();
  assert.equal(await evaluate(`document.querySelector('#stat-total').textContent`),String(data.project.jobs_total));
  assert.equal(await evaluate(`document.querySelectorAll('.run-cell').length`),data.project.jobs_total);
  assert.equal(await evaluate(`document.querySelectorAll('.output-link').length`),data.project.outputs.length);
  assert.equal(await evaluate(`document.querySelector('#stage-title').textContent`),data.project.progress.stages.find(s=>s.id===data.project.progress.focus_stage).title);
  pass('Real project counts, experiment tiles and registered output links');
  assert.equal(await evaluate(`getComputedStyle(document.documentElement).colorScheme`),'light');
  assert.equal(await evaluate(`document.querySelectorAll('.phase-caption').length`),3);
  assert.equal(await evaluate(`document.querySelector('#hero-progress').textContent`),`${data.project.progress.completed_stages} / ${data.project.progress.total_stages}`);
  assert.equal(await evaluate(`document.querySelectorAll('#run-chart rect').length`),Object.keys(data.project.job_counts).length);
  pass('Light theme, three journey groups, real progress ring and experiment distribution chart');
  assert.ok(await evaluate(`getComputedStyle(document.querySelector('.stat-value')).fontVariantNumeric.includes('tabular-nums')`));
  assert.ok(await evaluate(`[...document.querySelectorAll('.node-name,.phase-badge,.criterion')].every(n=>parseFloat(getComputedStyle(n).fontSize)>=14)`));
  assert.ok(await evaluate(`[...document.querySelectorAll('.file-size')].every(n=>n.getBoundingClientRect().right<=n.closest('.output-link').getBoundingClientRect().right)`));
  pass('Consistent readable type scale, aligned numerals and untruncated file sizes');
  assert.equal(await evaluate(`document.documentElement.scrollWidth<=window.innerWidth`),true);
  pass('Desktop layout without horizontal overflow');
  await new Promise(r=>setTimeout(r,1000));
  const desktop=await send('browsingContext.captureScreenshot',{context,origin:'document'});
  await writeFile(output+'/desktop.png',Buffer.from(desktop.data,'base64'));

  assert.equal(await evaluate(`document.querySelectorAll('.reference-row').length`),data.project.references.items.length);
  const referenceLinks=await evaluate(`[...document.querySelectorAll('.reference-actions a')].map(n=>n.href)`);
  for(const paper of data.project.references.items){
    if(paper.pdf_url)assert.ok(referenceLinks.includes(paper.pdf_url));
    if(paper.code_url)assert.ok(referenceLinks.includes(paper.code_url));
  }
  await evaluate(`(()=>{document.querySelector('#resources').scrollIntoView({behavior:'instant',block:'start'});return true})()`);
  const referencesCapture=await send('browsingContext.captureScreenshot',{context,origin:'viewport'});
  await writeFile(output+'/references.png',Buffer.from(referencesCapture.data,'base64'));
  await click('#tools-tab');
  assert.equal(await evaluate(`document.querySelector('#tools-panel').hidden`),false);
  assert.equal(await evaluate(`document.querySelectorAll('.tool-card').length`),data.tools.items.length);
  await evaluate(`(()=>{document.querySelector('#resources').scrollIntoView({behavior:'instant',block:'start'});return true})()`);
  await send('browsingContext.setViewport',{context,viewport:{width:1440,height:1300},devicePixelRatio:1});
  const toolsCapture=await send('browsingContext.captureScreenshot',{context,origin:'viewport'});
  await writeFile(output+'/tools.png',Buffer.from(toolsCapture.data,'base64'));
  await send('browsingContext.setViewport',{context,viewport:{width:1440,height:1100},devicePixelRatio:1});
  for(const kind of ['skill','mcp','plugin']){
    await click(`[data-tool-filter="${kind}"]`);
    assert.equal(await evaluate(`document.querySelectorAll('.tool-card').length`),data.tools.items.filter(t=>t.kind===kind).length);
  }
  await click('[data-tool-filter="all"]');
  await click('#references-tab');
  assert.equal(await evaluate(`document.querySelector('#references-panel').hidden`),false);
  pass('Registered PDF/GitHub links and filterable Skill, MCP and plugin inventory');

  assert.equal(await evaluate(`document.querySelectorAll('.progress-node').length`),data.project.progress.total_stages);
  assert.equal(await evaluate(`Number(document.querySelector('#overall-progress').value)`),data.project.progress.completed_stages);
  await click('[data-stage="experiment_review"]');
  assert.equal(await evaluate(`document.querySelector('#stage-title').textContent`),'实验审查');
  assert.equal(await evaluate(`document.querySelectorAll('.map-node[aria-pressed=true]').length`),1);
  assert.ok(await evaluate(`document.querySelectorAll('#stage-checks li').length>0`));
  await click('[data-stage="papers"]');
  assert.equal(await evaluate(`document.querySelectorAll('#stage-items .stage-item').length`),data.project.progress.stages.find(s=>s.id==='papers').items?.length || 0);
  pass('Twelve milestone states, checklist progress and real candidate paper links');
  await click('#goto-current');
  assert.equal(await evaluate(`document.querySelector('#stage-title').textContent`),data.project.progress.stages.find(s=>s.id===data.project.progress.focus_stage).title);
  assert.equal(await evaluate(`document.querySelector('.nav-active').getAttribute('href')`),'#research-progress');
  await click('.header .guide-trigger');
  assert.equal(await evaluate(`document.querySelector('#guide-dialog').open`),true);
  const guideCapture=await send('browsingContext.captureScreenshot',{context,origin:'viewport'});
  await writeFile(output+'/guide.png',Buffer.from(guideCapture.data,'base64'));
  await click('#guide-start');
  assert.equal(await evaluate(`document.querySelector('#guide-dialog').open`),false);
  pass('First-use guide and current-step shortcuts work through actual pointer clicks');
  await evaluate(`(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.qaCopied=text}}});return true})()`);
  await click('#copy-current');
  await until(`Boolean(window.qaCopied)`);
  assert.ok((await evaluate(`window.qaCopied`)).includes(data.project.id));
  assert.equal(await evaluate(`document.querySelector('#toast').hidden`),false);
  await evaluate(`(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('QA clipboard unavailable')}}});return true})()`);
  await click('#copy-current');
  assert.equal(await evaluate(`document.querySelector('#copy-dialog').open`),true);
  assert.ok((await evaluate(`document.querySelector('#command-text').value`)).includes(data.project.id));
  await click('[data-dialog="copy-dialog"]');
  pass('Copying continuation instructions gives feedback and falls back to selectable text');
  await click('#run-details > summary');
  await click('.run-cell');
  assert.ok((await evaluate(`document.querySelector('#run-detail').textContent`)).includes('第 1 次'));
  pass('Research nodes and experiment tiles respond to actual pointer clicks');

  await click('#about');
  assert.equal(await evaluate(`document.querySelector('#about-dialog').open`),true);
  await click('#close-about');
  assert.equal(await evaluate(`document.querySelector('#about-dialog').open`),false);
  pass('Data explanation dialog opens and closes');

  const originalId=data.project.id;
  const other=data.projects.find(p=>p.execution_state==='paused');
  if(other){
    await evaluate(`(()=>{const n=document.querySelector('#project');n.value=${JSON.stringify(other.id)};n.dispatchEvent(new Event('change'));return true})()`);
    await loaded();
    assert.equal(await evaluate(`document.querySelector('#project-id').textContent`),other.id);
    assert.ok((await evaluate(`document.querySelector('#project-state').textContent`)).includes('暂停'));
    await click('#copy-current');
    assert.ok((await evaluate(`document.querySelector('#command-text').value`)).includes('保留暂停状态'));
    await click('[data-dialog="copy-dialog"]');
    assert.equal(await evaluate(`document.documentElement.scrollWidth<=window.innerWidth`),true);
    pass('Switching to the paused project preserves its paused presentation');
  }
  await evaluate(`(()=>{const n=document.querySelector('#project');n.value=${JSON.stringify(originalId)};n.dispatchEvent(new Event('change'));return true})()`);
  await loaded();
  const before=await evaluate(`document.querySelector('#stat-total').textContent`);
  await evaluate(`(()=>{window.qaFetch=window.fetch;window.fetch=()=>Promise.reject(new TypeError('QA connection failure'));return true})()`);
  await click('#refresh');
  await loaded();
  assert.equal(await evaluate(`document.querySelector('#notice').hidden`),false);
  assert.equal(await evaluate(`document.querySelector('#stat-total').textContent`),before);
  assert.equal(await evaluate(`document.body.classList.contains('offline')`),true);
  await evaluate(`(()=>{window.fetch=window.qaFetch;delete window.qaFetch;return true})()`);
  await click('#refresh');
  await loaded();
  assert.equal(await evaluate(`document.querySelector('#notice').hidden`),true);
  assert.equal(await evaluate(`document.body.classList.contains('offline')`),false);
  pass('Failed refresh keeps previous data, labels it and recovers on retry');

  await evaluate(`(()=>{window.qaEmptyFetch=window.fetch;window.fetch=()=>Promise.resolve(new Response(JSON.stringify({schema_version:1,generated_at:new Date().toISOString(),mode:'recorded_state',projects:[],project:null}),{headers:{'Content-Type':'application/json'}}));return true})()`);
  await click('#refresh');
  await loaded();
  assert.equal(await evaluate(`document.querySelector('#goto-current').disabled`),true);
  assert.ok((await evaluate(`document.querySelector('#copy-label').textContent`)).includes('开始研究'));
  await click('#copy-current');
  assert.ok((await evaluate(`document.querySelector('#command-text').value`)).includes('研究领域'));
  await click('[data-dialog="copy-dialog"]');
  await evaluate(`(()=>{window.fetch=window.qaEmptyFetch;delete window.qaEmptyFetch;return true})()`);
  await click('#refresh');
  await loaded();
  pass('First project empty state provides a start template and restores real data');

  for(const artifact of data.project.outputs){
    const response=await fetch('http://127.0.0.1:8765'+artifact.url);
    assert.equal(response.status,200);
    const bytes=Buffer.from(await response.arrayBuffer());
    assert.equal(createHash('sha256').update(bytes).digest('hex'),artifact.sha256);
  }
  pass('All current PDF/PNG outputs return bytes matching registered SHA256');

  await click('#goto-current');
  if(await evaluate(`document.querySelector('#run-details').open`))await click('#run-details > summary');
  await send('browsingContext.setViewport',{context,viewport:{width:390,height:844},devicePixelRatio:1});
  await evaluate(`(()=>{scrollTo({top:0,behavior:'instant'});return true})()`);
  assert.equal(await evaluate(`document.documentElement.scrollWidth<=window.innerWidth`),true);
  const tooWide=await evaluate(`[...document.querySelectorAll('main section,main aside,header,footer')].filter(n=>n.getBoundingClientRect().right>window.innerWidth+1).map(n=>n.className)`);
  assert.deepEqual(tooWide,[]);
  assert.equal(await evaluate(`[...document.querySelectorAll('.stat-value')].every(n=>n.scrollWidth<=n.clientWidth+1)`),true);
  assert.ok(await evaluate(`[...document.querySelectorAll('.file-size')].every(n=>n.scrollWidth<=n.clientWidth+1)`));
  await until(`document.querySelector('#toast').hidden`);
  await send('input.performActions',{context,actions:[{type:'pointer',id:'mouse',parameters:{pointerType:'mouse'},actions:[{type:'pointerMove',x:2,y:2}]}]});
  await new Promise(r=>setTimeout(r,350));
  const mobile=await send('browsingContext.captureScreenshot',{context,origin:'document'});
  await writeFile(output+'/mobile.png',Buffer.from(mobile.data,'base64'));
  pass('390 px mobile layout without horizontal overflow, screenshot captured after data loaded');

  await send('browsingContext.setViewport',{context,viewport:{width:320,height:700},devicePixelRatio:1});
  assert.equal(await evaluate(`document.documentElement.scrollWidth<=window.innerWidth`),true);
  assert.equal(await evaluate(`[...document.querySelectorAll('.stat-value')].every(n=>n.scrollWidth<=n.clientWidth+1)`),true);
  pass('320 px narrow layout and large counters without horizontal overflow');
  await evaluate(`(()=>{window.qaLiveFetch=window.fetch;window.qaLiveRequests=0;window.fetch=(...args)=>{if(String(args[0]).includes('/api/snapshot'))window.qaLiveRequests++;return window.qaLiveFetch(...args)};return true})()`);
  for(let i=0;i<40;i++){
    if(await evaluate(`window.qaLiveRequests>0`))break;
    await new Promise(r=>setTimeout(r,1000));
  }
  await loaded();
  assert.ok(await evaluate(`window.qaLiveRequests>0`));
  await evaluate(`(()=>{window.fetch=window.qaLiveFetch;delete window.qaLiveFetch;delete window.qaLiveRequests;return true})()`);
  pass('Visible page automatically synchronizes milestone progress');
  assert.deepEqual(browserErrors,[]);
  pass('No browser JavaScript or content-security-policy errors');
  await writeFile(output+'/browser-checks.json',JSON.stringify({generated_at:new Date().toISOString(),browser:{name:capabilities.browserName,version:capabilities.browserVersion},checks,browser_errors:browserErrors,project_id:originalId},null,2)+'\n');
}finally{
  try{await send('session.end');}catch{}
  ws.close();
}
