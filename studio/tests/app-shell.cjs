const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const slice=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));
const tick=()=>new Promise(resolve=>setImmediate(resolve));

// Minimal DOM double: one element per selector, recorded listeners and children.
function element(name){
  const classes=new Set();
  const node={name,value:'',textContent:'',hidden:false,disabled:false,open:false,dataset:{},style:{},children:[],listeners:{},attributes:{},
    classList:{add:(...c)=>c.forEach(x=>classes.add(x)),remove:(...c)=>c.forEach(x=>classes.delete(x)),contains:c=>classes.has(c),
      toggle:(c,force)=>((force??!classes.has(c))?classes.add(c):classes.delete(c))},
    addEventListener(type,fn){(this.listeners[type]||=[]).push(fn);},
    replaceChildren(...nodes){this.children=nodes;this.rebuilds=(this.rebuilds||0)+1;},append(...nodes){this.children.push(...nodes);},prepend(...nodes){this.children.unshift(...nodes);},
    cloneNode(){return element(name);},childNodes:[],focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;},
    setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},contains:()=>false,querySelector:()=>null,querySelectorAll:()=>[],
    scrollIntoView(){},requestSubmit(){},closest:()=>null,scrollHeight:0,scrollTop:0,clientHeight:0};
  node.parentElement={title:'',classList:node.classList};
  return node;
}
const response=(status,body)=>({status,ok:status<400,json:async()=>body});
function page({fetch,storage={}}){
  const nodes=new Map(),calls={toasts:[],intervals:[],inits:[],replaced:[]};
  const get=s=>{if(!nodes.has(s))nodes.set(s,element(s));return nodes.get(s);};
  const store=new Map(Object.entries(storage));
  const document={querySelector:get,querySelectorAll:()=>[],createElement:element,createTextNode:text=>({text}),body:element('body'),activeElement:null,title:'',addEventListener(){}};
  document.body.inert=true;document.body.classList.add('dashboard-home');
  const ctx={document,fetch,console,JSON,Promise,Date,Set,Map,Number,String,Boolean,Array,Object,Error,
    localStorage:{getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)},
    location:{replace:url=>calls.replaced.push(url)},window:{addEventListener(){}},confirm:()=>true,
    setTimeout:()=>0,clearTimeout(){},setInterval:fn=>{calls.intervals.push(fn);return calls.intervals.length;},
    showDashboard:visible=>{document.body.classList.toggle('dashboard-home',visible);},loadDashboard(){calls.inits.push('loadDashboard');},pollApprovalInbox(){calls.inits.push('pollApprovalInbox');}};
  for(const name of ['initMissions','initDecisionLab','initBrowserPilot','initProducts','initCompanies','initFlow','initPreview','initApprovalInbox','initDashboard'])ctx[name]=()=>calls.inits.push(name);
  vm.createContext(ctx);vm.runInContext(source,ctx);
  // Observe toasts through the real toast element.
  const toastNode=get('#toast');Object.defineProperty(toastNode,'textContent',{get(){return this._text||'';},set(v){this._text=v;calls.toasts.push(v);}});
  return {ctx,get,store,calls,run:code=>vm.runInContext(code,ctx)};
}
const stateData=(extra={})=>({token:'t',projects:[{id:'gone',name:'Gone',path:'/gone'},{id:'ok',name:'Works',path:'/ok'}],
  profiles:[{id:'local',model:'coder',protocol:'chat_completions'},{id:'gpt',model:'gpt',oauth_provider:'chatgpt',backend:'codex'},{id:'claude',model:'claude',oauth_provider:'claude_console'}],
  default_profile:'gpt',runs:[],...extra});

