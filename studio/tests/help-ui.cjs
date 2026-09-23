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
  const tools=help('Automaticky schvalovat nástroje'), accept=help('Automatické převzetí');
  assert.match(tools.purpose, /povolené nástroje/);
  assert.match(accept.purpose, /úspěšných nezávislých kontrolách/);
  assert.notEqual(tools.example, accept.example);
});
test('approval choices explain their actual action despite prefixed IDs', () => {
  assert.match(help('Ano','approval-yes').purpose, /konkrétní čekající akci nástroje/);
  assert.match(help('Ne','approval-no').purpose, /Odmítne/);
  assert.match(help('Vlastní odpověď','approval-reply').how, /odmítne aktuální akci/);
});
test('close-model help explains closing rather than configuring a model', () => {
  assert.match(help('Zavřít','model-close').purpose, /Zavře tento panel/);
});
test('help distinguishes attempt time from the whole project deadline', () => {
  assert.match(help('Minut na pokus').purpose, /každého pokusu realizátora/);
  assert.match(help('Minut na pokus').how, /oddělené od celkového termínu/);
});
test('budgets do not claim to cap provider billing', () => {
  assert.match(help('Rozpočet běhů').how, /nejde o měnu ani o limit účtování poskytovatele/);
});
test('organizational roles are not represented as security boundaries', () => {
  assert.match(help('Oddělení').how, /není samostatný bezpečnostní účet/);
  assert.match(help('Omezení').how, /nenahrazují izolaci na úrovni operačního systému/);
});
test('template help does not claim to launch every role', () => {
  assert.match(help('Šablony').how, /nespustí všechny role/);
});
test('preview help explains that files must be saved and is not public hosting', () => {
  assert.match(help('Spustit náhled').how, /Nejprve uložte soubory/);
  assert.match(help('Spustit náhled').how, /nikoli veřejné hostování/);
});
test('profile credentials help asks for the variable name, not the secret', () => {
  assert.match(help('Proměnná přihlašovacího klíče').how, /název proměnné, nikoli tajný klíč/);
  assert.equal(help('Proměnná přihlašovacího klíče').example, 'OPENAI_API_KEY');
});
test('all catalog entries have a purpose, usage and example', () => {
  const entries=vm.runInContext('studioHelpRules', context);
  for(const [pattern,purpose,how,example] of entries) {
    assert.ok(Object.prototype.toString.call(pattern)==='[object RegExp]');
    for(const text of [purpose,how,example]) assert.ok(typeof text==='string' && text.trim().length>0);
  }
});

test('Czech labels keep specific help for review, verification and office controls', () => {
  assert.match(help('Model pro kontrolu').purpose, /kontroluje výsledek realizátora/);
  assert.match(help('Ověřovací příkazy').purpose, /nezávisle ověřují produkt/);
  assert.match(help('3D kancelář').how, /nevytvářejí realizátory/);
  assert.match(help('Obnovit kameru').how, /nikdy nespustí ani nezastaví práci/);
});
test('stable field IDs select help even when the displayed label changes', () => {
  assert.match(help('Povolit automaticky','auto-approve').purpose, /povolené nástroje/);
  assert.match(help('Nastavení přístupu','api_key_env').example, /OPENAI_API_KEY/);
});
