// Dependency-free browser-controller tests. Run: node --test tests/test_workspace_ui.cjs
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness({view = {}, evidence = {}} = {}) {
  const elements = new Map(), requests = [], timers = new Map();
  let timerId = 0;
  class Element {
    constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.dataset = {}; this.value = ''; this.checked = false; this.disabled = false; this.open = false; this._text = ''; }
    get textContent() { return this._text + this.children.map(child => child.textContent || '').join(''); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get childElementCount() { return this.children.length; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._text = ''; this.children = children; }
    setAttribute(name, value) { this[name] = value; }
    getAttribute(name) { return this[name] ?? null; }
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    async fire(type, extra = {}) { for (const listener of this.listeners[type] || []) await listener({preventDefault() {}, ...extra}); }
    querySelector() { return this.children.find(child => ['INPUT', 'SELECT', 'BUTTON'].includes(child.tagName)) || null; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    focus() {}
    reportValidity() { return !this.required || !!this.value; }
  }
  const html = fs.readFileSync(path.join(__dirname, '../local_osint/static/index.html'), 'utf8');
  for (const match of html.matchAll(/<([a-z]+)([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const element = new Element(match[1]), attributes = match[2];
    element.checked = /\bchecked\b/.test(attributes);
    element.value = attributes.match(/\bvalue="([^"]*)"/)?.[1] || '';
    elements.set(match[3], element);
  }
  const walk = element => [element, ...element.children.flatMap(walk)];
  const all = () => [...elements.values()].flatMap(walk);
  const document = {
    getElementById: id => elements.get(id) || all().find(element => element.id === id), createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag),
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
  const sandbox = {document, window: {addEventListener() {}}, crypto: {randomUUID: () => 'page-test'}, location: {hash: '', pathname: '/'}, history: {replaceState() {}}, URLSearchParams, URL, TextEncoder, setTimeout: callback => { timers.set(++timerId, callback); return timerId; }, clearTimeout: id => timers.delete(id), setInterval: () => 1, clearInterval() {}, console,
    fetch: async (url, options = {}) => {
      requests.push({url, body: options.body ? JSON.parse(options.body) : null});
      const result = url === '/api/connectors' ? {connectors: catalogue, search_service: {state: 'unavailable'}}
        : url === '/api/status' ? {network: true} : url === '/api/prompts' ? {defaults: {}, providers: [{id:'bing',label:'Bing'},{id:'duckduckgo',label:'DuckDuckGo'}]}
        : url === '/api/ai' ? {state: 'stopped'} : url === '/api/cases' ? []
        : url.startsWith('/api/view?') ? view
        : url.startsWith('/api/evidence?') ? evidence
        : url.startsWith('/api/workspace?') ? {tasks: [], settings: {}, counts: {}}
        : url.startsWith('/api/investigation?') ? null : {};
      return {ok: true, json: async () => result};
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../local_osint/static/app.js'), 'utf8'), sandbox);
  return {elements, requests, timers, sandbox, run: source => vm.runInContext(source, sandbox), walk};
}

test('metasearch polls asynchronous setup failures and supports retry without a selected case', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  const fallback = ui.sandbox.fetch;
  let outcome = {state: 'error', error: 'Docker is unavailable. Start Docker and retry.'};
  ui.sandbox.fetch = async (url, options) => {
    if (url === '/api/search-service/start') return {ok: true, json: async () => ({state: 'preparing'})};
    if (url === '/api/search-service') return {ok: true, json: async () => outcome};
    if (url === '/api/connectors') return {ok: true, json: async () => ({connectors: [], search_service: outcome})};
    return fallback(url, options);
  };
  // The DOM fixture does not model input names from HTML.
  const input = ui.sandbox.document.createElement('input'); input.name = 'metasearch-engine'; input.value = 'bing'; input.checked = true;
  ui.elements.get('source-packs').append(input);
  await ui.elements.get('start-search-service').fire('click');
  assert.equal(ui.elements.get('start-search-service').disabled, true);
  assert.equal(ui.elements.get('stop-search-service').hidden, false);
  assert.equal(ui.timers.size, 1);
  const poll = [...ui.timers.values()][0]; ui.timers.clear(); await poll();
  assert.match(ui.elements.get('search-service-status').textContent, /Start Docker/);
  assert.equal(ui.elements.get('start-search-service').disabled, false);
  assert.equal(ui.timers.size, 0);
  // Retry observes a successful terminal state and keeps Start disabled.
  ui.elements.get('source-packs').append(input);
  outcome = {state: 'ready'};
  await ui.elements.get('start-search-service').fire('click');
  const retryPoll = [...ui.timers.values()][0]; ui.timers.clear(); await retryPoll();
  assert.match(ui.elements.get('search-service-status').textContent, /ready/);
  assert.equal(ui.elements.get('start-search-service').disabled, true);
  assert.equal(ui.timers.size, 0);
});

test('metasearch request failures replace preparing status and leave retry enabled', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  const fallback = ui.sandbox.fetch;
  ui.sandbox.fetch = async (url, options) => url === '/api/search-service/start'
    ? {ok: false, json: async () => ({error: 'Network collection is disabled.'})} : fallback(url, options);
  const input = ui.sandbox.document.createElement('input'); input.name = 'metasearch-engine'; input.value = 'bing'; input.checked = true;
  ui.elements.get('source-packs').append(input);
  await ui.elements.get('start-search-service').fire('click');
  assert.match(ui.elements.get('search-service-status').textContent, /Network collection is disabled/);
  assert.equal(ui.elements.get('start-search-service').disabled, false);
});

test('failed metasearch with an ownership receipt keeps Stop available for recovery', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run("renderSearchService({state:'error', error:'Start Docker and retry.', owned_container:true})");
  assert.equal(ui.elements.get('stop-search-service').hidden, false);
  assert.equal(ui.elements.get('start-search-service').disabled, false);
});

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

test('primary Investigate sends the automatic recipe and public focus without requiring tool choices', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run(`currentCase='case-a'; currentSubject='Example Person';
    for (const key of Object.keys(promptLabels)) $('prompt-'+key).value='Use approved sources and retain uncertainty.';
    $('investigation-query').value='"Example Person"';
    $('investigation-question').value='Which public sources describe this work?';
    $('public-country').value='Ireland'; $('public-language').value='English'; $('public-organization').value='Example Foundation';
    $('identifiers').value='private@example.invalid';`);
  assert.equal(ui.elements.get('use-ai').checked, true);
  assert.equal(ui.elements.get('use-ai').disabled, false);
  await ui.run("startInvestigation('case-a')");
  const request = ui.requests.find(item => item.url === '/api/investigate');
  assert.equal(request.body.ai, true);
  assert.equal(request.body.options.automatic, true);
  assert.equal(request.body.options.question, 'Which public sources describe this work?');
  assert.equal(request.body.options.public_country, 'Ireland');
  assert.equal(request.body.options.public_language, 'English');
  assert.equal(request.body.options.public_organization, 'Example Foundation');
  assert.equal(JSON.stringify(request.body).includes('private@example.invalid'), false);
  assert.ok(request.body.options.tools.includes('web_search'));
});

test('local AI startup state preserves the analysis preference and explicit opt-out', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.elements.get('use-ai').checked, true);
  await ui.run('refreshAI()');
  assert.equal(ui.elements.get('use-ai').checked, true);
  ui.elements.get('use-ai').checked = false;
  await ui.run('refreshAI()');
  assert.equal(ui.elements.get('use-ai').checked, false);
});

