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
