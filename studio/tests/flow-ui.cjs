const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/companies.js'),'utf8').split('// Live mission flow map\n')[1];
const ctx={missionLabels:{running:"Pracuje",paused:"Pozastaveno",cancelled:"Zastaveno"},Date};vm.createContext(ctx);vm.runInContext(source,ctx);
function mission(extra={}){return {id:'m',status:'running',phase:'build',active_attempt:'a',attempts:[{id:'a',task:'one',started:990}],updated:990,message:'Work',questions:[],tasks:[{id:'one',title:'Inspect',status:'pending',depends_on:[]},{id:'two',title:'Docs',status:'pending',depends_on:['one']},{id:'three',title:'Integrations',status:'pending',depends_on:['one']}],...extra};}
const model=(m,events=[],extra={})=>ctx.flowModel(m,{events,approvals:[],...extra},1000);
test('only fresh active work animates, not a stale running flag',()=>{assert.equal(model(mission(),[{type:'content',time:998}]).moving,true);const stale=model(mission(),[{type:'content',time:700}]);assert.equal(stale.moving,false);assert.equal(stale.tone,'waiting');assert.match(stale.headline,/čerstvou/);});
test('manual approvals and unanswered questions stop motion',()=>{const approval=model(mission(),[],{approvals:[{name:'bash',reason:'Shell'}]});assert.equal(approval.moving,false);assert.match(approval.headline,/schválení/);const question=model(mission({questions:[{answer:null,question:'Which host?'}]}));assert.equal(question.reason,'Which host?');assert.equal(question.moving,false);});
test('denied SSH stays visible despite running status',()=>{const r=model(mission(),[{type:'note',time:998,text:'✗ blocked: ssh is denied'}]);assert.equal(r.tone,'blocked');assert.equal(r.current.id,'one');assert.equal(r.current.status,'blocked');assert.equal(r.moving,false);assert.match(r.reason,/ssh/);});
test('web tools returning a denial as successful text are still obstacles',()=>{const r=model(mission(),[{type:'tool_result',time:998,is_error:false,text:'[1] URL: http://host/\n    Info: Blocked: refusing non-public address'}]);assert.equal(r.tone,'blocked');});
test('real dependency branches share a column, never become a fake linear sequence',()=>{const r=model(mission());assert.equal(r.nodes.find(n=>n.id==='two').rank,r.nodes.find(n=>n.id==='three').rank);assert.ok(r.edges.some(e=>e.from==='one'&&e.to==='three'));assert.ok(!r.edges.some(e=>e.from==='two'&&e.to==='three'));});
test('tool results do not mark a pending task as done',()=>{const r=model(mission(),[{type:'tool_result',time:999,is_error:false,text:'OK'}]);assert.equal(r.done,0);assert.notEqual(r.current.status,'done');});
test('manual acceptance does not claim automated checks passed',()=>{const r=model(mission({status:'accepted',active_attempt:null,acceptance:{kind:'manual'}}));assert.equal(r.nodes.find(n=>n.id==='@checks').status,'paused');assert.equal(r.nodes.find(n=>n.id==='@accept').label,"Převzato");assert.equal(r.moving,false);});
test('cancelled work never displays old run as current activity',()=>{const r=model(mission({status:'cancelled',active_attempt:null}),[{type:'content',time:999}]);assert.equal(r.moving,false);assert.equal(r.nodes.find(n=>n.id==='one').status,'pending');});
test('incomplete event pagination cannot produce fake activity',()=>{assert.equal(model(mission(),[{type:'content',time:999}],{loading:true}).moving,false);});
test('review is attached to its actual task and completed tasks retain their state',()=>{const m=mission({phase:'review'});m.tasks[2].status='done';const r=model(m);assert.match(r.current.subtitle,/kontrola/i);assert.equal(r.done,1);});
function pollingFixture(api){
 const nodes={'#flow-dialog':{open:true,classList:{remove(){},add(){}}},'#flow-mission':{dataset:{},replaceChildren(){}},'#flow-sync':{textContent:''}};
 const c={missionLabels:{},Date,api,$:id=>nodes[id],el:()=>({}),rendered:0};vm.createContext(c);vm.runInContext(source,c);c.renderFlow=()=>c.rendered++;return {c,nodes};
}
test('event pagination drains past the first 600 token events',async()=>{
 const offsets=[],m=mission();let calls=0;
 const {c}=pollingFixture(async path=>{if(path==='/api/missions')return {missions:[m]};offsets.push(path);if(calls++===0)return {offset:600,approvals:[],events:Array.from({length:600},(_,i)=>({type:'content',time:i,text:'x'}))};return {offset:601,approvals:[{name:'bash'}],events:[{type:'note',time:999,text:'blocked: ssh'}]};});
 await c.loadFlow();assert.equal(offsets.length,2);assert.match(offsets[1],/offset=600/);assert.equal(vm.runInContext('flowState.data.log.approvals.length',c),1);assert.equal(vm.runInContext('flowState.data.log.events.at(-1).text',c),'blocked: ssh');
});
test('late response after changing selection cannot draw the old mission',async()=>{
 let finish;const {c}=pollingFixture(()=>new Promise(resolve=>finish=resolve));const pending=c.loadFlow();vm.runInContext('flowState.epoch++',c);finish({missions:[mission()]});await pending;assert.equal(c.rendered,0);
});
test('connection loss is visible and never replaces evidence with a fake healthy state',async()=>{
 const {c,nodes}=pollingFixture(async()=>{throw Error('offline');});await c.loadFlow();assert.match(nodes['#flow-sync'].textContent,/zastaralý/);assert.equal(c.rendered,0);
});
test('verification phase can be active without a model worker',()=>{
 const r=model(mission({status:'verifying',phase:'final',active_attempt:null}));assert.equal(r.current.id,'@checks');assert.equal(r.current.status,'active');assert.equal(r.moving,true);
});