test('a failed initial load still binds every control and retries from polling',async()=>{
  let online=false;
  const p=page({fetch:async url=>{if(!online)throw new Error('Server restarting');return response(200,url.startsWith('/api/tree')?[]:stateData());}});
  await tick();await tick();
  assert.ok(p.get('#project-select').listeners.change,'project selector must be bound');
  assert.ok(p.get('#task-form').listeners.submit,'task form must be bound');
  assert.ok(p.calls.inits.includes('initDashboard')&&p.calls.inits.includes('initApprovalInbox'));
  assert.equal(p.ctx.document.body.inert,false);assert.equal(p.calls.intervals.length,1);
  assert.ok(p.calls.toasts.some(t=>/Failed to load Studio.*Retrying/.test(t)));
  assert.equal(p.run('state.loaded'),undefined);
  online=true;await p.calls.intervals[0]();
  assert.equal(p.run('state.loaded'),true);assert.equal(p.run('state.project'),'gone');
});
test('a missing project folder falls back to the next project and forgets the broken choice',async()=>{
  const p=page({storage:{'switch.project':'gone'},fetch:async url=>url.startsWith('/api/tree?project=gone')?response(500,{error:'No such file or directory'}):response(200,url.startsWith('/api/tree')?[]:stateData())});
  for(let i=0;i<6;i++)await tick();
  assert.equal(p.run('state.project'),'ok');assert.equal(p.store.get('switch.project'),'ok');
  assert.ok(p.calls.toasts.some(t=>/Project folder is missing or unreadable \(Gone\)/.test(t)));
  assert.equal(p.run('state.loaded'),true);assert.ok(p.get('#project-select').listeners.change);
});
test('a transient tree failure keeps the owner project and retries from polling',async()=>{
  for(const failure of [()=>response(503,{error:'Studio storage is busy. Retry in a moment.'}),()=>{throw new TypeError('Failed to fetch');}]){
    let broken=true;
    const p=page({storage:{'switch.project':'gone'},fetch:async url=>url.startsWith('/api/tree')&&broken?failure():response(200,url.startsWith('/api/tree')?[]:stateData())});
    for(let i=0;i<6;i++)await tick();
    assert.equal(p.run('state.project'),'gone');assert.equal(p.store.get('switch.project'),'gone','the saved choice survives');
    assert.equal(p.run('state.loaded'),undefined,'polling retries the load');
    assert.ok(!p.calls.toasts.some(t=>/missing or unreadable/.test(t)));
    broken=false;await p.calls.intervals[0]();
    assert.equal(p.run('state.loaded'),true);assert.equal(p.run('state.project'),'gone');
  }
});
test('an expired session during the tree load does not walk through every project',async()=>{
  let signedIn=false;const trees=[];
  const p=page({storage:{'switch.project':'gone'},fetch:async url=>{if(url.startsWith('/api/tree')){trees.push(url);if(!signedIn)return response(401,{error:'Sign in'});}
    return response(200,url.startsWith('/api/tree')?[]:stateData());}});
  for(let i=0;i<6;i++)await tick();
  assert.deepEqual(trees,['/api/tree?project=gone']);assert.equal(p.run('state.project'),'gone');assert.equal(p.store.get('switch.project'),'gone');
  signedIn=true;await p.calls.intervals[0]();await p.calls.intervals[0]();
  assert.equal(p.run('sessionExpired'),false);assert.equal(p.run('state.loaded'),true);assert.equal(p.run('state.project'),'gone');
});
test('when no project folder opens the saved choice is kept for a remounted folder',async()=>{
  const p=page({storage:{'switch.project':'gone'},fetch:async url=>url.startsWith('/api/tree')?response(404,{error:'Project folder is missing.'}):response(200,stateData())});
  for(let i=0;i<8;i++)await tick();
  assert.equal(p.run('state.project'),'gone');assert.equal(p.store.get('switch.project'),'gone');
  assert.match(p.get('#sidebar-content').children[0].textContent,/No project folder could be opened/);
  assert.equal(p.calls.toasts.filter(t=>/missing or unreadable/.test(t)).length,2);
});
test('the dashboard stays the landing view when the last run is restored',async()=>{
  const data=stateData({runs:[{id:'r1',project:'gone',task:'Fix',status:'completed',created:1}]});
  const p=page({storage:{'switch.project':'gone','switch.run':'r1'},fetch:async url=>response(200,url.startsWith('/api/tree')?[]:url.startsWith('/api/events')?{offset:0,events:[],approvals:[],run:data.runs[0]}:url.startsWith('/api/artifacts')?[]:data)});
  for(let i=0;i<8;i++)await tick();
  assert.equal(p.run('state.run'),'r1');assert.equal(p.ctx.document.body.classList.contains('dashboard-home'),true);
});
test('OAuth and Codex profiles are never offered as a worker model',async()=>{
  const p=page({fetch:async url=>response(200,url.startsWith('/api/tree')?[]:stateData())});
  for(let i=0;i<4;i++)await tick();
  const options=p.get('#model-select').children.map(o=>o.value);
  assert.deepEqual(options,['local']);assert.equal(p.get('#model-select').value,'local');
  assert.equal(p.run('isRunnableProfile({id:"x"})'),true);assert.equal(p.run('isRunnableProfile({backend:"codex"})'),false);
});
test('an expired session keeps the page, pauses requests and resumes after sign-in',async()=>{
  let status=200,count=0;
  const p=page({fetch:async url=>{count++;return status===401?response(401,{error:'Sign in'}):response(200,url.startsWith('/api/tree')?[]:stateData({token:'new'}));}});
  for(let i=0;i<4;i++)await tick();
  status=401;
  await assert.rejects(p.run('api("/api/state")'),/session has expired/);
  assert.deepEqual(p.calls.replaced,[],'must not navigate away and lose drafts');
  assert.equal(p.run('sessionExpired'),true);assert.equal(p.get('#session-dialog').open,true);
  const before=count;await assert.rejects(p.run('api("/api/missions")'),/session has expired/);assert.equal(count,before,'paused requests are not sent');
  await p.calls.intervals[0]();assert.equal(p.run('sessionExpired'),true,'probe while signed out');
  status=200;await p.calls.intervals[0]();
  assert.equal(p.run('sessionExpired'),false);assert.equal(p.get('#session-dialog').open,false);assert.equal(p.run('state.data.token'),'new');
});
test('sign-in errors only blame the key for 401',()=>{
  const ctx=vm.createContext({});vm.runInContext(slice('function signInMessage(','function showSessionExpired('),ctx);
  const login=vm.createContext({});vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/login.js'),'utf8'),login);
  for(const f of [ctx.signInMessage,login.loginMessage]){
    assert.equal(f(401,'Authentication failed.'),'Access key was not accepted.');
    assert.match(f(403,'Unauthorized request origin.'),/Studio rejected the sign-in \(403\): Unauthorized request origin\./);
    assert.match(f(429,'Authentication failed.'),/Too many/);assert.equal(f(429,'Too many active sessions.'),'Too many active sessions.');
    assert.match(f(500,''),/\(500\)/);
  }
});
test('login page and app use one product name and no owner-specific placeholders',()=>{
  const read=name=>fs.readFileSync(path.join(__dirname,'../static',name),'utf8');
  assert.match(read('login.html'),/<title>Sign in · Switch Studio<\/title>/);
  for(const name of ['login.html','login.js','index.html','app.js'])assert.doesNotMatch(read(name),/\bMiner\b|192\.168\.|\/Users\/[a-z]/,name);
  assert.match(read('index.html'),/placeholder="\/path\/to\/project"/);assert.match(read('index.html'),/placeholder="http:\/\/127\.0\.0\.1:11434\/v1"/);
});