test('Continue preserves the automatic public focus and respects an explicit local AI opt-out', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run(`currentCase='case-a';
    for (const key of Object.keys(promptLabels)) $('prompt-'+key).value='Use approved sources and retain uncertainty.';
    setOptions({query:'"Example Person"',question:'Which public sources support this role?',public_country:'Ireland',public_language:'English',public_organization:'Example Foundation'});
    $('use-ai').checked=false;`);
  await ui.run("continueInvestigation('case-a')");
  const request = ui.requests.find(item => item.url === '/api/continue');
  assert.equal(request.body.options.automatic, true);
  assert.equal(request.body.options.question, 'Which public sources support this role?');
  assert.equal(request.body.options.public_country, 'Ireland');
  assert.equal(request.body.options.public_language, 'English');
  assert.equal(request.body.options.public_organization, 'Example Foundation');
  assert.equal(request.body.ai, false);
});

test('candidate decisions remain explicit and questions show unresolved gaps with source references', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run(`currentCase='case-a'; renderResearch({tasks:[],claims:[],counts:{},
    candidates:[{id:'candidate-a',label:'Possible Person',status:'uncertain',source_origin:'https://example.org/team',source_id:'source-a'}],
    questions:[{id:'question-a',text:'Does this source describe the target?',status:'unresolved',gap:'Affiliation has not been independently corroborated.',evidence_ids:['source-a']}]}, 'case-a')`);
  const candidates = ui.elements.get('research-candidates');
  assert.match(candidates.textContent, /https:\/\/example\.org\/team/);
  const buttons = ui.walk(candidates).filter(element => element.dataset.candidateDecision);
  assert.equal(buttons.find(button => button.dataset.candidateDecision === 'uncertain')['aria-pressed'], 'true');
  const questions = ui.elements.get('research-question-list');
  assert.match(questions.textContent, /Affiliation has not been independently corroborated/);
  assert.equal(ui.walk(questions).filter(element => element.tagName === 'BUTTON').length, 1);
  await buttons.find(button => button.dataset.candidateDecision === 'different').fire('click');
  assert.deepEqual(ui.requests.find(item => item.url === '/api/candidate/decision').body, {case:'case-a',candidate:'candidate-a',status:'different'});
});

