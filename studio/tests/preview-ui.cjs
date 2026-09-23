const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/preview.js'), 'utf8');
function fixture() {
  const pending = [];
  const nodes = {'#preview-dialog':{open:true}, '#preview-auto':{checked:true}, '#preview-status':{textContent:''}};
  const ctx = {
    state:{project:'one'}, previewCurrent:{id:'first'}, previewRevision:1,
    previewBusy:false, previewPolling:false, previewEpoch:1,
    $:id=>nodes[id], api:()=>new Promise(resolve=>pending.push(resolve)),
    renderPreview:s=>{ctx.renders++;ctx.previewCurrent=s}, renders:0,
    refreshPreviewFrame:()=>ctx.reloads++, reloads:0,
  };
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('async function pollPreview('), source.indexOf('function initPreview(')),ctx);
  return {ctx,pending,nodes};
}
const session = revision => ({id:'first',project:'one',running:true,revision});
test('unchanged polling preserves the interactive page; a changed asset reloads once',async()=>{
  const {ctx,pending}=fixture();
  let request=ctx.pollPreview();pending.shift()(session(1));await request;
  assert.equal(ctx.reloads,0);
  request=ctx.pollPreview();pending.shift()(session(2));await request;
  assert.equal(ctx.reloads,1);
  request=ctx.pollPreview();pending.shift()(session(2));await request;
  assert.equal(ctx.reloads,1);
});
test('turning automatic refresh off preserves unsaved input inside the app',async()=>{
  const {ctx,pending,nodes}=fixture();nodes['#preview-auto'].checked=false;
  const request=ctx.pollPreview();pending.shift()(session(2));await request;
  assert.equal(ctx.reloads,0);assert.equal(ctx.previewRevision,1);
});
test('late responses cannot resurrect a stopped preview or another project',async()=>{
  for (const mutate of [ctx=>ctx.previewEpoch++, ctx=>ctx.state.project='two']) {
    const {ctx,pending}=fixture();const request=ctx.pollPreview();mutate(ctx);
    pending.shift()(session(2));await request;
    assert.equal(ctx.reloads,0);assert.equal(ctx.renders,0);
  }
});
test('a closed dialog and an in-flight mutation cannot be overwritten by polling',async()=>{
  const {ctx,pending,nodes}=fixture();const request=ctx.pollPreview();
  await ctx.pollPreview();assert.equal(pending.length,1);
  nodes['#preview-dialog'].open=false;pending.shift()(session(2));await request;
  assert.equal(ctx.renders,0);
  nodes['#preview-dialog'].open=true;ctx.previewBusy=true;
  await ctx.pollPreview();assert.equal(pending.length,0);
});