function saveFixture(){
  const pending=[],sent=[],editor={value:'A1'};
  const ctx={state:{project:'p',file:{path:'a.txt',content:'A',revision:'r1'},dirty:true,fileEpoch:0},
    $:s=>s==='#editor'?editor:{textContent:'',disabled:false},toast(){},
    api:(url,body)=>{sent.push(body);return new Promise(resolve=>pending.push(resolve));}};
  vm.createContext(ctx);vm.runInContext(slice('function markDirty()','function updateModelLabel()')+slice('async function saveFile(','function updateLines()'),ctx);
  return {ctx,pending,sent,editor};
}
test('a second save during an in-flight save waits and uses the new revision',async()=>{
  const {ctx,pending,sent,editor}=saveFixture();
  const first=ctx.saveFile();editor.value='A12';ctx.markDirty();const second=ctx.saveFile();
  assert.equal(sent.length,1,'no concurrent write with a stale revision');
  pending[0]({revision:'r2'});await tick();await tick();
  assert.equal(sent.length,2);assert.equal(sent[1].revision,'r2');assert.equal(sent[1].content,'A12');
  pending[1]({revision:'r3'});await first;await second;
  assert.equal(ctx.state.file.revision,'r3');assert.equal(ctx.state.dirty,false);assert.equal(ctx.state.saving,null);
});
test('a failed save releases the guard so the owner can retry',async()=>{
  const {ctx}=saveFixture();ctx.api=async()=>{throw new Error('File has changed meanwhile.');};
  await assert.rejects(ctx.saveFile(),/changed/);assert.equal(ctx.state.saving,null);
});

