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
    companyData:null,$:id=>nodes[id],api:()=>new Promise(resolve=>pending.push(resolve)),renders:0,renderDriverCompany:()=>{ctx.renders++;},
    executionUnavailable:()=>ctx.unavailable||''};
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

// Plain-object DOM double for the company detail view.
function make(tag,cls,text){return {tag,className:cls,textContent:text===undefined?'':String(text),children:[],dataset:{},disabled:false,
  append(...n){this.children.push(...n);},prepend(...n){this.children.unshift(...n);},replaceChildren(...n){this.children=n;},setAttribute(){}};}
function walk(node,out=[]){for(const c of node.children||[]){out.push(c);walk(c,out);}return out;}
function driverFixture(){
  const panel=make('section');
  const ctx={$:s=>s==='#driver-detail'?panel:make('x'),$$:()=>[],el:make,state:{data:{projects:[{id:'p',name:'P',path:'/p'}]}},Date,
    missionButton:(label,fn,primary)=>Object.assign(make('button','button',label),{onclick:fn,primary}),
    keepPanelState:(p,scope,build)=>{p.children=[];build();}};
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('const companyStatus'),source.indexOf('async function loadCompanies(')),ctx);
  ctx.actions=[];ctx.flows=[];ctx.companyAction=async(c,action,extra)=>ctx.actions.push([action,extra]);ctx.showFlow=id=>ctx.flows.push(id);
  return {ctx,panel};
}
const companyWith=(tasks,extra={})=>({id:'c',name:'Co',status:'paused',purpose:'Goal',projects:['p'],usage:{used_runs:1,reserved_runs:0,remaining_runs:10},cycles:[],
  policy:{cycle_limit:5,days:7,run_budget:50,attempts_per_cycle:5,attempt_minutes:30,max_turns:40,auto_tools:false,auto_accept:false},
  message:'',deadline:null,inbox:[],products:[],events:[],tasks,...extra});
const task=(id,status,extra={})=>({id,title:'Task '+id,status,department:'delivery',priority:2,enabled:true,goal:'g',criteria:['c'],interval_hours:0,max_cycles:1,depends_on:[],project:'p',kind:'work',last_mission:null,...extra});
test('live progress map never falls back to an unrelated mission',async()=>{
  const {ctx,panel}=driverFixture();ctx.renderDriverCompany(companyWith([task('t','queued')]),{departments:{delivery:'Delivery'}});
  const button=walk(panel).find(n=>/^Live progress map/.test(n.textContent));
  assert.equal(button.disabled,true);assert.match(button.textContent,/no execution yet/);
  ctx.renderDriverCompany(companyWith([task('t','running',{last_mission:'m1'})]),{departments:{delivery:'Delivery'}});
  const live=walk(panel).find(n=>n.textContent==='Live progress map');assert.equal(live.disabled,false);await live.onclick();assert.deepEqual(ctx.flows,['m1']);
});
test('cancelled and expired tasks can be requeued; recovery events are visible',async()=>{
  const {ctx,panel}=driverFixture();
  const events=[{at:10,action:'recovery',task:'x',message:'Driver recovery 2/3: time_limit'},{at:5,action:'dispatch',task:'x',mission:'m'}];
  ctx.renderDriverCompany(companyWith([task('x','expired'),task('y','cancelled'),task('z','queued'),task('e','cancelled',{kind:'external'})],{events}),{departments:{delivery:'Delivery'}});
  const requeue=walk(panel).filter(n=>n.textContent==='Requeue');assert.equal(requeue.length,2,'only cancelled or expired agent work can be requeued');
  // The backend action is requeue_task (companies.py); any other name fails with "Unknown company action."
  await requeue[0].onclick();assert.deepEqual(JSON.parse(JSON.stringify(ctx.actions[0])),['requeue_task',{task:'x'}]);
  const text=walk(panel).map(n=>n.textContent);
  assert.ok(text.includes('Driver recovery'));assert.ok(text.some(t=>/Driver recovery 2\/3: time_limit/.test(t)));
  assert.ok(text.some(t=>t==='Last recovery: Driver recovery 2/3: time_limit'));
});
test('company notes describe the bubblewrap runtime instead of an unsandboxed native one',()=>{
  const {ctx,panel}=driverFixture();ctx.renderDriverCompany(companyWith([]),{departments:{delivery:'Delivery'}});
  const text=walk(panel).map(n=>n.textContent).join('\n');
  assert.match(text,/bubblewrap sandbox/);assert.doesNotMatch(text,/not sandboxed|user's permissions/);
});
test('refresh overview discards every draft flag and forces a re-render',async()=>{
  const handlers={},forms=[{dataset:{dirty:'true'}},{dataset:{dirty:'true'}}],calls=[];
  const ctx={companyDirty:true,driverEpoch:0,companySnapshot:'old',confirm:()=>true,$$:()=>forms,$:()=>({replaceChildren(){calls.push('clear');}}),
    bind:(s,e,fn)=>{handlers[s]=fn;},setInterval(){},showCompanies:force=>calls.push(['show',force]),loadCompanies:async()=>{},toast(){}};
  vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function initCompanies('),source.indexOf('// Global inbox:')),ctx);ctx.initCompanies();
  await handlers['#driver-refresh']();
  assert.equal(ctx.companyDirty,false);assert.equal(ctx.companySnapshot,'');assert.ok(forms.every(f=>f.dataset.dirty===undefined));
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),['clear',['show',true]]);
  await handlers['#driver-button']({type:'click'});assert.equal(calls.at(-1)[0],'show');assert.ok(!calls.at(-1)[1],'a click event is not a force flag');
});
test('an unsaved company form is reported in English and execution limits are surfaced',async()=>{
  const {ctx,pending,nodes}=fixture();ctx.companyDirty=true;ctx.unavailable='Workers need Linux with bubblewrap.';
  const call=ctx.loadCompanies();pending[0](data());await call;
  assert.match(nodes['#driver-health'].textContent,/^Agent execution is unavailable on this host: Workers need Linux/);
  assert.match(nodes['#driver-health'].textContent,/Unsaved form changes; the display was not refreshed\./);
});
