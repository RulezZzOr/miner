const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/office.js'),'utf8');
// The office shares the flow map's obstacle detection (companies.js loads first in the page).
const companies=fs.readFileSync(path.join(__dirname,'../static/companies.js'),'utf8');
const obstacle=companies.slice(companies.indexOf('function isToolObstacle('),companies.indexOf('function flowModel('));
function fixture(extra={}){const ctx=vm.createContext({Date,...extra});vm.runInContext(obstacle+source,ctx);return ctx;}
const ctx=fixture();
const run={id:'r',status:'running',model:'local-coder',profile:'local',created:900};
const mission={id:'m',title:'Inventory',status:'running',phase:'build',active_attempt:'r',attempts:[],tasks:[{status:'pending'}]};
const company={id:'c',name:'Test company',status:'active',tasks:[{id:'t',title:'Inventory',department:'operations',last_mission:'m'}]};
const data={companies:[company],missions:[mission],runs:[{...run,mission:{id:'m'}}]};
const recent={r:{events:[{type:'tool_call',name:'read_file',time:999}],approvals:[],loading:false}};
test('fresh activity maps a real company task to exactly one occupied desk',()=>{const m=ctx.officeModel(data,'',recent,1000);assert.equal(m.items.length,1);assert.equal(m.active,1);assert.equal(m.items[0].department,'operations');assert.equal(m.items[0].status,'working');});
test('a running flag alone never produces work animation',()=>{const m=ctx.officeModel(data,'',{},1000);assert.equal(m.active,0);assert.equal(m.items[0].status,'stale');});
test('old events and incomplete event pages remain unconfirmed',()=>{for(const log of [{events:[{time:600}]},{events:[{time:999}],loading:true}])assert.equal(ctx.officeItemStatus(mission,run,log,1000),'stale');});
test('blocked and paused controller states override a leftover running process',()=>{for(const status of ['blocked','paused'])assert.equal(ctx.officeItemStatus({...mission,status},run,recent.r,1000),status);});
test('approval and review are distinguishable from execution',()=>{assert.equal(ctx.officeItemStatus(mission,run,{...recent.r,approvals:[{id:'a'}]},1000),'waiting');assert.equal(ctx.officeItemStatus({...mission,phase:'review'},run,recent.r,1000),'review');});
test('a rejected tool is a blocker rather than productive motion',()=>{assert.equal(ctx.officeItemStatus(mission,run,{events:[{type:'note',time:999,text:'blocked: shell is denied'}]},1000),'blocked');});
test('a completed standalone run is not claimed as an accepted product',()=>{const m=ctx.officeModel({runs:[{...run,status:'completed'}]},'',{},1000);assert.equal(m.items[0].status,'finished');assert.equal(m.done,0);});
test('accepted products count only the recorded accepted state',()=>{const m=ctx.officeModel({...data,missions:[{...mission,status:'accepted',active_attempt:null}]},'',{},1000);assert.equal(m.done,1);assert.equal(m.active,0);});
test('a selected company excludes unrelated missions and runs',()=>{assert.equal(ctx.officeModel(data,'other',recent,1000).items.length,0);assert.equal(ctx.officeModel(data,'c',recent,1000).items.length,1);});
test('unstarted tasks remain queued or paused with no fake model assignment',()=>{for(const status of ['active','paused']){const m=ctx.officeModel({companies:[{...company,status,tasks:[{id:'t',title:'Read server',department:'platform'}]}]},'',{},1000);assert.equal(m.items[0].status,status==='paused'?'paused':'queued');assert.equal(m.items[0].model,null);}});
test('all tasks stay in the list even when a department has more than four seats',()=>{const m=ctx.officeModel({companies:[{...company,tasks:Array.from({length:7},(_,i)=>({id:String(i),title:'Task '+i,department:'delivery'}))}]},'',{},1000);assert.equal(m.departments.find(d=>d.id==='delivery').items.length,7);});
function polling(api){const nodes={'#office-dialog':{open:true,classList:{add(){},remove(){}}},'#office-active-count':{textContent:'1'},'#office-sync':{textContent:''},'#office-company':{dataset:{},replaceChildren(){},append(){}}};const c=fixture({api,$:id=>nodes[id],el:()=>({}),renders:0});c.renderOffice=()=>c.renders++;c.renderOfficeDetail=()=>{};return {c,nodes};}
test('offline state explicitly invalidates the active-now count',async()=>{const {c,nodes}=polling(async()=>{throw Error('offline');});await c.loadOffice();assert.equal(nodes['#office-active-count'].textContent,'—');assert.match(nodes['#office-sync'].textContent,/Offline.*outdated/);assert.equal(c.renders,0);});
test('late replies after closing or switching context cannot repaint the office',async()=>{const pending=[];const {c}=polling(()=>new Promise(resolve=>pending.push(resolve)));const request=c.loadOffice();vm.runInContext('officeState.epoch++',c);for(const resolve of pending)resolve({companies:[],missions:[],runs:[]});await request;assert.equal(c.renders,0);});
test('office polling is read-only and does not overlap',async()=>{const pending=[],paths=[];const {c}=polling((...args)=>{assert.equal(args.length,1);paths.push(args[0]);return new Promise(resolve=>pending.push(resolve));});const one=c.loadOffice();await c.loadOffice();assert.equal(paths.length,3);for(const resolve of pending)resolve({companies:[],missions:[],runs:[]});await one;assert.equal(c.renders,1);});
test('a blocked execution exposes its last reviewer rather than the worker profile',()=>{const m=ctx.officeModel({...data,missions:[{...mission,status:'blocked',active_attempt:null,profile:'worker',attempts:[{id:'r',phase:'review'}]}],runs:[{...run,status:'cancelled',profile:'reviewer',model:'review-model',mission:{id:'m'}}]},'',{},1000);assert.equal(m.items[0].model,'review-model');assert.equal(m.items[0].phase,'review');assert.equal(m.items[0].run,'r');assert.equal(m.items[0].status,'blocked');assert.equal(m.active,0);});