test('selecting the current project keeps the open run and file',async()=>{
  const calls=[];const ctx={state:{project:'p',run:'r',file:{path:'a'}},$:()=>({value:''}),canLeave:()=>{calls.push('canLeave');return true;},clearFile:()=>calls.push('clear')};
  vm.createContext(ctx);vm.runInContext(slice('async function selectProject(','async function renderSidebar('),ctx);
  await ctx.selectProject('p');assert.equal(ctx.state.run,'r');assert.deepEqual(calls,[]);
});
test('the task brief and open-file hint are separated',async()=>{
  const sent=[];const input={value:'Fix the failing test',focus(){}};
  const ctx={state:{dirty:false,file:{path:'src/a.py'},project:'p',mode:'react',data:{runs:[]}},executionUnavailable:()=>'',
    $:s=>s==='#task-input'?input:s==='#model-select'?{value:'local'}:s==='#turn-limit'?{value:'20'}:s==='#auto-approve'?{checked:false}:{},
    api:async(url,body)=>{sent.push(body);return {id:'r'};},selectRun:async()=>{},toast(){},updateRunControls(){}};
  vm.createContext(ctx);vm.runInContext(slice('async function launchTask(','function fillModel('),ctx);
  await ctx.launchTask({preventDefault(){}});assert.equal(sent[0].task,'Fix the failing test\n\nFile open in editor: src/a.py');
});
test('launching is refused with the host reason when execution is unavailable',async()=>{
  const ctx={state:{dirty:false,file:null},executionUnavailable:()=>'Workers need Linux with bubblewrap.',$:()=>({value:'Do it',focus(){}}),api:async()=>assert.fail('must not launch')};
  vm.createContext(ctx);vm.runInContext(slice('async function launchTask(','function fillModel('),ctx);
  await assert.rejects(ctx.launchTask({preventDefault(){}}),/bubblewrap/);
});
test('execution availability is read from state and tolerates its absence',()=>{
  const ctx={state:{data:null}};vm.createContext(ctx);vm.runInContext(slice('const executionUnavailableDefault','function renderExecution('),ctx);
  assert.equal(ctx.executionUnavailable({}),'');assert.equal(ctx.executionUnavailable({execution:{available:true}}),'');
  assert.match(ctx.executionUnavailable({execution:{available:false}}),/Linux with bubblewrap/);
  assert.equal(ctx.executionUnavailable({execution:{available:false,reason:'No bwrap.'}}),'No bwrap.');
});