test('choosing a PDF, office document or image automatically selects its import format', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  for (const [name, mime, format] of [
    ['scan.PDF','application/pdf','pdf'], ['report.docx','','docx'], ['notes.odt','','odt'],
    ['page.png','image/png','png'], ['photo.JPG','image/jpeg','jpeg'], ['scan.tif','image/tiff','tiff'],
    ['scan.tiff','','tiff'], ['download','application/pdf','pdf'], ['message.eml','','text']
  ]) {
    ui.elements.get('file').files = [{name,type:mime}];
    await ui.elements.get('file').fire('change');
    assert.equal(ui.elements.get('format').value, format);
    assert.equal(ui.elements.get('title').value, name);
  }
});

test('document evidence exposes extraction status and pages and retries only local extraction', async () => {
  const ui = harness({evidence:{id:'document-a',title:'Scan.pdf',source:'Scan.pdf',format:'pdf',collected_at:'2026-10-16T12:00:00Z',sha256:'original-hash',text:'Original extraction',mentions:[],document:{status:'partial',text:'Latest local extraction',pages:[{page:1,start:0,end:23}],warnings:['One page needs local OCR language data.']}}});
  await new Promise(resolve => setImmediate(resolve));
  await ui.run("currentCase='case-a'; showEvidence('case-a','document-a')");
  const details = ui.elements.get('detail-content');
  assert.match(details.textContent, /partial/);
  assert.match(details.textContent, /Page 1/);
  assert.match(details.textContent, /One page needs local OCR language data/);
  assert.match(details.textContent, /Latest local extraction/);
  assert.match(details.textContent, /original-hash/);
  const retry = ui.walk(details).find(element => element.dataset.documentReextract === 'document-a');
  await retry.fire('click');
  assert.deepEqual(ui.requests.find(item => item.url === '/api/document/reextract').body, {case:'case-a',id:'document-a',document_options:{ocr:true}});
  assert.equal(ui.requests.some(item => item.url === '/api/action' || item.url === '/api/investigate'), false);
});

test('document claims expose cited page numbers with the exact original evidence reference', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run(`currentCase='case-a'; renderResearch({tasks:[],candidates:[],claims:[{id:'claim-a',predicate:'public statement',value:'A quoted observation',status:'unconfirmed',references:[{evidence_id:'document-a',quote:'Exact text',extractor_version:'documents-v1',start:10,end:20,pages:[{page:2,start:0,end:200},{page:3,start:200,end:400}],relation:'support'}]}],counts:{}},'case-a')`);
  assert.match(ui.elements.get('research-claims').textContent, /Pages 2, 3/);
  assert.match(ui.elements.get('evidence-relationships').textContent, /Pages 2, 3/);
  assert.equal(ui.walk(ui.elements.get('research-claims')).filter(element => element.tagName === 'BUTTON').length, 1);
});

test('evidence atlas draws only recorded provenance and question citations, never inferred target or namesake edges', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  const graph = JSON.parse(ui.run(`JSON.stringify(atlasGraph({candidates:[{id:'candidate-a',label:'Namesake A',status:'uncertain',source_id:'source-a'},{id:'candidate-b',label:'Namesake B',status:'different'}],claims:[{candidate_id:'candidate-a',references:[{evidence_id:'source-a',relation:'support',quote:'Literal text'}]}],questions:[{id:'question-a',text:'Which role is supported?',status:'unresolved',evidence_ids:['source-b']}],origins:[{evidence_id:'source-a',kind:'unknown',established:false}]},'Research target'))`));
  assert.ok(graph.edges.some(edge => edge.from === 'candidate:candidate-a' && edge.to === 'source:source-a'));
  assert.ok(graph.edges.some(edge => edge.from === 'question:question-a' && edge.to === 'source:source-b'));
  assert.equal(graph.edges.some(edge => edge.from === 'focus' || edge.to === 'focus'), false);
  assert.equal(graph.edges.some(edge => edge.from === 'candidate:candidate-b'), false);
  assert.equal(graph.nodes.find(point => point.id === 'source:source-a').status, 'unknown');
});

