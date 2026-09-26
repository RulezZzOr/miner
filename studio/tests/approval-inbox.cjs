const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/companies.js'),'utf8').split('// Global inbox:')[1];
class Node {
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.value='';this.classList={toggle(){}};this.hidden=false;}
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
test('allow is bound to the original run and preserves risk confirmation',async()=>{
 const {ctx,calls}=fixture();const item=tool();item.request.dangerous='Sensitive command';const card=ctx.approvalInboxCard(item);
 find(card,'input').value='yes';await find(card,'button',"Allow").onclick();
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{url:'/api/decision',body:{run:'run-a',approval:'a',allow:true,feedback:'',confirmation:'yes'}});
});
test('risky confirmation tolerates mobile capitalisation and spaces but nothing else',async()=>{
 for(const [typed,sent] of [['Yes ','yes'],['YES','yes'],[' yes','yes'],['no','no']]){
  const {ctx,calls}=fixture();const item=tool();item.request.dangerous='Sensitive command';const card=ctx.approvalInboxCard(item);
  const input=find(card,'input');assert.equal(input.autocapitalize,'off');assert.equal(input.spellcheck,false);
  input.value=typed;await find(card,'button',"Allow").onclick();assert.equal(calls[0].body.confirmation,sent);
 }
});
test('skip and redirect declines only the action and sends the instruction; empty instruction sends nothing',async()=>{
 const {ctx,calls}=fixture();const card=ctx.approvalInboxCard(tool());const button=find(card,'button',"Skip and redirect");
 await button.onclick();assert.equal(calls.length,0);assert.ok(card.children.some(n=>/instruction/.test(n.textContent)&&!n.hidden));
 find(card,'textarea').value='Only inspect the service';await button.onclick();
 assert.equal(calls[0].body.allow,false);assert.equal(calls[0].body.feedback,'Only inspect the service');
});
test('allow and reject never silently drop a typed note',async()=>{
 for(const label of ['Allow','Reject and stop run']){
  const {ctx,calls}=fixture();const card=ctx.approvalInboxCard(tool());find(card,'textarea').value='Use staging';
  await find(card,'button',label).onclick();assert.equal(calls.length,0,label);
  assert.equal(find(card,'textarea').value,'Use staging');assert.ok(card.children.some(n=>/does not deliver your note/.test(n.textContent)&&!n.hidden));
 }
});
test('reject stops the run explicitly and the card explains the consequence',async()=>{
 const {ctx,calls}=fixture();const card=ctx.approvalInboxCard(tool());
 assert.ok(card.children.some(n=>/ends the current run/.test(n.textContent)));
 await find(card,'button',"Reject and stop run").onclick();assert.equal(calls[0].body.allow,false);assert.equal(calls[0].body.feedback,'');
});
test('mission custom replies use the answer API',async()=>{
 const {ctx,calls}=fixture();
 const card=ctx.approvalInboxCard({kind:'question',key:'q',mission:'m',question:'q1',context:'Cloud',title:'URL?'});
 find(card,'textarea').value='https://example.test';await find(card,'button',"Send response").onclick();
 const call=calls.find(x=>x.body?.action==='answer');assert.equal(call.body.id,'m');assert.equal(call.body.question,'q1');assert.equal(call.body.answer,'https://example.test');
});
test('server rejection keeps input and exposes error without silently approving',async()=>{
 const {ctx}=fixture();ctx.api=async()=>{throw new Error('Type yes first');};const item=tool();item.request.dangerous='Risk';const card=ctx.approvalInboxCard(item);
 find(card,'textarea').value='Keep this draft';await find(card,'button',"Skip and redirect").onclick();
 assert.equal(find(card,'textarea').value,'Keep this draft');assert.ok(card.children.some(n=>n.textContent==='Type yes first'&&!n.hidden));assert.equal(find(card,'button',"Allow").disabled,false);
});
test('double click cannot send a second decision',async()=>{
 const {ctx}=fixture();let count=0,finish;ctx.api=()=>{count++;return new Promise(r=>finish=r);};ctx.pollApprovalInbox=async()=>{};
 const button=find(ctx.approvalInboxCard(tool()),'button',"Allow");const pending=button.onclick();await button.onclick();assert.equal(count,1);finish({});await pending;
});
test('stalls without a question reach the inbox, terminal and answered work does not',()=>{
 const {ctx}=fixture();
 const items=ctx.approvalInboxItems([],[
  {id:'b',title:'Blocked',project:'p',status:'blocked',message:'Three unsuccessful attempts.',questions:[],company_context:{id:'c'}},
  {id:'r',title:'Ready',project:'p',status:'ready',message:'Checks passed',questions:[]},
  {id:'k',title:'Checks',project:'p',status:'awaiting_checks',questions:[]},
  {id:'q',title:'Asked',project:'p',status:'blocked',questions:[{id:'x',question:'How should I adjust?',answer:null}]},
  {id:'d',title:'Done',status:'accepted',questions:[]},{id:'w',title:'Working',status:'running',questions:[]}],
  [{id:'c',name:'Company',tasks:[{id:'t',title:'Audit',status:'expired',enabled:true,last_mission:'old'},{id:'off',status:'expired',enabled:false},{id:'x',status:'cancelled',enabled:true}]}]);
 assert.deepEqual(Array.from(items,x=>x.kind),['blocked','ready','awaiting_checks','question','expired']);
 assert.equal(items[0].reason,'Three unsuccessful attempts.');assert.equal(items[0].driver,true);assert.equal(items[3].blocked,true);
 assert.equal(items[4].company,'c');assert.equal(items[4].task,'t');
});
test('blocked stall offers an explicit retry and opens details without a mutation',async()=>{
 const {ctx,calls}=fixture();let opened;ctx.dashboardOpenMission=m=>{opened=m;};ctx.pollApprovalInbox=async()=>{};
 const card=ctx.approvalInboxCard({kind:'blocked',key:'blocked/m',mission:'m',project:'p',context:'Audit',title:'Work is blocked',reason:'Run limit exhausted',driver:true});
 assert.equal(find(card,'textarea'),undefined);
 await find(card,'button','Open details').onclick();assert.equal(opened.id,'m');assert.equal(calls.length,0);
 await find(card,'button','Retry now').onclick();assert.deepEqual(JSON.parse(JSON.stringify(calls[0].body)),{id:'m',action:'resume'});
});
// The answer action returns the mission; the server resumes it itself for recovery questions.
function answering(ctx,calls,answered,resume=async()=>({status:'running'})){
 ctx.toasts=[];ctx.toast=(message,error=false)=>ctx.toasts.push([message,error]);ctx.pollApprovalInbox=async()=>{};
 ctx.api=async(url,body)=>{calls.push({url,body});return body.action==='answer'?answered:resume();};
}
const texts=card=>card.children.flatMap(n=>[n,...n.children]).filter(n=>!n.hidden).map(n=>n.textContent);
test('inbox marks controller and Driver block questions as recovery questions',()=>{
 const {ctx}=fixture();const q=(id,extra)=>({id,question:'Q '+id,answer:null,task:null,...extra});
 const items=ctx.approvalInboxItems([],[{id:'m',project:'p',title:'Audit',status:'blocked',questions:[
  q('r',{kind:'recovery'}),q('f',{kind:'failure'}),q('l',{question:'How should I adjust the approach before restarting the task?'}),q('a',{task:'t1'})]}]);
 assert.deepEqual(Array.from(items,i=>[i.question,i.recovery,i.blocked]),[['r',true,true],['f',true,true],['l',true,true],['a',false,true]]);
});
test('an agent question on blocked work: answer and retry resumes it, save answer only does not',async()=>{
 const {ctx,calls}=fixture();answering(ctx,calls,{id:'m',status:'blocked',message:'Answer saved.'});
 const item={kind:'question',key:'q',mission:'m',question:'q1',context:'Audit',title:'Which host?',blocked:true,recovery:false};
 let card=ctx.approvalInboxCard(item);assert.equal(find(card,'button','Yes'),undefined);assert.equal(find(card,'button','Open details'),undefined);
 await find(card,'button','Answer and retry').onclick();assert.equal(calls.length,0,'an agent question needs a written answer');
 find(card,'textarea').value='Use the staging host';await find(card,'button','Answer and retry').onclick();
 assert.deepEqual(Array.from(calls,c=>c.body.action),['answer','resume']);assert.equal(calls[0].body.answer,'Use the staging host');
 card=ctx.approvalInboxCard(item);find(card,'textarea').value='Later';await find(card,'button','Save answer only').onclick();
 assert.deepEqual(Array.from(calls,c=>c.body.action),['answer','resume','answer']);
 assert.ok(texts(card).some(t=>/Save answer only records it; the work stays blocked/.test(t)));
});
test('a recovery answer the server already resumed is a success without a second resume',async()=>{
 const {ctx,calls}=fixture();answering(ctx,calls,{id:'m',status:'running',message:'Answer saved; continuing from saved state.'});
 let opened;ctx.dashboardOpenMission=m=>{opened=m;};
 const item={kind:'question',key:'q',mission:'m',project:'p',question:'q1',context:'Audit',title:'Execution is blocked. How should the company continue?',blocked:true,recovery:true};
 const card=ctx.approvalInboxCard(item);
 assert.equal(find(card,'button','Save answer only'),undefined,'answering a recovery question always resumes on the server');
 await find(card,'button','Open details').onclick();assert.equal(opened.id,'m');assert.equal(opened.project,'p');assert.equal(calls.length,0);
 await find(card,'button','Answer and retry').onclick();
 assert.deepEqual(Array.from(calls,c=>c.body.action),['answer']);assert.equal(calls[0].body.answer,'Continue with the current limits.');
 assert.deepEqual(ctx.toasts,[]);assert.equal(card.dataset.busy,'false');
 assert.ok(!texts(card).some(t=>/not available|no longer open/.test(t)),'no false error after a successful answer');
});
test('a recovery answer left for a paused Driver is reported instead of failing a resume',async()=>{
 const {ctx,calls}=fixture();answering(ctx,calls,{id:'m',status:'blocked',driver_resume:true,message:'Answer saved. The company is paused. The Driver continues this execution when it runs again.'});
 const card=ctx.approvalInboxCard({kind:'question',key:'q',mission:'m',question:'q1',context:'Audit',title:'Blocked',blocked:true,recovery:true});
 find(card,'textarea').value='continue';await find(card,'button','Answer and retry').onclick();
 assert.deepEqual(Array.from(calls,c=>c.body.action),['answer']);
 assert.deepEqual(ctx.toasts,[['Answer saved. The company is paused. The Driver continues this execution when it runs again.',false]]);
});
test('a failed retry after a saved answer is reported and does not leave a stale answer card',async()=>{
 const {ctx,calls}=fixture();answering(ctx,calls,{id:'m',status:'blocked',message:'Answer saved; the execution stays blocked.'},async()=>{throw new Error('The run budget is exhausted.');});
 const card=ctx.approvalInboxCard({kind:'question',key:'q',mission:'m',question:'q1',context:'Audit',title:'Blocked',blocked:true,recovery:true});
 find(card,'textarea').value='wait for the new key, then retry';await find(card,'button','Answer and retry').onclick();
 assert.deepEqual(Array.from(calls,c=>c.body.action),['answer','resume']);
 assert.deepEqual(ctx.toasts,[['Answer saved, but the work is still blocked: The run budget is exhausted.',true]]);
 assert.ok(!texts(card).some(t=>/run budget/.test(t)),'the answered question is closed; the blocked card that follows offers Retry now');
});
test('expired company task is requeued with the latest company revision',async()=>{
 const {ctx,calls}=fixture();ctx.pollApprovalInbox=async()=>{};
 ctx.api=async(url,body)=>{calls.push({url,body});return body?{}:{companies:[{id:'c',revision:9}]};};
 const card=ctx.approvalInboxCard({kind:'expired',key:'expired/c/t/m',company:'c',task:'t',context:'Company · Audit',title:'Time limit expired'});
 await find(card,'button','Requeue task').onclick();
 assert.deepEqual(JSON.parse(JSON.stringify(calls[1])),{url:'/api/companies/action',body:{id:'c',revision:9,action:'requeue_task',task:'t'}});
});
test('the inbox never offers a requeue for an external owner step',()=>{
 const {ctx}=fixture();
 const items=ctx.approvalInboxItems([],[],[{id:'c',name:'Co',tasks:[{id:'w',title:'Audit',status:'expired',enabled:true,kind:'work'},{id:'x',title:'Sign',status:'expired',enabled:true,kind:'external'}]}]);
 assert.deepEqual(Array.from(items,i=>i.task),['w']);
});
test('inbox polling includes company stalls and survives a company request failure',async()=>{
 const {ctx,nodes}=fixture();
 ctx.api=async url=>url==='/api/companies'?{companies:[{id:'c',name:'Company',tasks:[{id:'t',title:'Audit',status:'expired',enabled:true}]}]}:{missions:[]};
 await ctx.pollApprovalInbox();assert.equal(nodes['#approvals'].children.length,1);assert.equal(nodes['#approval-count'].textContent,'1');
 const {ctx:other,nodes:otherNodes}=fixture();
 other.api=async url=>{if(url==='/api/companies')throw new Error('offline');return {missions:[{id:'b',title:'Blocked',status:'blocked',questions:[]}]};};
 await other.pollApprovalInbox();assert.equal(otherNodes['#approvals'].children.length,1);
});
