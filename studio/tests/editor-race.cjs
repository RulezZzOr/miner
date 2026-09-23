const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function functionSource(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end));
}
function fixture() {
  const nodes = new Map();
  const pending = [];
  const ctx = {
    state: {project:'fixture', file:{path:'a.txt', content:'A'}, dirty:false, fileEpoch:0},
    $: selector => {
      if (!nodes.has(selector)) nodes.set(selector, {
        value:'', textContent:'', classList:{add(){},remove(){}},
      });
      return nodes.get(selector);
    },
    $$: () => [],
    canLeave: () => true,
    api: () => new Promise(resolve => pending.push(resolve)),
    project: () => ({name:'fixture'}),
    updateLines(){}, updateCursor(){},
  };
  vm.createContext(ctx);
  vm.runInContext([
    functionSource('function markDirty()', 'function updateModelLabel()'),
    functionSource('async function openFile(', 'async function saveFile('),
    functionSource('function clearFile()', 'async function selectProject('),
  ].join('\n'), ctx);
  ctx.$('#editor').value = 'A';
  return {ctx, pending};
}
const file = (name) => ({path:name+'.txt',content:name.toUpperCase(),revision:name});
test('typing during a file request preserves the old file and unsaved edit', async () => {
  const {ctx, pending} = fixture();
  const request = ctx.openFile('b.txt');
  ctx.$('#editor').value = 'UNSAVED'; ctx.markDirty();
  pending[0](file('b')); await request;
  assert.equal(ctx.$('#editor').value, 'UNSAVED');
  assert.equal(ctx.state.file.path, 'a.txt');
  assert.equal(ctx.state.dirty, true);
});
test('an older response cannot replace the most recently selected file', async () => {
  const {ctx, pending} = fixture();
  const older = ctx.openFile('b.txt');
  const newer = ctx.openFile('c.txt');
  pending[1](file('c')); await newer;
  pending[0](file('b')); await older;
  assert.equal(ctx.state.file.path, 'c.txt');
  assert.equal(ctx.$('#editor').value, 'C');
});
test('editing and reverting still invalidates the in-flight request', async () => {
  const {ctx, pending} = fixture();
  const request = ctx.openFile('b.txt');
  ctx.$('#editor').value = 'EDIT'; ctx.markDirty();
  ctx.$('#editor').value = 'A'; ctx.markDirty();
  pending[0](file('b')); await request;
  assert.equal(ctx.state.file.path, 'a.txt');
});
test('clearing the editor invalidates a pending response', async () => {
  const {ctx, pending} = fixture();
  const request = ctx.openFile('b.txt');
  ctx.clearFile();
  pending[0](file('b')); await request;
  assert.equal(ctx.state.file, null);
});
test('project changes invalidate pending responses', async () => {
  const {ctx, pending} = fixture();
  const request = ctx.openFile('b.txt');
  ctx.state.project = 'other';
  pending[0](file('b')); await request;
  assert.equal(ctx.state.file.path, 'a.txt');
});
test('normal open and explicit discard still work', async () => {
  const {ctx, pending} = fixture();
  ctx.$('#editor').value = 'OLD EDIT'; ctx.markDirty();
  const request = ctx.openFile('b.txt');
  pending[0](file('b')); await request;
  assert.equal(ctx.state.file.path, 'b.txt');
  assert.equal(ctx.state.dirty, false);
});