test('polling the task history does not rebuild an unchanged list',async()=>{
  const list=element('#sidebar-content');
  const ctx={state:{view:'runs',project:'p',run:null,treeEpoch:0,data:{runs:[{id:'r',project:'p',task:'T',status:'running',created:1}]}},labels:{},
    $:s=>s==='#sidebar-content'?list:element(s),$$:()=>[],el:(tag,cls,text)=>Object.assign(element(tag),{textContent:text}),document:{activeElement:null},Date};
  vm.createContext(ctx);vm.runInContext(slice('async function renderSidebar(','function setMode('),ctx);
  await ctx.renderSidebar();await ctx.renderSidebar();assert.equal(list.rebuilds,1);
  ctx.state.data.runs[0].status='completed';await ctx.renderSidebar();assert.equal(list.rebuilds,2);
});
test('finished runs do not refetch outputs on every poll and content chunks do not rebuild activity',async()=>{
  const panel=element('#bottom-content'),paths=[];
  const run={id:'r',status:'completed',created:1};
  const ctx={state:{run:'r',offset:0,events:[],bottom:'outputs',data:{runs:[run]}},labels:{},
    $:s=>s==='#bottom-content'?panel:s==='#conversation'?element(s):element(s),el:(tag,cls,text)=>Object.assign(element(tag),{textContent:text}),
    api:async url=>{paths.push(url.split('?')[0]);return url.startsWith('/api/events')?{offset:0,events:[],approvals:[],run}:[];},
    run:()=>run,renderEvent(){},updateRunControls(){},renderSidebar:async()=>{},Date};
  vm.createContext(ctx);vm.runInContext(slice('async function pollRun(','async function launchTask('),ctx);
  await ctx.pollRun();await ctx.pollRun();await ctx.pollRun();
  assert.equal(paths.filter(p=>p==='/api/artifacts').length,1);
  ctx.state.bottom='activity';ctx.state.events=[{type:'tool_call',name:'bash',args:{},time:1}];await ctx.renderBottom();const rebuilt=panel.rebuilds;
  ctx.state.events.push({type:'content',text:'x',time:2});await ctx.renderBottom();assert.equal(panel.rebuilds,rebuilt);
  ctx.state.events.push({type:'error',text:'boom',time:3});await ctx.renderBottom();assert.equal(panel.rebuilds,rebuilt+1);
  assert.ok(panel.children.some(row=>row.children.some(n=>n.textContent==='error')),'error rows use the English label');
});

function tree(tag,props={},children=[]){
  const node=Object.assign(element(tag),props,{children});node.tag=tag;
  node.querySelectorAll=sel=>{const out=[];const walk=n=>{for(const c of n.children||[]){if(sel==='details[data-key]'&&c.tag==='details'&&c.dataset.key)out.push(c);walk(c);}};walk(node);return out;};
  node.append=(...n)=>node.children.push(...n);node.replaceChildren=(...n)=>{node.children=n;};
  return node;
}
test('panel rebuilds keep open sections and evidence the owner loaded',()=>{
  const ctx={};vm.createContext(ctx);vm.runInContext(slice('function keepPanelState(','async function loadWorkspace('),ctx);
  const panel=tree('section');const build=()=>{panel.replaceChildren(tree('details',{dataset:{key:'result'},open:false}),tree('details',{dataset:{key:'checks'},open:true}));};
  ctx.keepPanelState(panel,'mission:a',build);
  const [result,checks]=panel.children;result.open=true;checks.open=false;const loaded=tree('div');loaded.classList.add('kept-result');result.append(loaded);
  ctx.keepPanelState(panel,'mission:a',build);
  assert.equal(panel.children[0].open,true);assert.equal(panel.children[1].open,false);assert.ok(panel.children[0].children.includes(loaded));
  ctx.keepPanelState(panel,'mission:b',build);assert.equal(panel.children[0].open,false,'another mission starts from defaults');
});
test('sections the owner did not toggle follow status-dependent defaults on rebuild',()=>{
  const ctx={};vm.createContext(ctx);vm.runInContext(slice('function keepPanelState(','async function loadWorkspace('),ctx);
  const panel=tree('section');let status='paused';
  // Mirrors missions.js: checks opens in awaiting_checks, readiness closes once ready.
  const build=()=>panel.replaceChildren(tree('details',{dataset:{key:'checks'},open:status==='awaiting_checks'}),
    tree('details',{dataset:{key:'readiness'},open:status!=='ready'}),tree('details',{dataset:{key:'result'},open:false}));
  ctx.keepPanelState(panel,'mission:a',build);
  panel.children[2].open=true;
  status='awaiting_checks';ctx.keepPanelState(panel,'mission:a',build);
  assert.equal(panel.children[0].open,true,'checks opens when the mission starts waiting for checks');
  assert.equal(panel.children[2].open,true,'a section the owner opened stays open');
  panel.children[0].open=false;ctx.keepPanelState(panel,'mission:a',build);
  assert.equal(panel.children[0].open,false,'a section the owner closed stays closed');
  status='ready';ctx.keepPanelState(panel,'mission:a',build);
  assert.equal(panel.children[1].open,false,'readiness closes by default once the brief is ready');
  assert.equal(panel.children[2].open,true);
});