test('active per-task review overrides the encompassing build phase',()=>{
  for(const withRunPhase of [true,false]) {
    const m=ctx.officeModel({...data,
      missions:[{...mission,phase:'build',attempts:[{id:'r',phase:'review'}]}],
      runs:[{...run,mission:{id:'m',...(withRunPhase?{phase:'review'}:{})}}]
    },'',recent,1000);
    assert.equal(m.items[0].phase,'review');
    assert.equal(m.items[0].status,'review');
    assert.equal(m.active,1);
  }
});

test('English worker notes for blocked, denied and owner-rejected tools are obstacles',()=>{
  for(const text of ['✗ rejected bash — stopping task (use [e] to redirect instead)','✗ bash blocked: file was not read first','✗ blocked: destructive command','✗ plan mode: \'edit_file\' is disabled until the plan is approved'])
    assert.equal(ctx.officeItemStatus(mission,run,{events:[{type:'tool_call',time:990},{type:'note',time:999,text}]},1000),'blocked',text);
  for(const text of ['✓ always allowing: bash ls','↳ redirecting bash: use staging','✓ plan approved — edits unlocked'])
    assert.equal(ctx.officeItemStatus(mission,run,{events:[{type:'note',time:999,text}]},1000),'working',text);
});
test('controller errors are shown as a warning while the office keeps rendering current data',async()=>{
  const {c,nodes}=polling(async path=>path==='/api/companies'?{companies:[],controller_error:'Driver requires attention: disk full'}:path==='/api/missions'?{missions:[],controller_error:''}:{runs:[]});
  await c.loadOffice();assert.equal(c.renders,1);assert.equal(vm.runInContext('officeState.offline',c),false);
  assert.equal(vm.runInContext('officeState.warning',c),'Driver requires attention: disk full');
});
test('open company goes through the dashboard path that resets the company snapshot',async()=>{
  const opened=[];let closed=false;const panel={dataset:{},children:[],replaceChildren(){this.children=[];},append(...n){this.children.push(...n);}};
  const c=fixture({$:id=>id==='#office-detail'?panel:{close(){closed=true;}},el:(tag,cls,text)=>({tag,cls,textContent:text,children:[],append(...n){this.children.push(...n);}}),
    missionButton:(label,fn)=>({label,fn}),dashboardOpenCompany:async id=>opened.push(id)});
  vm.runInContext("officeState.model={items:[{id:'task:c2:t',title:'Audit',status:'queued',company:'c2',companyName:'B'}]};officeState.selected='task:c2:t';",c);
  c.renderOfficeDetail();const button=panel.children.find(n=>n.cls==='office-detail-actions').children.find(b=>b.label==='Open company');
  await button.fn();assert.deepEqual(opened,['c2']);assert.equal(closed,true);
});
