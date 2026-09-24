const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ctx={};vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/browser-pilot.js'),'utf8'),ctx);
const frame=()=>({query:'',filtered:false,visibleItems:['Blue desk','Green chair','White lamp']});
test('observed action is single use, current and deadline bounded',()=>{
  const state=frame();const lease=ctx.pilotLease(state,'set_query_blue',100);
  assert.equal(ctx.consumePilotLease(lease,state,101),'set_query_blue');
  assert.throws(()=>ctx.consumePilotLease(lease,state,102),/already-used/);
  assert.throws(()=>ctx.consumePilotLease(ctx.pilotLease(state,'set_query_blue',100),state,15100),/expired/);
  assert.throws(()=>ctx.consumePilotLease(ctx.pilotLease(state,'set_query_blue',100),{...state,query:'red'},101),/Stale/);
});
test('unknown operation and unobserved target cannot be executed',()=>{
  for(const action of ['click:#delete','goto:https://example.com','finish','apply_filter','eval','upload']) assert.throws(()=>ctx.pilotLease(frame(),action),/not eligible/);
});
test('DONE alone never establishes success',()=>{
  assert.equal(ctx.pilotVerified({query:'blue',filtered:true,visibleItems:['Green chair']}),false);
  assert.equal(ctx.pilotVerified({query:'blue',filtered:true,visibleItems:['Blue desk','Green chair']}),false);
  assert.equal(ctx.pilotVerified({query:'blue',filtered:false,visibleItems:['Blue desk']}),false);
  assert.equal(ctx.pilotVerified({query:'blue',filtered:true,visibleItems:['Blue desk']}),true);
});
