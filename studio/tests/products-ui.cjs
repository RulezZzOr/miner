const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/products.js'), 'utf8');
function fixture() {
  const pending = [];
  const nodes = {'#products-dialog':{open:true}, '#products-health':{textContent:''}, '#product-list':{replaceChildren(){},append(){}}};
  const ctx = {
    productLoading:false, productMutating:false, productEpoch:0, productDirty:false,
    productSnapshot:'', productSelection:null, state:{project:'one'}, $:id=>nodes[id],
    api:()=>new Promise(resolve=>pending.push(resolve)),
    renderProduct:()=>{ctx.renders++}, renders:0,
  };
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('async function loadProducts('), source.indexOf('async function showProducts(')),ctx);
  return {ctx,pending,nodes};
}
const data = () => ({products:[],controller_error:''});
test('product polling preserves an unsaved requirement or settings', async()=>{
  const {ctx,pending}=fixture();ctx.productDirty=true;
  const request=ctx.loadProducts();pending[0](data());await request;
  assert.equal(ctx.renders,0);
});
test('a late product response cannot override a mutation', async()=>{
  const {ctx,pending}=fixture();const request=ctx.loadProducts();ctx.productEpoch++;
  pending[0](data());await request;assert.equal(ctx.renders,0);
});
test('a late response cannot render a different workspace or closed dialog', async()=>{
  for(const change of ['project','close']) {
    const {ctx,pending,nodes}=fixture();const request=ctx.loadProducts();
    if(change==='project') ctx.state.project='two'; else nodes['#products-dialog'].open=false;
    pending[0](data());await request;assert.equal(ctx.renders,0);
  }
});
test('product requests never overlap and unchanged data leaves forms intact', async()=>{
  const {ctx,pending}=fixture();const request=ctx.loadProducts();await ctx.loadProducts();
  assert.equal(pending.length,1);pending[0](data());await request;assert.equal(ctx.renders,1);
  const next=ctx.loadProducts();pending[1](data());await next;assert.equal(ctx.renders,1);
});
