// Dependency-free browser-controller tests. Run: node --test tests/test_workspace_ui.cjs
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness({view = {}} = {}) {
  const elements = new Map(), requests = [];
  class Element {
    constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.dataset = {}; this.value = ''; this.checked = false; this.disabled = false; this.open = false; this._text = ''; }
    get textContent() { return this._text + this.children.map(child => child.textContent || '').join(''); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get childElementCount() { return this.children.length; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._text = ''; this.children = children; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    async fire(type) { for (const listener of this.listeners[type] || []) await listener({preventDefault() {}}); }
    querySelector() { return this.children.find(child => ['INPUT', 'SELECT', 'BUTTON'].includes(child.tagName)) || null; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    focus() {}
    reportValidity() { return !this.required || !!this.value; }
  }
  const html = fs.readFileSync(path.join(__dirname, '../local_osint/static/index.html'), 'utf8');
  for (const match of html.matchAll(/<([a-z]+)[^>]*\bid="([^"]+)"/g)) elements.set(match[2], new Element(match[1]));
  const walk = element => [element, ...element.children.flatMap(walk)];
  const all = () => [...elements.values()].flatMap(walk);
  const document = {
    getElementById: id => elements.get(id) || all().find(element => element.id === id), createElement: tag => new Element(tag),
    createTextNode: text => { const element = new Element('text'); element.textContent = text; return element; },
    addEventListener() {},
    querySelectorAll(selector) {
      if (selector.startsWith('[name=')) {
        const name = selector.match(/name="([^"]+)"/)[1];
        return all().filter(element => element.name === name && (!selector.endsWith(':checked') || element.checked));
      }
      if (selector === '[data-connector-field]') return all().filter(element => element.dataset.connectorField);
      return [];
    }
  };
  const catalogue = [
    {id: 'public_profile', label: 'Public profile', input_kinds: ['username', 'site'], sites: ['github', 'gitlab'], outbound_fields: ['username', 'site'], available: true},
    {id: 'email_headers', label: 'Local headers', input_kinds: ['evidence_id'], outbound_fields: [], available: true},
    {id: 'searxng', label: 'Metasearch', input_kinds: ['query'], available: false, availability: 'unavailable', availability_message: 'Install optional runtime'}
  ];
  const sandbox = {document, window: {addEventListener() {}}, crypto: {randomUUID: () => 'page-test'}, location: {hash: '', pathname: '/'}, history: {replaceState() {}}, URLSearchParams, URL, TextEncoder, setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {}, console,
    fetch: async (url, options = {}) => {
      requests.push({url, body: options.body ? JSON.parse(options.body) : null});
      const result = url === '/api/connectors' ? {connectors: catalogue, search_service: {state: 'unavailable'}}
        : url === '/api/status' ? {network: true} : url === '/api/prompts' ? {defaults: {}, providers: []}
        : url === '/api/ai' ? {state: 'stopped'} : url === '/api/cases' ? []
        : url.startsWith('/api/view?') ? view
        : url.startsWith('/api/workspace?') ? {tasks: [], settings: {}, counts: {}}
        : url.startsWith('/api/investigation?') ? null : {};
      return {ok: true, json: async () => result};
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../local_osint/static/app.js'), 'utf8'), sandbox);
  return {elements, requests, sandbox, run: source => vm.runInContext(source, sandbox), walk};
}

test('source configuration round trips approved public inputs and unlimited page-scoped mode', async () => {
  const ui = harness();
  await new Promise(resolve => setImmediate(resolve));
  await ui.run('loadConnectors()');
  ui.run("currentCase = 'case-a'; setSourceSettings({packs:['public_profile'],public_usernames:['researcher'],seed_urls:['https://example.org/public'],runtime_seconds:0,keep_working:true})");
  const settings = JSON.parse(ui.run('JSON.stringify(sourceSettings())'));
  assert.deepEqual(settings, {packs: ['public_profile'], public_usernames: ['researcher'], seed_urls: ['https://example.org/public'], runtime_seconds: 0, keep_working: true});
  await ui.run('loadConnectors()');
  assert.deepEqual(JSON.parse(ui.run('JSON.stringify(sourceSettings().packs)')), ['public_profile']);
  await ui.run('saveSourceSettings()');
  assert.deepEqual(ui.requests.at(-1), {url: '/api/workspace/settings', body: {case: 'case-a', settings}});
});

test('local source remains usable offline and unavailable optional source is disabled', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run('status.network = false'); await ui.run('loadConnectors()');
  const buttons = ui.walk(ui.elements.get('connector-catalogue')).filter(element => element.tagName === 'BUTTON');
  assert.equal(buttons.find(button => button.dataset.connector === 'email_headers').disabled, false);
  assert.equal(buttons.find(button => button.dataset.connector === 'public_profile').disabled, true);
  assert.equal(buttons.find(button => button.dataset.connector === 'searxng').disabled, true);
});

test('source action submits only explicit form fields and rejects stale case dialog', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run("currentCase = 'case-a'; openConnector(connectors[0], {username:'researcher',site:'github'})");
  // Mock DOM keeps dynamic IDs discoverable through its appended children.
  const fields = ui.walk(ui.elements.get('connector-fields')).filter(element => element.dataset.connectorField);
  fields.find(field => field.dataset.connectorField === 'username').value = 'researcher';
  fields.find(field => field.dataset.connectorField === 'site').value = 'github';
  await ui.elements.get('connector-form').fire('submit');
  const request = ui.requests.find(item => item.url === '/api/action');
  assert.deepEqual(request.body, {case: 'case-a', connector: 'public_profile', arguments: {username: 'researcher', site: 'github'}});
  ui.run("openConnector(connectors[0], {username:'other',site:'github'}); currentCase = 'case-b'");
  const before = ui.requests.filter(item => item.url === '/api/action').length;
  await ui.elements.get('connector-form').fire('submit');
  assert.equal(ui.requests.filter(item => item.url === '/api/action').length, before);
  assert.match(ui.elements.get('status').textContent, /current case/);
});

test('interrupted actions retain inspectable inputs and support explicit retry', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run("currentCase='case-a'; renderResearch({tasks:[{id:'task-a',connector:'public_profile',arguments:{username:'researcher',site:'github'},reason:'Distinguish candidates',state:'interrupted_after_dispatch',priority:2,attempts:1}],candidates:[],claims:[],counts:{leads:3,readable_documents:1,corroborated_claims:0}}, 'case-a')");
  assert.match(ui.elements.get('workspace-progress').textContent, /1 awaiting work/);
  assert.match(ui.elements.get('research-tasks').textContent, /researcher/);
  const retry = ui.walk(ui.elements.get('research-tasks')).find(element => element.tagName === 'BUTTON' && element.textContent === 'Retry');
  await retry.fire('click');
  assert.deepEqual(ui.requests.find(item => item.url === '/api/task').body, {case: 'case-a', id: 'task-a', operation: 'retry'});
});

