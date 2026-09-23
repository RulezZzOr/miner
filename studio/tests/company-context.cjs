const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const ctx = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function companyTaskContext('), source.indexOf('async function addCompanyContext(')), ctx);
test('company instructions preserve draft and reference the project file', () => {
  const draft = "  Vytvoř API.\nZachovej tyto podmínky.  ";
  const result = ctx.companyTaskContext(draft, 'company/ai-build-company.json');
  assert.ok(result.startsWith(draft + '\n\n'));
  assert.ok(result.includes("Přečti soubor company/ai-build-company.json"));
  assert.equal(ctx.companyTaskContext(result, 'company/ai-build-company.json'), result);
});
test('empty task requires a concrete goal instead of launching an entire company', () => {
  const result = ctx.companyTaskContext('', 'company/ai-build-company.json');
  assert.ok(result.startsWith('[AI Build Company:'));
  assert.ok(result.includes("Pokud chybí konkrétní cíl, nejdřív si ho vyžádej"));
});
