const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const ctx = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function companyTaskContext('), source.indexOf('async function addCompanyContext(')), ctx);
test('company instructions preserve draft and reference the project file', () => {
  const draft = "  Create an API.\nKeep these conditions.  ";
  const result = ctx.companyTaskContext(draft, 'company/ai-build-company.json');
  assert.ok(result.startsWith(draft + '\n\n'));
  assert.ok(result.includes("Read the file company/ai-build-company.json"));
  assert.equal(ctx.companyTaskContext(result, 'company/ai-build-company.json'), result);
});
test('empty task requires a concrete goal instead of launching an entire company', () => {
  const result = ctx.companyTaskContext('', 'company/ai-build-company.json');
  assert.ok(result.startsWith('[AI Build Company:'));
  assert.ok(result.includes("If a specific goal is missing, request it first"));
});
test('authored prompts that ask for reports require English output', () => {
  const directive = 'Write all reports, questions, summaries, notes and generated documentation in English, regardless of the language of the input.';
  assert.ok(ctx.companyTaskContext('', 'company/ai-build-company.json').includes(directive));
  const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  for (const [, prompt] of html.matchAll(/data-prompt="([^"]+)"/g)) assert.ok(prompt.includes(directive), prompt);
  const companies = fs.readFileSync(path.join(__dirname, '../static/companies.js'), 'utf8');
  assert.ok(companies.includes(directive), 'company preset goals');
});
