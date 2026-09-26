const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/missions.js'), 'utf8');
function fixture() {
  const pending = [];
  const nodes = {'#missions-dialog':{open:true}, '#missions-health':{textContent:''}, '#mission-list':{replaceChildren(){},append(){}}};
  const ctx = {
    missionLoading:false, missionSnapshot:'', missionSelection:null,
    state:{project:'one'}, $:id=>nodes[id], $$:()=>[],
    api:()=>new Promise(resolve=>pending.push(resolve)),
    renderMission:()=>{ctx.renders++}, renders:0, executionUnavailable:()=>'',
  };
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('async function loadMissions('), source.indexOf('async function showMissions(')),ctx);
  return {ctx,pending,nodes};
}
const data = () => ({missions:[],capabilities:{controller:'Running',search_status:'Ready'}});
test('polling preserves an answer being written', async()=>{
  const {ctx,pending}=fixture();ctx.$$=()=>[{value:'My pending answer'}];
  const request=ctx.loadMissions();pending[0](data());await request;
  assert.equal(ctx.renders,0);
});
test('polling preserves edited model and budget settings', async()=>{
  const {ctx,pending}=fixture();
  ctx.$$=selector=>selector.includes('form') ? [{dataset:{dirty:'true'}}] : [];
  const request=ctx.loadMissions();pending[0](data());await request;
  assert.equal(ctx.renders,0);
});
test('a late response cannot render the previous project',async()=>{
  const {ctx,pending}=fixture();const request=ctx.loadMissions();ctx.state.project='two';
  pending[0](data());await request;assert.equal(ctx.renders,0);
});
test('only one poll runs concurrently and identical state is not rerendered',async()=>{
  const {ctx,pending}=fixture();const request=ctx.loadMissions();await ctx.loadMissions();
  assert.equal(pending.length,1);pending[0](data());await request;assert.equal(ctx.renders,1);
  const next=ctx.loadMissions();pending[1](data());await next;assert.equal(ctx.renders,1);
});
test('closing the dialog during a request leaves the view unchanged',async()=>{
  const {ctx,pending,nodes}=fixture();const request=ctx.loadMissions();nodes['#missions-dialog'].open=false;
  pending[0](data());await request;assert.equal(ctx.renders,0);
});
test('selection reads current draft state after a successful save instead of a captured stale flag',async()=>{
 const {ctx,pending}=fixture();let dirty=true,buttons=[],warnings=[];
 ctx.$$=selector=>selector.includes('form') && dirty ? [{dataset:{dirty:'true'}}] : [];
 ctx.toast=message=>warnings.push(message);
 ctx.missionButton=(label,fn)=>{const button={onclick:fn,classList:{toggle(){}}};buttons.push(button);return button;};
 ctx.renderMission=()=>{dirty=false;};
 const request=ctx.loadMissions(true);pending[0]({...data(),missions:[{id:'a',title:'A',status:'paused'}]});
 ctx.missionLabels={paused:'Paused'};await request;
 const select=buttons[0].onclick();assert.equal(pending.length,2);pending[1]({...data(),missions:[]});await select;
 assert.equal(warnings.length,0);
 dirty=true;await buttons[0].onclick();assert.equal(warnings.length,1);
});
function make(tag,cls,text){return {tag,className:cls,textContent:text===undefined?'':String(text),children:[],dataset:{},
  append(...n){this.children.push(...n);},prepend(...n){this.children.unshift(...n);},replaceChildren(...n){this.children=n;},querySelector(){return null;},setAttribute(){}};}