test('candidate observations expose original passages without claiming identity or parsing source HTML', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run(`currentCase='case-a'; renderResearch({tasks:[],candidates:[{id:'candidate-a',label:'Researcher',context:'Unresolved name match',status:'unconfirmed',source_id:'evidence-a'}],claims:[{id:'claim-a',candidate_id:'candidate-a',predicate:'public observation',value:'Team member',status:'unconfirmed',references:[{evidence_id:'evidence-a',quote:'<img src=x onerror=alert(1)> Team member',extractor_version:'plain-v1',extraction_id:'extract-a',start:10,end:50,relation:'support'}]}],counts:{leads:1,readable_documents:1,corroborated_claims:0}}, 'case-a')`);
  const claims = ui.elements.get('research-claims');
  assert.match(claims.textContent, /unconfirmed/);
  assert.match(claims.textContent, /plain-v1/);
  assert.match(claims.textContent, /characters 10–50/);
  const quote = ui.walk(claims).find(element => element.tagName === 'BLOCKQUOTE');
  assert.equal(quote.textContent, '<img src=x onerror=alert(1)> Team member');
  assert.equal(quote.children.length, 0);
  assert.equal(ui.walk(claims).some(element => element.tagName === 'IMG'), false);
});

test('relationship view preserves support versus contradiction, filters candidates and orders recorded observations', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run(`currentCase='case-a'; renderResearch({tasks:[],candidates:[{id:'candidate-a',label:'Candidate A'},{id:'candidate-b',label:'Candidate B'}],claims:[
    {id:'later',candidate_id:'candidate-a',predicate:'role',value:'Later observation',status:'disputed',created_at:'2026-10-15T12:00:00Z',references:[{evidence_id:'source-a',quote:'Different role',relation:'contradict'}]},
    {id:'earlier',candidate_id:'candidate-b',predicate:'role',value:'Earlier observation',status:'unconfirmed',created_at:'2026-10-14T12:00:00Z',references:[{evidence_id:'source-b',quote:'Candidate role',relation:'support'}]}
  ],counts:{}}, 'case-a')`);
  const relationships = ui.elements.get('evidence-relationships');
  assert.match(relationships.textContent, /Contradicts observation/);
  assert.match(relationships.textContent, /Supports observation/);
  assert.match(relationships.textContent, /not confirmed identity or ownership/);
  const timeline = ui.walk(ui.elements.get('observation-timeline')).filter(element => element.tagName === 'LI');
  assert.match(timeline[0].textContent, /Earlier observation/);
  assert.match(timeline[1].textContent, /Later observation/);
  ui.elements.get('relationship-candidate').value = 'candidate-a';
  await ui.elements.get('relationship-candidate').fire('change');
  assert.match(relationships.textContent, /Later observation/);
  assert.doesNotMatch(relationships.textContent, /Earlier observation/);
});

