const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/help.js'), 'utf8'), context);
function help(label, id='') {
  return context.studioHelpFor({id, textContent:label, getAttribute:()=>null, matches:()=>false, closest:()=>null});
}
test('tool approval and result acceptance have distinct instructions', () => {
  const tools=help('Auto approve tools'), accept=help('Auto accept');
  assert.match(tools.purpose, /tools/);
  assert.match(accept.purpose, /checks pass/);
  assert.notEqual(tools.example, accept.example);
});
test('approval choices explain their actual action despite prefixed IDs', () => {
  assert.match(help('Yes','approval-yes').purpose, /specific pending tool action/);
  assert.match(help('No','approval-no').purpose, /Declines/);
  assert.match(help('Custom reply','approval-reply').how, /declines the current action/);
});
test('close-model help explains closing rather than configuring a model', () => {
  assert.match(help('Close','model-close').purpose, /Closes this panel/);
});
test('help distinguishes attempt time from the whole project deadline', () => {
  assert.match(help('Minutes per attempt').purpose, /each worker attempt/);
  assert.match(help('Minutes per attempt').how, /separate from.*deadline/);
});
test('budgets do not claim to cap provider billing', () => {
  assert.match(help('Run budget').how, /not a currency or provider-billing cap/);
});
test('organizational roles are not represented as security boundaries', () => {
  assert.match(help('Department').how, /not a separate security account/);
  assert.match(help('Constraints').how, /do not replace OS-level isolation/);
});
test('template help does not claim to launch every role', () => {
  assert.match(help('Templates').how, /does not start all/);
});
test('preview help explains that files must be saved and is not public hosting', () => {
  assert.match(help('Start preview').how, /Save the files/);
  assert.match(help('Start preview').how, /not public hosting/);
});
test('profile credentials help asks for the variable name, not the secret', () => {
  assert.match(help('Credential variable').how, /variable name, not the secret/);
  assert.equal(help('Credential variable').example, 'OPENAI_API_KEY');
});
test('all catalog entries have a purpose, usage and example', () => {
  const entries=vm.runInContext('studioHelpRules', context);
  for(const [pattern,purpose,how,example] of entries) {
    assert.ok(Object.prototype.toString.call(pattern)==='[object RegExp]');
    for(const text of [purpose,how,example]) assert.ok(typeof text==='string' && text.trim().length>0);
  }
});
test('tool approval actions explain whether the run continues', () => {
  assert.match(help('Allow').purpose, /specific pending tool action/);
  assert.match(help('Skip and redirect').purpose, /run continues/);
  assert.match(help('Reject and stop run').purpose, /stops the current run/);
  assert.match(help('Reject and stop run').how, /may retry/);
});
test('recovery actions and session sign-in have their own help', () => {
  assert.match(help('Retry now').purpose, /Resumes blocked work/);
  assert.match(help('Answer and retry').purpose, /Resumes blocked work/);
  assert.match(help('Requeue').purpose, /cancelled or expired company task/);
  assert.match(help('Answer and retry').how, /recovery question .* resumes the work/);
  assert.match(help('Save answer only').purpose, /agent's question without resuming/);
  assert.match(help('Owner access key').purpose, /session expired/);
  assert.doesNotMatch(help('Sign in and resume').purpose, /provider account/);
});
test('keyboard help uses F1 anywhere and ? only outside text entry', () => {
  const control=(tag,type='')=>({isContentEditable:false,matches:s=>tag==='input'?!/input:not\(\[type=checkbox\]\)/.test(s)||['checkbox','radio','button','submit'].includes(type)?false:true:s.split(',').includes(tag)});
  const key=(k,extra={})=>({key:k,ctrlKey:false,metaKey:false,altKey:false,...extra});
  assert.equal(context.studioHelpKey(key('F1'),control('textarea')),true);
  assert.equal(context.studioHelpKey(key('?'),control('button')),true);
  assert.equal(context.studioHelpKey(key('?'),control('textarea')),false);
  assert.equal(context.studioHelpKey(key('?'),control('input','text')),false);
  assert.equal(context.studioHelpKey(key('?',{ctrlKey:true}),control('button')),false);
  assert.equal(context.studioHelpKey(key('a'),control('button')),false);
});
test('the Run button keeps its help when its title explains why it is disabled', () => {
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const label=(html.match(/<button id="run-task"[^>]*aria-label="([^"]+)"/)||[])[1];
  assert.equal(label,'Run task','the Run button needs a stable accessible name');
  for (const title of [null,'Workers need Linux with bubblewrap; this host can edit and review but not run agents.','A worker is busy. Wait for the current run or stop it.']) {
    const attrs={'aria-label':label,title};
    const found=context.studioHelpFor({id:'run-task',textContent:'Run ↑',getAttribute:k=>attrs[k]??null,matches:()=>false,closest:()=>null});
    assert.equal(found.title,'Run task');assert.match(found.purpose,/^Starts the selected task/);
  }
});
