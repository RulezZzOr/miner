const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/dashboard.js'),'utf8');
const company={id:'company-a',revision:7,name:'Example company',projects:['project-a'],status:'paused',tasks:[]};
const values={project:'project-a',destination:'company-a',who:'delivery',brief:'Build the page\nSave it in the project.',criteria:'index.html exists\nLinks work',priority:'2',checks:'npm test',constraints:'Do not publish',mode:'react',turns:'20'};
function fixture(){
 const nodes=new Map(), calls=[];
 const ctx={state:{dirty:false,data:{}},console,Set,Date,
  $:s=>{if(!nodes.has(s))nodes.set(s,{value:'',textContent:'',disabled:false,hidden:false});return nodes.get(s);},
  $$:()=>[...nodes.values()],dashboardRouting(){},loadDashboard:async()=>{},refreshState:async()=>{},
  api:async(url,body)=>{calls.push({url,body});return body?{status:'paused'}:{companies:[company]};}};
 vm.createContext(ctx);vm.runInContext(source,ctx);
 ctx.dashboardRouting=()=>{};ctx.loadDashboard=async()=>{};
 for(const [key,value] of Object.entries(values))ctx.$('#dashboard-'+key).value=value;
 return {ctx,nodes,calls};
}
test('company task uses selected department, latest revision, criteria and one-time limits',()=>{
 const {ctx}=fixture(), r=ctx.dashboardTaskPayload(values,company);
 assert.equal(r.url,'/api/companies/action');assert.equal(r.body.revision,7);assert.equal(r.body.department,'delivery');
 assert.deepEqual(Array.from(r.body.criteria),['index.html exists','Links work']);assert.equal(r.body.verification_checks,'npm test');
 assert.equal(r.body.max_cycles,1);assert.equal(r.body.interval_hours,0);assert.equal(r.body.auto_approve,undefined);
});
test('standalone task preserves instructions and never silently enables automatic actions',()=>{
 const {ctx}=fixture(),r=ctx.dashboardTaskPayload({...values,who:'worker-b',mode:'agent_team'},null);
 assert.equal(r.body.profile,'worker-b');assert.equal(r.body.mode,'agent_team');assert.equal(r.body.auto_approve,false);
 assert.match(r.body.task,/Done when:\n- index.html exists/);assert.match(r.body.task,/Constraints:\nDo not publish/);
});
test('empty criteria and stale project assignment are rejected',()=>{
 const {ctx}=fixture();assert.throws(()=>ctx.dashboardTaskPayload({...values,criteria:' \n '},company));
 assert.throws(()=>ctx.dashboardTaskPayload({...values,project:'project-b'},company));
});
test('concurrent submit cannot enqueue duplicate work and fetches fresh company revision',async()=>{
 const {ctx,calls}=fixture();let resolve;
 ctx.api=async(url,body)=>{calls.push({url,body});if(!body)return new Promise(r=>{resolve=r});return {status:'paused'};};
 const first=ctx.submitDashboardTask({preventDefault(){}});await ctx.submitDashboardTask({preventDefault(){}});
 assert.equal(calls.length,1);resolve({companies:[{...company,revision:11}]});await first;
 assert.equal(calls.length,2);assert.equal(calls[1].body.revision,11);
 assert.equal(ctx.$('#dashboard-brief').value,'');assert.match(ctx.$('#dashboard-task-feedback').textContent,/Driver is paused/);
});
test('server rejection keeps task draft and restores submit controls',async()=>{
 const {ctx}=fixture();ctx.api=async()=>{throw new Error('Revision conflict');};
 await ctx.submitDashboardTask({preventDefault(){}});
 assert.equal(ctx.$('#dashboard-brief').value,values.brief);assert.equal(ctx.$('#dashboard-submit').disabled,false);
 assert.match(ctx.$('#dashboard-task-feedback').textContent,/Revision conflict/);
});
test('successful save followed by refresh failure cannot be reported as an unsaved task',async()=>{
 const {ctx}=fixture();ctx.refreshState=async()=>{throw new Error('Offline');};await ctx.submitDashboardTask({preventDefault(){}});
 assert.equal(ctx.$('#dashboard-brief').value,'');assert.match(ctx.$('#dashboard-task-feedback').textContent,/task was saved/);
});
test('deleted company cannot fall through into a standalone model run',async()=>{
 const {ctx,calls}=fixture();ctx.api=async(url,body)=>{calls.push({url,body});return {companies:[]};};
 await ctx.submitDashboardTask({preventDefault(){}});assert.equal(calls.length,1);assert.equal(ctx.$('#dashboard-brief').value,values.brief);
});
test('overview distinguishes queued, disabled, completed and genuinely running work',()=>{
 const {ctx}=fixture();const c={...company,tasks:[{id:'queued',enabled:true,status:'queued',priority:2,created:1},{id:'off',enabled:false,status:'queued',priority:1,created:0},{id:'done',enabled:true,status:'done'}]};
 const data={companies:[c],missions:[]},result=ctx.dashboardSummary(data,[{id:'old',status:'completed'},{id:'live',status:'running'}]);
 assert.equal(result.queued,1);assert.deepEqual(Array.from(result.live,r=>r.id),['live']);assert.deepEqual(Array.from(result.tasks,t=>t.id),['queued','off']);
 assert.equal(ctx.dashboardSummary(data,[],'done').tasks[0].id,'done');
});
test('dashboard polling does not touch the task form and retains last snapshot on failure',async()=>{
 const {ctx}=fixture();ctx.document={body:{classList:{contains:()=>true}}};ctx.renderDashboard=()=>{};
 ctx.api=async(url)=>url==='/api/companies'?{companies:[company]}:{missions:[]};
 await ctx.loadDashboard(); // use the real polling function below
 vm.runInContext(source.slice(source.indexOf('async function loadDashboard('),source.indexOf('function dashboardTaskPayload(')),ctx);
 await ctx.loadDashboard();assert.equal(ctx.$('#dashboard-brief').value,values.brief);
 ctx.api=async()=>{throw new Error('Disconnected');};await ctx.loadDashboard();
 assert.match(ctx.$('#dashboard-sync').textContent,/Offline/);assert.equal(ctx.$('#dashboard-brief').value,values.brief);
});
test('home keeps the same approval inbox visible and preserves its draft across workspace navigation',()=>{
 const {ctx}=fixture();let position='workspace';const draft={value:'Use staging only'};
 const inbox={draft,before(anchor){assert.equal(anchor,marker);}},marker={after(node){assert.equal(node,inbox);position='workspace';}};
 const base=ctx.$;
 ctx.$=s=>s==='#approval-inbox'?inbox:s==='#dashboard-approval-slot'?{append(node){assert.equal(node,inbox);position='dashboard';}}:{...base(s),setAttribute(){},classList:{toggle(){}}};
 ctx.document={createComment:()=>marker,body:{classList:{toggle(){}}}};
 ctx.showDashboard(true);assert.equal(position,'dashboard');assert.equal(inbox.draft.value,'Use staging only');
 ctx.showDashboard(false);assert.equal(position,'workspace');assert.equal(inbox.draft,draft);
});
function routingFixture({runs=[],unavailable='',companies=[]}={}){
 const nodes=new Map();
 const node=()=>({value:'',textContent:'',disabled:false,hidden:false,dataset:{},options:[],selectedIndex:-1,
  replaceChildren(...o){this.options=o;if(!o.some(x=>x.value===this.value))this.value='';}});
 const ctx={Set,Date,JSON,state:{project:'project-a',data:{projects:[{id:'project-a',name:'A'}],default_profile:'gpt',runs,
   profiles:[{id:'local',model:'coder'},{id:'gpt',model:'gpt',oauth_provider:'chatgpt',backend:'codex'}]}},
  $:s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s);},el:(tag,cls,text)=>({tag,textContent:text,value:''}),
  runnableProfiles:()=>ctx.state.data.profiles.filter(p=>!p.oauth_provider&&p.backend!=='codex'),executionUnavailable:()=>unavailable};
 vm.createContext(ctx);vm.runInContext(source,ctx);vm.runInContext('dashboardData='+JSON.stringify({companies,departments:{delivery:'Delivery'}}),ctx);
 // Selects in the double keep their assigned value.
 for(const id of ['#dashboard-project','#dashboard-destination','#dashboard-who'])Object.defineProperty(ctx.$(id),'value',{get(){return this._v??'';},set(v){this._v=v;}});
 return ctx;
}
test('standalone routing offers only runnable models and refuses while the worker slot is busy',()=>{
 let ctx=routingFixture();ctx.dashboardRouting();
 assert.deepEqual(Array.from(ctx.$('#dashboard-who').options,o=>o.value),['local']);assert.equal(ctx.$('#dashboard-submit').disabled,false);
 ctx=routingFixture({runs:[{id:'r',status:'running'}]});ctx.dashboardRouting();
 assert.equal(ctx.$('#dashboard-submit').disabled,true);assert.match(ctx.$('#dashboard-routing').textContent,/A worker is busy/);
});
test('unavailable execution blocks standalone runs but still allows queueing company work with a warning',()=>{
 let ctx=routingFixture({unavailable:'Workers need Linux with bubblewrap.'});ctx.dashboardRouting();
 assert.equal(ctx.$('#dashboard-submit').disabled,true);assert.match(ctx.$('#dashboard-routing').textContent,/unavailable on this host/);
 ctx=routingFixture({unavailable:'Workers need Linux with bubblewrap.',runs:[{id:'r',status:'running'}],companies:[{...company,profile:'local',review_profile:'local'}]});ctx.dashboardRouting();
 assert.equal(ctx.$('#dashboard-destination').value,'company-a');assert.equal(ctx.$('#dashboard-submit').disabled,false);
 assert.match(ctx.$('#dashboard-routing').textContent,/queued work cannot run here/);
});
