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
    renderMission:()=>{ctx.renders++}, renders:0,
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
