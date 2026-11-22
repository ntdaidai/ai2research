// Optional accessibility check: Firefox BiDi on port 9223, reduced motion enabled.
import assert from "node:assert/strict";
import {writeFile} from "node:fs/promises";

const ws = new WebSocket("ws://127.0.0.1:9223/session");
await new Promise((resolve,reject) => { ws.onopen=resolve; ws.onerror=reject; });
let sequence=0;
const pending=new Map();
ws.onmessage=event => {
  const message=JSON.parse(event.data);
  const request=pending.get(message.id);
  if (!request) return;
  pending.delete(message.id);
  if (message.type === "error") request.reject(new Error(message.message));
  else request.resolve(message.result);
};
function send(method,params={}) {
  return new Promise((resolve,reject) => {
    const id=++sequence;
    pending.set(id,{resolve,reject});
    ws.send(JSON.stringify({id,method,params}));
  });
}
let context;
async function evaluate(expression) {
  const result=await send("script.evaluate",{expression:`JSON.stringify(${expression})`,target:{context},awaitPromise:true});
  if (result.type === "exception") throw new Error(result.exceptionDetails.text);
  return JSON.parse(result.result.value);
}
try {
  const session=await send("session.new",{capabilities:{}});
  context=(await send("browsingContext.getTree")).contexts[0].context;
  await send("browsingContext.navigate",{context,url:"http://127.0.0.1:8765",wait:"complete"});
  for(let i=0;i<100;i++) {
    if (await evaluate(`document.querySelector('main')?.getAttribute('aria-busy')==='false'`)) break;
    await new Promise(resolve => setTimeout(resolve,100));
  }
  assert.equal(await evaluate(`matchMedia('(prefers-reduced-motion: reduce)').matches`),true);
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.art-flow')).animationName`),"none");
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.art-float')).animationName`),"none");
  assert.equal(await evaluate(`document.querySelector('#hero-ring').getAnimations().length`),0);
  assert.equal(await evaluate(`document.querySelector('#run-chart').getAnimations().length`),0);
  assert.equal(await evaluate(`document.querySelectorAll('.progress-node').length`),12);
  const result={generated_at:new Date().toISOString(),browser:session.capabilities.browserName,
    version:session.capabilities.browserVersion,reduced_motion:true,checks:[
      "OS preference is recognized", "Flow and floating illustration are static",
      "Progress ring and chart have no animations", "Research progress remains available"
    ]};
  await writeFile("research/showcase/reduced-motion-checks.json",JSON.stringify(result,null,2)+"\n");
  process.stdout.write("PASS Reduced motion: illustrations, ring and chart are static; research progress remains available\n");
} finally {
  try { await send("session.end"); } catch {}
  ws.close();
}