test('action selection and passage extraction prompts remain editable and included in run settings', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run(`for (const key of Object.keys(promptLabels)) $('prompt-'+key).value = 'Keep scope and verify source passages.';
    $('prompt-action').value='Choose an untried offered action.';
    $('prompt-extract').value='Copy the exact supporting passage.';
    $('request-limit').value='24'; $('page-limit').value='4';
    const tool=node('input');tool.name='investigation-tool';tool.value='web_search';tool.checked=true;$('source-packs').append(tool);
    const provider=node('input');provider.name='search-provider';provider.value='bing';provider.checked=true;$('search-providers').append(provider);`);
  const options = JSON.parse(ui.run('JSON.stringify(getOptions())'));
  assert.equal(options.prompts.action, 'Choose an untried offered action.');
  assert.equal(options.prompts.extract, 'Copy the exact supporting passage.');
  assert.equal(Object.keys(options.prompts).length, 8);
});

test('local email tool offers saved source titles, prefers email originals and submits the selected evidence ID', async () => {
  const ui = harness({view: {evidence: [
    {id:'generated',title:'Investigation report',source:'local-osint:investigation-report',format:'json'},
    {id:'web',title:'Public article',source:'https://example.org/article',format:'html'},
    {id:'headers',title:'Pasted headers',source:'Research notes',format:'text'},
    {id:'email',title:'Message.eml',source:'Message.eml',format:'text'}
  ],limits:{evidence:200}}});
  await new Promise(resolve => setImmediate(resolve));
  await ui.run("currentCase='case-a'; openConnector(connectors.find(item => item.id === 'email_headers'))");
  const select = ui.walk(ui.elements.get('connector-fields')).find(element => element.dataset.connectorField === 'evidence_id');
  assert.equal(select.tagName, 'SELECT');
  assert.deepEqual(select.children.map(option => option.value), ['', 'email', 'headers', 'web']);
  assert.match(select.children[1].textContent, /Message\.eml/);
  assert.doesNotMatch(select.textContent, /Investigation report/);
  select.value = 'headers';
  await ui.elements.get('connector-form').fire('submit');
  assert.deepEqual(ui.requests.find(item => item.url === '/api/action').body, {case:'case-a',connector:'email_headers',arguments:{evidence_id:'headers'}});
});

test('empty saved email selection explains import and prevents running without a source', async () => {
  const ui = harness({view:{evidence:[{id:'report',title:'Report',source:'local-osint:investigation-report',format:'json'}]}});
  await new Promise(resolve => setImmediate(resolve));
  await ui.run("currentCase='case-a'; openConnector(connectors.find(item => item.id === 'email_headers'))");
  assert.equal(ui.elements.get('run-connector').disabled, true);
  assert.match(ui.elements.get('connector-fields').textContent, /Import an email \(\.eml\) or paste its headers/);
});

test('late saved-evidence response cannot alter another source dialog or case', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  const originalFetch = ui.sandbox.fetch;
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  ui.sandbox.fetch = async (url, options) => url.startsWith('/api/view?') ? {ok:true,json:() => delayed} : originalFetch(url, options);
  const opening = ui.run("currentCase='case-a'; openConnector(connectors.find(item => item.id === 'email_headers'))");
  await ui.run("currentCase='case-b'; openConnector(connectors.find(item => item.id === 'public_profile'), {username:'newcase',site:'github'})");
  release({evidence:[{id:'old-email',title:'Old case email.eml',source:'Old case email.eml',format:'text'}]});
  await opening;
  assert.equal(ui.elements.get('connector-dialog-title').textContent, 'Public profile');
  assert.doesNotMatch(ui.elements.get('connector-fields').textContent, /Old case email/);
  const fields = ui.walk(ui.elements.get('connector-fields')).filter(element => element.dataset.connectorField);
  assert.deepEqual(fields.map(field => field.dataset.connectorField), ['username','site']);
});