test('partial-result atlas supports keyboard source activation and keeps unknown origin explicit', async () => {
  const ui = harness({evidence:{id:'source-a',title:'Saved public source',source:'https://example.org/team',format:'text',text:'Literal source passage',mentions:[]}});
  await new Promise(resolve => setImmediate(resolve));
  ui.run(`currentCase='case-a'; currentSubject='Research target'; displayedRun={case_id:'case-a',status:'partial'};
    renderEvidenceAtlas({candidates:[{id:'candidate-a',label:'Possible person',status:'uncertain',source_id:'source-a'}],claims:[],questions:[{id:'question-a',text:'Which role?',status:'unresolved',gap:'No independent corroboration',evidence_ids:['source-a']}],origins:[]},'case-a')`);
  assert.equal(ui.elements.get('evidence-atlas').hidden, false);
  const points = ui.walk(ui.elements.get('atlas-canvas')).filter(element => element.dataset.atlasNode);
  const source = points.find(element => element.dataset.atlasNode === 'source:source-a');
  assert.equal(source.getAttribute('tabindex'), '0');
  await source.fire('keydown', {key:'Enter'});
  assert.match(ui.elements.get('atlas-detail').textContent, /Origin is not established/);
  // A stable background poll must not discard the selected graph or inspector.
  ui.run('renderEvidenceAtlas(atlasState.workspace,currentCase)');
  assert.equal(ui.elements.get('atlas-node-picker').value, 'source:source-a');
  const open = ui.walk(ui.elements.get('atlas-detail')).find(element => element.tagName === 'BUTTON');
  await open.fire('click');
  assert.ok(ui.requests.some(item => item.url === '/api/evidence?case=case-a&id=source-a'));
});

test('atlas question selection exposes gaps and cannot read an old case after switching', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run(`currentCase='case-a'; displayedRun={case_id:'case-a',status:'partial'};
    renderEvidenceAtlas({candidates:[],claims:[],origins:[],questions:[{id:'q',text:'Which affiliation?',status:'unresolved',gap:'Affiliation remains uncorroborated',evidence_ids:['source-a']}]},'case-a');
    inspectAtlasPoint('question:q')`);
  assert.match(ui.elements.get('atlas-detail').textContent, /Affiliation remains uncorroborated/);
  const read = ui.walk(ui.elements.get('atlas-detail')).find(element => element.tagName === 'BUTTON');
  const before = ui.requests.length; ui.run("currentCase='case-b'");
  await read.fire('click');
  assert.equal(ui.requests.length, before);
});

test('atlas remains hidden during collection and for empty results', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run("currentCase='case-a'; displayedRun={case_id:'case-a',status:'running'}; renderEvidenceAtlas({questions:[{id:'q',text:'Question'}]},'case-a')");
  assert.equal(ui.elements.get('evidence-atlas').hidden, true);
  ui.run("displayedRun={case_id:'case-a',status:'complete'}; renderEvidenceAtlas({candidates:[],claims:[],questions:[],origins:[]},'case-a')");
  assert.equal(ui.elements.get('evidence-atlas').hidden, true);
});

test('atlas preserves saved evidence titles when report page records omit their title', async () => {
  const ui = harness(); await new Promise(resolve => setImmediate(resolve));
  ui.run(`currentCase='case-a'; displayedRun={case_id:'case-a',status:'partial',pages:[{source_id:'source-a',url:'https://example.org/team'}]};
    atlasEvidence={caseId:'case-a',items:[{id:'source-a',title:'Named public team source',source:'https://example.org/team'}]};
    renderEvidenceAtlas({candidates:[{id:'candidate-a',label:'Possible match',source_id:'source-a'}],claims:[],questions:[],origins:[]},'case-a');
    inspectAtlasPoint('source:source-a')`);
  assert.match(ui.elements.get('atlas-detail').textContent, /Named public team source/);
  assert.match(ui.elements.get('atlas-node-picker').textContent, /Named public team source/);
});
