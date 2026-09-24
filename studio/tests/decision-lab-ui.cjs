const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../static/decision-lab.js'),'utf8');
function fixture() {
  const pending=[], dialog={open:true};
  const ctx={decisionLabProject:'one',decisionLabGeneration:1,decisionLabLoading:false,decisionLabSnapshot:'',
    $:()=>dialog,api:()=>new Promise(resolve=>pending.push(resolve)),renders:0,renderDecisionLab:()=>{ctx.renders++;}};
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('async function loadDecisionLab('),source.indexOf('function initDecisionLab(')),ctx);
  return {ctx,pending,dialog};
}
test('lab serializes polling and retains unchanged results',async()=>{
  const {ctx,pending}=fixture();const first=ctx.loadDecisionLab();await ctx.loadDecisionLab();
  assert.equal(pending.length,1);pending[0]({evaluations:[]});await first;assert.equal(ctx.renders,1);
  const next=ctx.loadDecisionLab();pending[1]({evaluations:[]});await next;assert.equal(ctx.renders,1);
});
test('lab discards old dialog and project responses',async()=>{
  for(const mode of ['close','generation','project']) {
    const {ctx,pending,dialog}=fixture();const request=ctx.loadDecisionLab();
    if(mode==='close')dialog.open=false;else if(mode==='generation')ctx.decisionLabGeneration++;else ctx.decisionLabProject='two';
    pending[0]({evaluations:[]});await request;assert.equal(ctx.renders,0);
  }
});
test('event binding prevents default synchronously and reports sync and async errors',async()=>{
  const app=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
  let listener;const errors=[];const ctx={$:()=>({addEventListener:(_,fn)=>{listener=fn;}}),toast:msg=>errors.push(msg)};
  vm.createContext(ctx);vm.runInContext(app.slice(app.indexOf('function bind('),app.indexOf('function project(')),ctx);
  let prevented=false;ctx.bind('form','submit',async e=>{e.preventDefault();throw new Error('async');});
  listener({preventDefault(){prevented=true;}});assert.equal(prevented,true);await new Promise(resolve=>setImmediate(resolve));
  ctx.bind('form','submit',()=>{throw new Error('sync');});listener({});assert.deepEqual(errors,['async','sync']);
});
