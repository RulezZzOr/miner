const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('function defaultModelForRole(');
const end = source.indexOf('\nconst state = {', start);
const context = {state:{data:{default_profile:'amd-coder',profiles:[
  {id:'amd-coder'}, {id:'radeon-r9700',reviewer_default:true},
]}}};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

test('new work defaults to the configured worker and reviews to the marked reviewer', () => {
  assert.equal(context.defaultModelForRole('work'), 'amd-coder');
  assert.equal(context.defaultModelForRole('review'), 'radeon-r9700');
});

test('review default falls back to the app default when no reviewer is marked', () => {
  const profiles = [{id:'amd-coder'}];
  assert.equal(context.defaultModelForRole('review', profiles), 'amd-coder');
});
