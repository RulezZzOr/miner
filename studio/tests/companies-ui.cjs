const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/companies.js'), 'utf8');
function fixture() {
  const pending=[];
  const nodes={'#driver-dialog':{open:true},'#driver-health':{textContent:''},'#driver-list':{replaceChildren(){},append(){}}};
  const ctx={companyLoading:false,companyMutating:false,driverEpoch:0,companyDirty:false,companySnapshot:'',companySelection:null,
    companyData:null,$:id=>nodes[id],api:()=>new Promise(resolve=>pending.push(resolve)),renders:0,renderDriverCompany:()=>{ctx.renders++;}};
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('async function loadCompanies('),source.indexOf('async function showCompanies(')),ctx);
  return {ctx,pending,nodes};
}
const data=()=>({companies:[],controller_error:''});
test('company polling never destroys an unsaved task or policy',async()=>{
  const {ctx,pending}=fixture();ctx.companyDirty=true;const call=ctx.loadCompanies();pending[0](data());await call;assert.equal(ctx.renders,0);
});
test('company polling cannot override a newer mutation or closed dialog',async()=>{
  for(const mode of ['mutation','close']) {
    const {ctx,pending,nodes}=fixture();const call=ctx.loadCompanies();
    if(mode==='mutation')ctx.driverEpoch++;else nodes['#driver-dialog'].open=false;
    pending[0](data());await call;assert.equal(ctx.renders,0);
  }
});
test('company polling serializes requests and preserves unchanged DOM',async()=>{
  const {ctx,pending}=fixture();const call=ctx.loadCompanies();await ctx.loadCompanies();assert.equal(pending.length,1);
  pending[0](data());await call;assert.equal(ctx.renders,1);
  const next=ctx.loadCompanies();pending[1](data());await next;assert.equal(ctx.renders,1);
});

test('all browser scripts share one valid global scope',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const sources=[...html.matchAll(/<script src="\/([^"]+)"/g)].map(m=>fs.readFileSync(path.join(__dirname,'../static',m[1]),'utf8'));
  assert.doesNotThrow(()=>new vm.Script(sources.join('\n')));
  const names = sources.flatMap(s=>[...s.matchAll(/^(?:async )?function (\w+)/gm)].map(m=>m[1]));
  assert.equal(new Set(names).size,names.length,'global function names must be unique');
});

function submitFixture() {
  const form={dataset:{dirty:'true'},children:[],append(...nodes){this.children.push(...nodes);}};
  const ctx={companyMutating:false,companySubmitting:false,el:()=>({textContent:'',setAttribute(){}}),
    $$:()=>[form],toast:()=>{}};
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('function companySubmit('),source.indexOf('function companyPanel(')),ctx);
  return {ctx,form};
}
test('rejected company save preserves draft and allows corrected retry',async()=>{
  const {ctx,form}=submitFixture();let attempts=0;
  ctx.companySubmit(form,'Save',async()=>{if(++attempts===1)throw new Error('Invalid criteria');});
  const button=form.children.find(n=>n.type==='submit');
  await form.onsubmit({preventDefault(){}});
  assert.equal(button.disabled,false,'A failed save must never strand the form');
  assert.equal(form.dataset.dirty,'true');
  assert.equal(ctx.companySubmitting,false);
  assert.ok(form.children.some(n=>n.textContent==='Invalid criteria'),'Error must be visible inside the modal');
  await form.onsubmit({preventDefault(){}});
  assert.equal(attempts,2);
  assert.equal(button.disabled,false);
  assert.ok(!form.children.some(n=>n.textContent==='Invalid criteria'));
});
test('company submit cannot send duplicate requests while awaiting response',async()=>{
  const {ctx,form}=submitFixture();let count=0,finish;
  ctx.companySubmit(form,'Save',()=>{count++;return new Promise(resolve=>{finish=resolve;});});
  const first=form.onsubmit({preventDefault(){}});
  await form.onsubmit({preventDefault(){}});
  assert.equal(count,1);finish();await first;
});