function walk(node,out=[]){for(const c of node.children||[]){out.push(c);walk(c,out);}return out;}
test('opening the working version leaves the dashboard and shows the workspace',async()=>{
  const panel=make('section'),calls=[];
  const ctx={$:s=>s==='#mission-detail'?panel:{close(){calls.push('close');}},el:make,Date,JSON,state:{project:'main',data:{profiles:[]}},
    missionButton:(label,fn)=>Object.assign(make('button','button',label),{onclick:fn}),keepPanelState:(p,s,build)=>build(),
    refreshState:async()=>calls.push('refresh'),selectProject:async id=>{ctx.state.project=id;},showDashboard:v=>calls.push(['dashboard',v]),toast(){}};
  vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('const missionLabels'),source.indexOf('async function loadMissions(')),ctx);
  ctx.renderMission({id:'m',title:'T',status:'running',message:'',goal:'g',criteria:[],attempts:[],max_attempts:5,days:1,profile:'w',review_profile:'r',questions:[],tasks:[],work_project:'copy'});
  await walk(panel).find(n=>n.textContent==='Open working version in editor').onclick();
  assert.equal(ctx.state.project,'copy');assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))),['dashboard',false]);
});
test('mission sections carry stable keys so polling keeps them open',()=>{
  const panel=make('section');
  const ctx={$:()=>panel,el:make,Date,JSON,state:{project:'main',data:{profiles:[]}},missionButton:(label,fn)=>Object.assign(make('button','button',label),{onclick:fn}),keepPanelState:(p,s,build)=>build()};
  vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('const missionLabels'),source.indexOf('async function loadMissions(')),ctx);
  ctx.renderMission({id:'m',title:'T',status:'running',message:'',goal:'g',criteria:[],attempts:[{id:'a',phase:'build',started:1}],max_attempts:5,days:1,profile:'w',review_profile:'r',questions:[],tasks:[{id:'t1',title:'One',status:'pending',instructions:'i',depends_on:[],criteria:[],artifacts:[]}]});
  const keys=walk(panel).filter(n=>n.tag==='details').map(n=>n.dataset.key);
  for(const key of ['result','timeline','task:t1','attempts'])assert.ok(keys.includes(key),key);
});
test('answering a recovery question on blocked work says it continues and reports the outcome',async()=>{
  const panel=make('section'),toasts=[],sent=[];
  const ctx={$:()=>panel,el:make,Date,JSON,Map,state:{project:'main',data:{profiles:[]}},missionButton:(label,fn)=>Object.assign(make('button','button',label),{onclick:fn}),keepPanelState:(p,s,build)=>build(),
    toast:(message,error=false)=>toasts.push([message,error]),loadMissions:async()=>{},runnableProfiles:()=>[],populateDecisionModels(){},missionAnswers:new Map(),
    api:async(url,body)=>{sent.push(body);return {id:'m',status:'blocked',message:'Answer saved; the execution stays blocked: The run budget is exhausted.'};}};
  vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('const missionLabels'),source.indexOf('async function loadMissions(')),ctx);
  const mission=questions=>({id:'m',title:'T',status:'blocked',message:'',goal:'g',criteria:[],attempts:[],max_attempts:5,days:1,profile:'w',review_profile:'r',questions,tasks:[]});
  ctx.renderMission(mission([{id:'q',kind:'recovery',question:'How should the company continue?',reason:'Block',answer:null,task:null}]));
  const form=walk(panel).find(n=>n.tag==='form'&&n.className==='mission-question');
  assert.ok(walk(form).some(n=>n.textContent==='Save response and continue'));
  assert.ok(walk(form).some(n=>/continues the blocked work/.test(n.textContent)));
  walk(form).find(n=>n.tag==='textarea').value='continue';await form.onsubmit({preventDefault(){}});
  assert.equal(sent[0].action,'answer');assert.deepEqual(toasts,[['Answer saved; the execution stays blocked: The run budget is exhausted.',true]]);
  panel.children=[];ctx.renderMission(mission([{id:'q2',kind:null,question:'Which host?',reason:'',answer:null,task:'t1'}]));
  assert.ok(walk(panel).some(n=>n.textContent==='Save response'),'an agent question keeps the plain label');
});
