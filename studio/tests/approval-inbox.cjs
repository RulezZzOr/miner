const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/companies.js'),'utf8').split('// Global inbox:')[1];
class Node {
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.value='';this.classList={toggle(){}};}
  append(...nodes){for(const n of nodes){this.children.push(n);n.parent=this;}}
  setAttribute(){} focus(){this.focused=true;} remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
}
function fixture(){
 const calls=[],nodes=Object.fromEntries(['approvals','approval-jump','approval-count','approval-total','approval-inbox','approval-heading','approval-status'].map(id=>['#'+id,new Node('div')]));
 const ctx={Map,Set,document:{title:''},state:{data:{runs:[],projects:[]}},el:(tag,cls,text)=>new Node(tag,text),$:id=>nodes[id],$$:()=>[],
 api:async(url,body)=>{calls.push({url,body});return {missions:[]};},setInterval(){}};
 vm.createContext(ctx);vm.runInContext("//"+source,ctx);return {ctx,nodes,calls};
}
const tool=(run='run-a',id='a')=>({key:`tool/${run}/${id}`,kind:'tool',run,context:'Project A',request:{id,name:'bash',reason:'Needs approval',target:'ssh target hostname',dangerous:''}});
const find=(card,tag,label)=>card.children.flatMap(n=>[n,...n.children]).find(n=>n.tag===tag&&(label===undefined||n.textContent===label));
test('global inbox includes another run and project, questions and plans but no terminal questions',()=>{
 const {ctx}=fixture();const items=ctx.approvalInboxItems([{id:'r2',title:'Other project',approvals:[{id:'a'}]}],[
 {id:'m1',title:'Question',status:'waiting',questions:[{id:'q',question:'Which URL?',answer:null}]},
 {id:'m2',title:'Plan',status:'awaiting_plan',questions:[],tasks:[]},
 {id:'m3',status:'cancelled',questions:[{id:'old',answer:null}]}]);
 assert.deepEqual(Array.from(items,x=>x.kind),['tool','question','plan']);assert.equal(items[0].run,'r2');
});
test('conflicting readiness brief routes to revision instead of an ineffective yes/no answer',()=>{
 const {ctx}=fixture();
 const items=ctx.approvalInboxItems([],[{id:'m',project:'p',title:'Audit',status:'blocked',questions:[{id:'q',kind:'readiness',question:'Audit or repair?',answer:null}]}]);
 assert.equal(items[0].kind,'readiness');
 let opened;
 ctx.dashboardOpenMission=m=>{opened=m;};
 ctx.missionButton=(label,fn)=>{const button=new Node('button',label);button.onclick=fn;return button;};
 const card=ctx.approvalInboxCard(items[0]);
 assert.equal(find(card,'button','Yes'),undefined);assert.equal(find(card,'textarea'),undefined);
 find(card,'button','Revise brief').onclick();assert.equal(opened.id,'m');assert.equal(opened.project,'p');
});
test('polling and adding another approval preserve typed feedback and card identity',()=>{
 const {ctx,nodes}=fixture();ctx.renderApprovalInbox([tool()]);const card=nodes['#approvals'].children[0];find(card,'textarea').value='Use staging';
 ctx.renderApprovalInbox([tool(),tool('run-b','b')]);assert.equal(nodes['#approvals'].children[0],card);assert.equal(find(card,'textarea').value,'Use staging');
 ctx.renderApprovalInbox([tool()]);assert.equal(nodes['#approvals'].children.length,1);
});
test('yes is bound to the original run and preserves risk confirmation',async()=>{
 const {ctx,calls}=fixture();const item=tool();item.request.dangerous='Sensitive command';const card=ctx.approvalInboxCard(item);
 find(card,'input').value='yes';await find(card,'button',"Yes").onclick();
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{url:'/api/decision',body:{run:'run-a',approval:'a',allow:true,feedback:'',confirmation:'yes'}});
});
test('custom reply declines the pending action and sends feedback, empty reply sends nothing',async()=>{
 const {ctx,calls}=fixture();const card=ctx.approvalInboxCard(tool());const button=find(card,'button',"Send response");
 await button.onclick();assert.equal(calls.length,0);find(card,'textarea').value='Only inspect the service';await button.onclick();
 assert.equal(calls[0].body.allow,false);assert.equal(calls[0].body.feedback,'Only inspect the service');
});
test('no denies and mission custom replies use answer API',async()=>{
 const {ctx,calls}=fixture();await find(ctx.approvalInboxCard(tool()),'button',"No").onclick();assert.equal(calls[0].body.allow,false);
 const card=ctx.approvalInboxCard({kind:'question',key:'q',mission:'m',question:'q1',context:'Cloud',title:'URL?'});
 find(card,'textarea').value='https://example.test';await find(card,'button',"Send response").onclick();
 const call=calls.find(x=>x.body?.action==='answer');assert.equal(call.body.id,'m');assert.equal(call.body.question,'q1');assert.equal(call.body.answer,'https://example.test');
});
test('server rejection keeps input and exposes error without silently approving',async()=>{
 const {ctx}=fixture();ctx.api=async()=>{throw new Error('Type yes first');};const item=tool();item.request.dangerous='Risk';const card=ctx.approvalInboxCard(item);
 find(card,'textarea').value='Keep this draft';await find(card,'button',"Yes").onclick();
 assert.equal(find(card,'textarea').value,'Keep this draft');assert.ok(card.children.some(n=>n.textContent==='Type yes first'&&!n.hidden));assert.equal(find(card,'button',"Yes").disabled,false);
});
test('double click cannot send a second decision',async()=>{
 const {ctx}=fixture();let count=0,finish;ctx.api=()=>{count++;return new Promise(r=>finish=r);};ctx.pollApprovalInbox=async()=>{};
 const button=find(ctx.approvalInboxCard(tool()),'button',"Yes");const pending=button.onclick();await button.onclick();assert.equal(count,1);finish({});await pending;
});
