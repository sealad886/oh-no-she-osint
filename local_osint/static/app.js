'use strict';
const $ = id => document.getElementById(id);
let currentCase = '', currentSubject = '', optionsCase = '', status = {}, requestVersion = 0, aiReady = false;
let promptDefaults = {}, searchProviders = [];
const promptLabels = {plan: 'Plan the next useful action', repetition: 'Recover from repeated or stalled actions', provider_failure: 'Recover from search-provider failure', no_useful_pages: 'Recover when pages are not useful', identity_ambiguity: 'Handle uncertain identity matches', draft: 'Write the final sourced draft', action: 'Choose the next queued action', extract: 'Extract observations with exact supporting passages'};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function notice(message) { $('status').textContent = message; }
async function api(path, data) {
  const response = await fetch(path, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {'X-OSINT-Local': '1', ...(data === undefined ? {} : {'Content-Type': 'application/json'})},
    credentials: 'same-origin',
    ...(data === undefined ? {} : {body: JSON.stringify(data)})
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}
const pageLeaseId = crypto.randomUUID();
let pageLeaseTimer, pageLeaseEnabled = false, pageLeaseOpened = false, pageLeaving = false;
async function openPageLease() {
  if (!pageLeaseEnabled || pageLeaving) return;
  try {
    await api('/api/page/open', {id: pageLeaseId});
    if (pageLeaving) { closePageLease(); return; }
    pageLeaseOpened = true;
    clearInterval(pageLeaseTimer);
    pageLeaseTimer = setInterval(heartbeatPageLease, 15000);
  } catch (_) { /* Lifecycle errors must not interrupt case work. */ }
}
async function heartbeatPageLease() {
  if (!pageLeaseEnabled || pageLeaving) return;
  if (!pageLeaseOpened) { await openPageLease(); return; }
  try {
    const lease = await api('/api/page/heartbeat', {id: pageLeaseId});
    if (!lease.registered && !pageLeaving) { pageLeaseOpened = false; await openPageLease(); }
  }
  catch (_) { /* A stopped local runtime needs no user-facing alert. */ }
}
function closePageLease() {
  pageLeaving = true;
  pageLeaseOpened = false;
  clearInterval(pageLeaseTimer);
  if (!pageLeaseEnabled) return;
  fetch('/api/page/close', {
    method: 'POST', credentials: 'same-origin', keepalive: true,
    headers: {'X-OSINT-Local': '1', 'Content-Type': 'application/json'},
    body: JSON.stringify({id: pageLeaseId})
  }).catch(() => {});
}
window.addEventListener('pagehide', closePageLease);
window.addEventListener('pageshow', event => {
  if (event.persisted && pageLeaseEnabled) { pageLeaving = false; openPageLease(); }
});
document.addEventListener('visibilitychange', heartbeatPageLease);
function action(element, callback, event = 'click') {
  element.addEventListener(event, async e => {
    e.preventDefault();
    const button = element.tagName === 'BUTTON' ? element : element.querySelector('button[type=submit]');
    if (button) button.disabled = true;
    try { await callback(e); }
    catch (error) { notice(error.message || 'Operation failed. Try again.'); }
    finally { if (button) button.disabled = false; updateRunControls(); }
  });
}
async function loadCases(preferred = currentCase) {
  const cases = await api('/api/cases');
  $('case-select').replaceChildren();
  for (const item of cases) {
    const option = node('option', item.name);
    option.value = item.id;
    $('case-select').append(option);
  }
  currentCase = cases.some(item => item.id === preferred) ? preferred : (cases[0]?.id || '');
  $('case-select').value = currentCase;
  $('empty').hidden = !!currentCase;
  $('case-workspace').hidden = !currentCase;
  $('case-select').disabled = !currentCase;
  await refresh();
}
function resultButton(kind, value, count, callback) {
  const button = node('button', undefined, 'result');
  button.type = 'button';
  button.append(node('span', kind, 'kind'), node('span', value, 'value'), node('span', count, 'count'));
  action(button, callback);
  return button;
}
async function refresh() {
  const version = ++requestVersion;
  if (!currentCase) return;
  const caseId = currentCase;
  const data = await api('/api/view?' + new URLSearchParams({case: caseId, q: $('search').value, kind: $('kind').value}));
  if (version !== requestVersion || currentCase !== caseId) return;
  $('case-title').textContent = data.case.name;
  $('subject-summary').textContent = [data.case.focus_type.toUpperCase(), data.case.aliases.length ? 'Aliases: ' + data.case.aliases.join(', ') : '', data.case.identifiers.length ? 'Known identifiers (user supplied): ' + data.case.identifiers.join(', ') : ''].filter(Boolean).join(' · ');
  currentSubject = data.case.name;
  const loadOptions = optionsCase !== caseId;
  if (loadOptions) {
    optionsCase = caseId;
    setOptions({query: '"' + data.case.name + '"', context: '', tools: ['web_search', 'wikipedia', 'wikidata', 'news', 'domain'], max_requests: 24, page_limit: 4});
  }
  $('evidence-count').textContent = data.counts.evidence;
  $('entity-count').textContent = data.counts.entities;
  $('mention-count').textContent = data.counts.mentions;
  $('indicators').replaceChildren();
  $('sources').replaceChildren();
  for (const item of data.entities) $('indicators').append(resultButton(item.kind, item.value, `${item.sources} source${item.sources === 1 ? '' : 's'} ↗`, () => showEntity(caseId, item.id)));
  for (const item of data.evidence) $('sources').append(resultButton(item.format, item.title, `${Math.max(1, Math.round(item.bytes / 1024))} KB ↗`, () => showEvidence(caseId, item.id)));
  if (!data.entities.length) $('indicators').append(node('p', data.counts.evidence ? 'No matching indicators. Try a different search or inspect the original evidence.' : 'Your first clue is waiting. Add evidence to begin.', 'empty-results'));
  if (!data.evidence.length) $('sources').append(node('p', 'No matching evidence. Add a source or adjust your search.', 'empty-results'));
  if (data.entities.length === data.limits.entities) $('indicators').append(node('p', 'Showing up to 500 indicators. Narrow your search; exports include all results.', 'small'));
  await refreshRun(caseId, loadOptions);
  await refreshResearch(caseId, loadOptions);
  if (data.evidence.length === data.limits.evidence) $('sources').append(node('p', 'Showing up to 200 sources. Narrow your search; exports include all results.', 'small'));
}
function detail(title) {
  const area = $('detail-content');
  area.replaceChildren(node('h2', title));
  if (!$('detail-dialog').open) $('detail-dialog').showModal();
  return area;
}
async function showEvidence(caseId, id) {
  const evidence = await api('/api/evidence?' + new URLSearchParams({case: caseId, id}));
  if (currentCase !== caseId) return;
  const area = detail(evidence.title);
  area.append(node('p', 'Source: ' + evidence.source, 'detail-meta'), node('p', 'Collected: ' + evidence.collected_at, 'detail-meta'), node('p', 'SHA-256: ' + evidence.sha256, 'detail-meta'), node('pre', evidence.text, 'evidence-text'), node('p', `${evidence.mentions.length} indicator mentions. Offsets refer to extracted searchable text; original bytes are in the evidence export.`, 'small'));
}
async function showEntity(caseId, id) {
  const entity = await api('/api/entity?' + new URLSearchParams({case: caseId, id}));
  if (currentCase !== caseId) return;
  const area = detail(entity.value);
  area.append(node('p', entity.kind.toUpperCase() + ' / EXTRACTED MENTION', 'eyebrow'), node('h3', 'Where it appears'));
  for (const evidence of entity.evidence) area.append(resultButton('source', evidence.title, 'Open ↗', () => showEvidence(caseId, evidence.id)));
  area.append(node('h3', 'Seen in the same sources'), node('p', 'Co-occurrence is a research lead. It does not prove ownership, identity or a direct relationship.', 'small'));
  for (const related of entity.related) area.append(resultButton(related.kind, related.value, `${related.shared_sources} shared ↗`, () => showEntity(caseId, related.id)));
  if (!entity.related.length) area.append(node('p', 'No other indicators share this evidence.'));
  if (entity.related.length === 100) area.append(node('p', 'Showing up to 100 related indicators.', 'small'));
}
function base64(bytes) {
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  return btoa(binary);
}
async function download(format) {
  const caseId = currentCase;
  const response = await fetch('/api/export?' + new URLSearchParams({case: caseId, format}), {headers: {'X-OSINT-Local': '1'}, credentials: 'same-origin'});
  if (!response.ok) { const error = await response.json(); throw new Error(error.error); }
  const blob = await response.blob(), url = URL.createObjectURL(blob), link = node('a');
  link.href = url;
  link.download = format === 'markdown' ? 'case-report.md' : 'case-evidence.json';
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  notice('Export ready. Keep downloaded evidence private; your browser controls download-folder permissions.');
}
for (const id of ['new-case', 'first-case']) action($(id), () => { $('case-dialog').showModal(); $('case-name').focus(); });
action($('cancel-case'), () => $('case-dialog').close());
action($('close-detail'), () => $('detail-dialog').close());
action($('case-form'), async () => {
  const created = await api('/api/cases', {name: $('case-name').value, focus_type: $('focus-type').value, aliases: $('aliases').value.split('\n').map(x => x.trim()).filter(Boolean), identifiers: $('identifiers').value.split('\n').map(x => x.trim()).filter(Boolean)});
  $('case-dialog').close(); $('case-form').reset(); $('search').value = ''; $('kind').value = '';
  await loadCases(created.id);
  if (status.network) await startInvestigation(created.id);
  else notice('Offline case created. Import saved evidence to begin.');
}, 'submit');
action($('case-select'), async () => { currentCase = $('case-select').value; $('search').value = ''; $('kind').value = ''; await refresh(); }, 'change');
let searchTimer;
$('search').addEventListener('input', () => { clearTimeout(searchTimer); ++requestVersion; searchTimer = setTimeout(() => refresh().catch(error => notice(error.message)), 220); });
action($('kind'), refresh, 'change');
$('file').addEventListener('change', () => {
  const file = $('file').files[0];
  if (!file) return;
  $('title').value = file.name;
  $('format').value = ({html: 'html', htm: 'html', json: 'json', csv: 'csv'})[file.name.split('.').pop().toLowerCase()] || 'text';
});
action($('import-form'), async () => {
  const caseId = currentCase, file = $('file').files[0], pasted = $('paste').value;
  if (file && pasted.trim()) throw new Error('Choose a file or paste notes, one at a time.');
  if (!file && !pasted.trim()) throw new Error('Choose a file or paste some notes first.');
  if (file && file.size > status.max_bytes) throw new Error('File exceeds the 2 MiB evidence limit.');
  const bytes = file ? new Uint8Array(await file.arrayBuffer()) : new TextEncoder().encode(pasted);
  if (bytes.length > status.max_bytes) throw new Error('Notes exceed the 2 MiB evidence limit.');
  const imported = await api('/api/import', {case: caseId, title: $('title').value, source: $('source').value.trim() || (file ? file.name : 'Pasted notes'), format: $('format').value, original_base64: base64(bytes)});
  if (currentCase === caseId) { $('import-form').reset(); await refresh(); }
  notice(imported.duplicate ? 'Already in this case. Original evidence kept.' : `Evidence saved. ${imported.mentions} indicator mentions found.`);
}, 'submit');
action($('export-json'), () => download('json'));
action($('export-md'), () => download('markdown'));
for (const name of ['indicators', 'sources']) action($(name + '-tab'), () => {
  for (const tab of ['indicators', 'sources']) { $(tab).hidden = tab !== name; $(tab + '-tab').setAttribute('aria-selected', String(tab === name)); }
});
for (const [index, id] of ['indicators-tab', 'sources-tab'].entries()) $(id).addEventListener('keydown', event => {
  if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const target = ['indicators-tab', 'sources-tab'][event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index];
    $(target).focus(); $(target).click();
  }
});
(async () => {
  try {
    const fragment = new URLSearchParams(location.hash.slice(1)), token = fragment.get('token');
    history.replaceState(null, '', location.pathname);
    if (token) await api('/api/session', {token});
    status = await api('/api/status');
    pageLeaseEnabled = true;
    await openPageLease();
    $('mode').textContent = status.network ? 'LOCAL PROCESSING' : 'OFFLINE MODE';
    $('create-investigation').textContent = status.network ? 'Start investigation ↗' : 'Create offline case';
    $('start-notice').hidden = !status.network;
    $('run-investigation').disabled = !status.network;
    await loadPromptSettings();
    await loadConnectors();
    await refreshAI();
    $('workspace').hidden = false;
    await loadCases();
  } catch (error) { $('locked').hidden = false; notice(error.message); }
})();

let displayedRun = null, runTimer, aiTimer;
async function loadPromptSettings() {
  const settings = await api('/api/prompts');
  promptDefaults = settings.defaults;
  searchProviders = settings.providers;
  const providers = $('search-providers'); providers.replaceChildren();
  for (const provider of searchProviders) {
    const label = node('label'), input = node('input'); input.type = 'checkbox'; input.name = 'search-provider'; input.value = provider.id; input.checked = true;
    label.append(input, document.createTextNode(provider.label)); providers.append(label);
  }
  const prompts = $('prompt-settings'); prompts.replaceChildren();
  for (const [key, label] of Object.entries(promptLabels)) {
    const text = node('label', label); text.htmlFor = 'prompt-' + key;
    const input = node('textarea'); input.id = 'prompt-' + key; input.dataset.promptKey = key; input.maxLength = 4000; input.rows = 4; input.value = promptDefaults[key] || '';
    prompts.append(text, input);
  }
}
function setRecoveryOptions(options = {}) {
  const providers = options.providers || searchProviders.map(provider => provider.id);
  for (const input of document.querySelectorAll('[name="search-provider"]')) input.checked = providers.includes(input.value);
  const prompts = {...promptDefaults, ...(options.prompts || {})};
  for (const [key] of Object.entries(promptLabels)) $('prompt-' + key).value = prompts[key] || '';
}
function providerLabel(value) {
  return searchProviders.find(provider => provider.id === value)?.label || ({wikipedia: 'Wikipedia (English)', wikidata: 'Wikidata'})[value] || value;
}
function setOptions(options = {}) {
  $('investigation-query').value = options.query || ('"' + currentSubject + '"');
  $('investigation-context').value = options.context || '';
  $('request-limit').value = options.max_requests || 24;
  $('page-limit').value = options.page_limit || 4;
  const tools = options.tools || ['web_search', 'wikipedia', 'wikidata', 'news', 'domain'];
  for (const input of document.querySelectorAll('[name="investigation-tool"]')) input.checked = tools.includes(input.value);
  setRecoveryOptions(options);
}
function getOptions() {
  const tools = [...document.querySelectorAll('[name="investigation-tool"]:checked')].map(input => input.value);
  if (!tools.length) throw new Error('Select at least one investigation tool.');
  for (const id of ['request-limit', 'page-limit']) if (!$(id).reportValidity()) throw new Error('Check the investigation limits.');
  if (['request-limit', 'page-limit'].some(id => !Number.isSafeInteger(Number($(id).value)))) throw new Error('Use whole numbers within the browser’s exact integer range for investigation limits.');
  const providers = [...document.querySelectorAll('[name="search-provider"]:checked')].map(input => input.value);
  if (!providers.length) throw new Error('Select at least one public search provider.');
  const prompts = {};
  for (const [key] of Object.entries(promptLabels)) {
    if ($('prompt-' + key).value.length > 4000) throw new Error('Each AI prompt must be at most 4,000 characters.');
    prompts[key] = $('prompt-' + key).value;
  }
  return {query: $('investigation-query').value.trim(), context: $('investigation-context').value.trim(), tools, providers, prompts, max_requests: Number($('request-limit').value), page_limit: Number($('page-limit').value)};
}
function updateRunControls() {
  const busy = displayedRun?.status === 'running';
  $('run-investigation').disabled = busy || !status.network || !currentCase;
  $('continue-investigation').hidden = !displayedRun || busy;
  $('continue-investigation').disabled = busy || !status.network || !currentCase;
  $('stop-investigation').hidden = !busy;
  $('investigation-options').disabled = busy || !status.network;
  $('recovery-options').disabled = busy;
  $('new-tool').disabled = busy || !currentCase;
  $('retry-ai').disabled = busy || !currentCase || !aiReady;
  for (const button of document.querySelectorAll('[data-tool-action]')) button.disabled = busy || !currentCase || (!status.network && button.dataset.toolAction !== 'read_evidence') || (button.dataset.toolAction === 'analyze' && !aiReady);
  if ($('tool-dialog').open) updateToolSubmit();
}
async function startInvestigation(caseId) {
  await saveSourceSettings(caseId);
  notice('Investigation started. Sources and progress appear below.');
  const report = await api('/api/investigate', {case: caseId, ai: $('use-ai').checked, options: getOptions()});
  if (currentCase === caseId) renderRun(report, caseId);
}
async function continueInvestigation(caseId) {
  await saveSourceSettings(caseId);
  notice('Continuing from saved evidence. Local AI will plan the next useful sources.');
  const report = await api('/api/continue', {case: caseId, options: getOptions()});
  if (currentCase === caseId) renderRun(report, caseId);
  await refreshAI();
}
function sourceLink(area, caseId, id, label = 'View source') {
  if (id) area.append(resultButton('source', label, 'Open ↗', () => showEvidence(caseId, id)));
}
const toolLabels = {web_search: 'Public web search', search_subject: 'Public profile search', wikipedia: 'Wikipedia candidates', wikidata: 'Wikidata candidates', news_search: 'Recent news search', news: 'Recent news search', collect_page: 'Public page collection', domain_signals: 'Domain signals', dns: 'DNS addresses', registration: 'Domain registration', certificate: 'TLS certificate', website: 'Public website', read_evidence: 'Read saved evidence', analyze: 'Local AI analysis', local_ai: 'Local AI request', ai_request: 'Local AI request'};
const argumentLabels = {query: 'Query', context: 'Public context', url: 'URL', domain: 'Domain', provider: 'Provider', signals: 'Signals', id: 'Evidence ID'};
function argumentList(args) {
  const list = node('dl', undefined, 'tool-arguments');
  for (const [key, value] of Object.entries(args || {})) {
    if (value === undefined || value === null || value === '') continue;
    list.append(node('dt', argumentLabels[key] || key), node('dd', key === 'provider' && typeof value === 'string' ? providerLabel(value) : Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value)));
  }
  return list;
}
function editableStep(step, report) {
  let tool = step.tool, args = {...(step.arguments || {})};
  if (!tool) {
    const label = step.label || '';
    const search = label.match(/^Search (wikipedia|wikidata): (.+)$/i);
    const page = label.match(/^Collect page: (https:\/\/.+)$/);
    const domain = label.match(/^(Dns|Registration|Certificate|Website): (.+)$/i);
    if (search) { tool = 'search_subject'; args = {provider: search[1].toLowerCase(), query: search[2]}; }
    else if (label === 'Search recent news') { tool = 'news_search'; args = {query: report?.subject || currentSubject}; }
    else if (page) { tool = 'collect_page'; args = {url: page[1]}; }
    else if (domain) { tool = 'domain_signals'; args = {domain: domain[2], signals: [domain[1].toLowerCase()]}; }
    else if (label.startsWith('Read profile: ')) {
      const candidate = report?.candidates?.find(item => item.label === label.slice(14));
      if (candidate?.collection_url) { tool = 'collect_page'; args = {url: candidate.collection_url}; }
    }
  }
  if (['wikipedia', 'wikidata'].includes(tool)) { args.provider = tool; tool = 'search_subject'; }
  if (tool === 'news') tool = 'news_search';
  if (['dns', 'registration', 'certificate', 'website'].includes(tool) && args.domain) { args.signals = [tool]; tool = 'domain_signals'; }
  if (['local_ai', 'ai_request'].includes(tool)) { tool = 'analyze'; args = {}; }
  return ['web_search', 'search_subject', 'news_search', 'collect_page', 'domain_signals', 'read_evidence', 'analyze'].includes(tool) ? {tool, args} : null;
}
function addToolButton(area, label, caseId, tool, args) {
  const button = node('button', label, 'secondary'); button.type = 'button'; button.dataset.toolAction = tool;
  action(button, () => openTool(caseId, tool, args)); area.append(button);
}
function renderActivity(report, caseId) {
  const area = $('tool-activity'); area.replaceChildren();
  const steps = report?.steps || [];
  const active = steps.filter(step => step.status === 'running');
  $('activity-current').textContent = active.length ? 'Running now: ' + active.map(step => toolLabels[step.tool] || step.label || 'Investigation tool').join(' · ') : report?.status === 'running' ? (report.stage || 'Preparing the next tool…') : 'Select a tool to inspect its inputs or run it again.';
  if (!steps.length) area.append(node('p', 'Tool calls appear here as they start. Their inputs, results, timings and source evidence stay with this investigation.', 'small muted'));
  for (const step of [...steps].reverse()) {
    const stepStatus = step.status || (step.ok ? 'success' : 'failed');
    const card = node('article', undefined, 'tool-step step-' + stepStatus);
    const heading = node('div', undefined, 'step-heading');
    heading.append(node('h4', step.label || toolLabels[step.tool] || 'Collection step'), node('span', ({running: 'Running', success: 'Saved', failed: 'Failed', skipped: 'Skipped', cancelled: 'Cancelled'})[stepStatus] || stepStatus, 'step-status'));
    card.append(heading);
    const metadata = [(step.origin === 'ai' ? 'AI requested' : step.origin === 'user' ? 'You requested' : 'Automatic'), toolLabels[step.tool] || step.tool, Number.isFinite(step.duration_ms) ? (step.duration_ms < 1000 ? step.duration_ms + ' ms' : (step.duration_ms / 1000).toFixed(1) + ' s') : '', step.started_at ? new Date(step.started_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : ''].filter(Boolean);
    card.append(node('p', metadata.join(' · '), 'small muted'));
    const editable = editableStep(step, report);
    const args = Object.keys(step.arguments || {}).length ? step.arguments : editable?.args || {};
    if (Object.keys(args).length) card.append(argumentList(args));
    if (step.result_summary) card.append(node('p', step.result_summary, 'step-result'));
    if (step.error) card.append(node('p', step.error, 'step-error'));
    sourceLink(card, caseId, step.source_id, 'Saved tool evidence');
    if (editable && stepStatus !== 'running') addToolButton(card, stepStatus === 'failed' ? 'Edit & retry' : 'Edit & run again', caseId, editable.tool, editable.args);
    area.append(card);
  }
}
function renderContinuation(report) {
  const area = $('continuation-plan'), continuation = report?.continuation;
  area.replaceChildren(); area.hidden = !continuation;
  if (!continuation) return;
  area.append(node('h3', 'Local AI continuation plan'), node('p', 'Saved evidence and useful pages carry forward. This continuation has a fresh collection ceiling and time limit. Planner choices are unverified; actual tool outcomes appear in activity below.', 'small muted'));
  if (continuation.strategy) area.append(node('p', 'Current recovery strategy: ' + (promptLabels[continuation.strategy] || continuation.strategy), 'small'));
  if (continuation.excluded_hosts?.length) area.append(node('p', 'Hosts excluded from follow-up: ' + continuation.excluded_hosts.join(', '), 'small'));
  if (continuation.stop_reason) area.append(node('p', continuation.stop_reason, 'small'));
  const decisions = continuation.decisions || [];
  if (!decisions.length) area.append(node('p', report.status === 'running' ? 'Preparing a plan from saved sources and tool outcomes…' : 'No further planner actions selected.', 'small'));
  for (const decision of decisions) {
    const card = node('article', undefined, 'tool-step');
    const heading = node('div', undefined, 'step-heading');
    heading.append(node('h4', toolLabels[decision.action] || decision.action || 'Planner decision'), node('span', decision.status || 'Planned', 'step-status'));
    card.append(heading);
    if (decision.reason) card.append(node('p', decision.reason, 'small'));
    if (decision.strategy) card.append(node('p', 'Strategy: ' + (promptLabels[decision.strategy] || decision.strategy), 'small muted'));
    if (decision.provider && decision.arguments?.provider !== decision.provider) card.append(node('p', 'Provider: ' + providerLabel(decision.provider), 'small'));
    if (decision.error) card.append(node('p', decision.error, 'step-error'));
    if (Object.keys(decision.arguments || {}).length) card.append(argumentList(decision.arguments));
    area.append(card);
  }
}
function publicLink(area, url, text) {
  try {
    const parsed = new URL(url);
    if (!['https:', 'http:'].includes(parsed.protocol)) return;
    const link = node('a', text || url, 'public-link'); link.href = parsed.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; area.append(link);
  } catch (_) { /* Invalid public URLs remain visible as plain source text. */ }
}
function matchLabel(match, relevant) {
  return ({identifier_match: 'LOCALLY CORROBORATED LEAD / IDENTITY UNCONFIRMED', context_match: 'CONTEXT MATCH / IDENTITY UNCONFIRMED', name_only: 'NAME MATCH ONLY / IDENTITY UNCONFIRMED', unrelated: 'UNRELATED OR UNCORROBORATED LEAD'})[match?.level] || (relevant === true ? 'NAME MATCH / IDENTITY UNCONFIRMED' : relevant === false ? 'NOT SELECTED FOR FOLLOW-UP' : 'UNCONFIRMED PUBLIC LEAD');
}
function localMatchNote(area, match) {
  if (!match) return;
  if (match.summary) area.append(node('p', match.summary, 'small'));
  const labels = {email: 'Known email', phone: 'Known phone', known_url: 'Known public URL', domain: 'Known domain', context: 'Known context', name: 'Subject name', alias: 'Known alias'};
  const signals = (match.signals || []).map(signal => {
    const kind = labels[signal.kind] || 'Local comparison';
    const identifier = Number.isInteger(signal.identifier_index) ? ' #' + (signal.identifier_index + 1) : '';
    return kind + identifier + (signal.strength ? ' · ' + signal.strength : '');
  });
  if (signals.length) area.append(node('p', 'Local comparison: ' + signals.join('; '), 'small muted'));
  const assessment = {context_consistent: 'Public material appears consistent with known private context.', context_unlikely: 'Public material appears unlikely to fit known private context.', uncertain: 'Known private context does not resolve this lead.'}[match.model_assessment];
  if (assessment) area.append(node('p', 'Local AI hint — unverified: ' + assessment, 'small muted'));
}
function renderRun(report, caseId) {
  if (currentCase !== caseId) return;
  if (displayedRun?.case_id === caseId) {
    if (!report && displayedRun.status === 'running') return;
    if (report && report.run_id !== displayedRun.run_id && Date.parse(report.started_at) < Date.parse(displayedRun.started_at)) return;
    if (report && report.run_id === displayedRun.run_id && displayedRun.status !== 'running' && report.status === 'running') return;
  }
  clearTimeout(runTimer);
  displayedRun = report;
  const running = report?.status === 'running';
  const steps = report?.steps || [], budget = report?.budget || {used: report?.request_units || 0, limit: report?.options?.max_requests || 24};
  const currentSteps = steps.some(step => step.run_id) ? steps.filter(step => step.run_id === report.run_id) : steps;
  const usefulPages = Number.isFinite(report?.quality_count) ? report.quality_count : (report?.pages || []).filter(page => page.useful === true && (!page.run_id || page.run_id === report.run_id)).length;
  const qualityProgress = report && (Number.isFinite(report.quality_count) || report.pages?.some(page => typeof page.useful === 'boolean')) ? ` · ${usefulPages} useful pages (goal ${report.options?.page_limit || 4})` : '';
  $('run-progress').textContent = report ? `${report.stage || report.status} · ${currentSteps.filter(s => s.ok || s.status === 'success').length} successful tool calls${qualityProgress} · ${budget.used} collection units used (ceiling ${budget.limit})` : 'Enter a subject and run an investigation. No manual evidence required.';
  $('run-stop-reason').textContent = report?.stop_reason || (report && !running ? 'The selected work finished. The request ceiling is a safety limit, not a completion target. See individual tool outcomes below.' : '');
  renderContinuation(report);
  renderActivity(report, caseId);
  const area = $('dossier'); area.replaceChildren();
  if (report) {
    area.append(node('p', report.interpretation, 'small'));
    if (report.error) area.append(node('p', report.error));
    if (report.web_results?.length) {
      area.append(node('h3', 'Public web leads'), node('p', 'Known identifiers are compared with collected material locally. Private contact details can guide the local model and are not added to web searches. Local corroboration helps rank leads; it does not confirm identity.', 'small muted'));
      for (const result of report.web_results) {
        const card = node('article', undefined, 'profile-card web-result');
        card.append(node('p', matchLabel(result.local_match, result.relevant), 'eyebrow'), node('h4', result.title || result.url), node('p', result.snippet || ''), node('p', result.url, 'detail-meta'));
        localMatchNote(card, result.local_match);
        if (result.reason) card.append(node('p', result.reason, 'small muted'));
        publicLink(card, result.url, 'Open public source ↗');
        sourceLink(card, caseId, result.source_id, 'Original web search evidence');
        sourceLink(card, caseId, result.page_source_id, 'Collected source page');
        if (!result.page_source_id) addToolButton(card, 'Collect this page', caseId, 'collect_page', {url: result.url});
        area.append(card);
      }
    }
    if (report.pages?.length) {
      area.append(node('h3', 'Collected public pages'));
      for (const page of report.pages) {
        const card = node('article', undefined, 'profile-card');
        if (typeof page.useful === 'boolean') {
          card.append(node('p', page.useful ? 'USEFUL PUBLIC SOURCE / IDENTITY UNCONFIRMED' : 'SAVED PAGE / NOT USEFUL FOR THE PAGE GOAL', 'eyebrow'));
          if (page.quality_reason) card.append(node('p', page.quality_reason, page.useful ? 'small' : 'step-error'));
        }
        if (page.local_match) { card.append(node('p', matchLabel(page.local_match), 'small')); localMatchNote(card, page.local_match); }
        card.append(node('h4', page.title || page.final_url || page.url), node('p', page.excerpt || ''), node('p', page.final_url || page.url, 'detail-meta'));
        sourceLink(card, caseId, page.source_id, 'Original collected page');
        if (page.useful === false) addToolButton(card, 'Inspect & retry page', caseId, 'collect_page', {url: page.url});
        const links = (page.links || []).slice(0, 6);
        if (links.length) {
          const more = node('details'); more.append(node('summary', 'Public links found on this page'));
          for (const link of links) {
            const url = typeof link === 'string' ? link : link.url;
            if (!url) continue;
            const row = node('div', undefined, 'page-link'); row.append(node('p', typeof link === 'string' ? link : (link.title || link.text || url), 'small'));
            addToolButton(row, 'Follow link', caseId, 'collect_page', {url}); more.append(row);
          }
          card.append(more);
        }
        area.append(card);
      }
    }
    for (const profile of report.profiles || []) {
      const card = node('article', undefined, 'profile-card');
      card.append(node('p', 'UNCONFIRMED CANDIDATE / ' + profile.candidate_id, 'eyebrow'), node('h3', profile.label), node('p', profile.description));
      for (const claim of profile.claims) {
        const context = (claim.qualifiers || []).map(q => q.label + ': ' + q.value);
        if (claim.qualifiers_truncated) context.push('More qualifiers in original source');
        card.append(node('p', claim.label + ': ' + claim.value + ' · ' + (claim.rank || 'normal') + ' statement' + (context.length ? ' · ' + context.join('; ') : '')));
      }
      sourceLink(card, caseId, profile.source_id, 'Original candidate profile');
      const labelIds = [...new Set(profile.claims.map(c => c.label_source_id).filter(Boolean))];
      for (const id of labelIds) sourceLink(card, caseId, id, 'Relationship label source');
      area.append(card);
    }
    if (report.articles?.length) {
      area.append(node('h3', 'Recent news mentioning this name'));
      for (const article of report.articles) {
        const card = node('article', undefined, 'profile-card');
        card.append(node('h4', article.title), node('p', article.url, 'detail-meta'), node('p', [article.seendate, article.language, article.sourcecountry].filter(Boolean).join(' · '), 'small'));
        sourceLink(card, caseId, article.page_source_id || article.source_id, article.page_source_id ? 'Collected article' : 'News search source');
        area.append(card);
      }
    }
    if (report.domain_lead) area.append(node('p', `Domain lead: ${report.domain_lead.domain} · ${report.domain_lead.basis}`, 'small'));
    if (!report.profiles?.length && report.candidates?.length) {
      area.append(node('h3', 'Public search leads'));
      for (const candidate of report.candidates.slice(0, 10)) {
        area.append(node('p', candidate.label + ' — ' + candidate.description));
        sourceLink(area, caseId, candidate.discovery_source_id);
      }
    }
    if (report.ai) {
      area.append(node('h3', 'Local AI draft — verify against sources'), node('pre', report.ai.summary || report.ai.error || 'No draft returned.', 'evidence-text'));
      if (report.ai.status === 'truncated') area.append(node('p', 'AI draft reached its output limit and is incomplete. Read collected sources for the full evidence.', 'small'));
      for (const id of report.ai.source_ids || report.source_ids || []) sourceLink(area, caseId, id, 'AI evidence reference');
      if (report.ai.error) area.append(node('p', report.ai.error, 'small'));
      area.append(node('p', `${report.ai.tool_calls?.length || 0} local AI tool calls. Model output may be wrong.`, 'small'));
    }
    if (!report.source_ids?.length && !running) area.append(node('p', 'No public evidence collected. Inspect tool errors, edit the web query or add public context, then retry.'));
  }
  updateRunControls();
  if (running) runTimer = setTimeout(async () => {
    try {
      if (currentCase !== caseId) return;
      const next = await api('/api/investigation?' + new URLSearchParams({case: caseId}));
      if (currentCase !== caseId) return;
      renderRun(next, caseId);
      if (next?.status !== 'running') { await refresh(); notice('Investigation ' + next.status + '. Evidence retained locally.'); }
    } catch (error) { notice(error.message); }
  }, 1200);
}
async function refreshRun(caseId, loadOptions = false) {
  const report = await api('/api/investigation?' + new URLSearchParams({case: caseId}));
  if (currentCase === caseId) { if (loadOptions && report?.options) setOptions(report.options); renderRun(report, caseId); }
}
async function refreshAI() {
  clearTimeout(aiTimer);
  const ai = await api('/api/ai');
  aiReady = ai.state === 'ready';
  $('ai-status').textContent = 'Local AI: ' + ai.state + (ai.error ? ' · ' + ai.error : '');
  $('use-ai').disabled = ai.state !== 'ready';
  if (ai.state !== 'ready') $('use-ai').checked = false;
  $('start-ai').disabled = ['ready', 'loading'].includes(ai.state);
  $('stop-ai').hidden = !['ready', 'loading'].includes(ai.state);
  updateRunControls();
  if (ai.state === 'loading' || displayedRun?.status === 'running' && displayedRun?.continuation) aiTimer = setTimeout(() => refreshAI().catch(error => notice(error.message)), 1500);
}
let toolCase = '';
function toolField(area, label, id, value = '', attributes = {}) {
  const text = node('label', label); text.htmlFor = id;
  const input = node('input'); input.id = id; input.value = value;
  for (const [name, setting] of Object.entries(attributes)) input[name] = setting;
  area.append(text, input); return input;
}
function renderToolFields(tool, args = {}) {
  const area = $('tool-fields'); area.replaceChildren();
  if (['web_search', 'search_subject', 'news_search'].includes(tool)) {
    const defaultQuery = $('investigation-query').value.trim() || ('"' + currentSubject + '"');
    toolField(area, 'Search query', 'tool-query', args.query ?? defaultQuery, {maxLength: 200, required: true});
    if (tool === 'search_subject') {
      const label = node('label', 'Public profile provider'); label.htmlFor = 'tool-provider';
      const select = node('select'); select.id = 'tool-provider';
      for (const provider of ['wikipedia', 'wikidata']) { const option = node('option', provider === 'wikipedia' ? 'Wikipedia (English)' : 'Wikidata'); option.value = provider; select.append(option); }
      select.value = args.provider || 'wikipedia'; area.append(label, select);
    } else if (tool === 'web_search') {
      const label = node('label', 'Public web search provider'); label.htmlFor = 'tool-web-provider';
      const select = node('select'); select.id = 'tool-web-provider';
      for (const provider of searchProviders) { const option = node('option', provider.label); option.value = provider.id; select.append(option); }
      select.value = args.provider || [...document.querySelectorAll('[name="search-provider"]:checked')][0]?.value || searchProviders[0]?.id;
      area.append(label, select);
    }
  } else if (tool === 'collect_page') {
    toolField(area, 'Public HTTPS URL', 'tool-url', args.url || '', {type: 'url', maxLength: 2000, required: true, placeholder: 'https://example.org/page'});
  } else if (tool === 'domain_signals') {
    toolField(area, 'Public domain', 'tool-domain', args.domain || '', {maxLength: 253, required: true, placeholder: 'example.org'});
    const signals = node('fieldset'); signals.append(node('legend', 'Signals to run'));
    for (const [value, label] of [['dns', 'DNS addresses'], ['registration', 'Domain registration (RDAP)'], ['certificate', 'TLS certificate'], ['website', 'Public website']]) {
      const item = node('label'), input = node('input'); input.type = 'checkbox'; input.value = value; input.dataset.toolSignal = value; input.checked = (args.signals || ['dns', 'registration', 'certificate', 'website']).includes(value); item.append(input, document.createTextNode(label)); signals.append(item);
    }
    area.append(signals);
  } else if (tool === 'read_evidence') {
    toolField(area, 'Saved evidence ID', 'tool-evidence', args.id || '', {maxLength: 200, required: true});
    area.append(node('p', 'Reads an existing source in this case. It does not send evidence online.', 'small'));
  } else if (tool === 'analyze') {
    area.append(node('p', 'Ask the local model to compare this case’s known identifiers and private context with collected public evidence. Private clues stay local; searches use the approved public query. Its tool calls and cited draft appear in the activity and dossier.', 'small'));
  }
  $('tool-privacy-note').textContent = ['analyze', 'read_evidence'].includes(tool) ? 'Evidence stays on this machine. Start local AI first for analysis.' : 'This sends your chosen query, URL or domain to public sources. Evidence and processing stay local.';
  updateToolSubmit();
}
function updateToolSubmit() {
  const tool = $('tool-kind').value, local = ['analyze', 'read_evidence'].includes(tool);
  $('run-tool').disabled = displayedRun?.status === 'running' || toolCase !== currentCase || (!local && !status.network) || (tool === 'analyze' && !aiReady);
}
function openTool(caseId, tool = 'web_search', args = {}) {
  if (caseId !== currentCase || displayedRun?.status === 'running') return;
  toolCase = caseId; $('tool-kind').value = tool;
  $('tool-dialog-title').textContent = Object.keys(args).length ? 'Inspect, edit & run again' : 'Run a tool';
  $('tool-edit-note').textContent = 'Runs only this tool. Earlier source evidence and activity remain saved.';
  renderToolFields(tool, args);
  if (!$('tool-dialog').open) $('tool-dialog').showModal();
  ($('tool-fields').querySelector('input, select') || $('tool-kind')).focus();
}
function toolArguments(tool) {
  if (['web_search', 'search_subject', 'news_search'].includes(tool)) return {query: $('tool-query').value.trim(), ...(tool === 'search_subject' ? {provider: $('tool-provider').value} : tool === 'web_search' ? {provider: $('tool-web-provider').value} : {})};
  if (tool === 'collect_page') return {url: $('tool-url').value.trim()};
  if (tool === 'domain_signals') {
    const signals = [...document.querySelectorAll('[data-tool-signal]:checked')].map(input => input.value);
    if (!signals.length) throw new Error('Select at least one domain signal.');
    return {domain: $('tool-domain').value.trim(), signals};
  }
  if (tool === 'read_evidence') return {id: $('tool-evidence').value.trim()};
  return {};
}
action($('new-tool'), () => openTool(currentCase));
action($('reset-prompts'), () => { setRecoveryOptions(); notice('Recovery defaults restored in this form. They apply to the next run or continuation.'); });
action($('retry-ai'), () => openTool(currentCase, 'analyze'));
action($('close-tool'), () => $('tool-dialog').close());
action($('tool-kind'), () => renderToolFields($('tool-kind').value), 'change');
action($('tool-form'), async () => {
  if (toolCase !== currentCase) throw new Error('This dialog belongs to another case. Close it and select the current case tool.');
  const caseId = toolCase, tool = $('tool-kind').value;
  const report = await api('/api/tool', {case: caseId, tool, arguments: toolArguments(tool), ai: false});
  $('tool-dialog').close();
  if (currentCase === caseId) { renderRun(report, caseId); notice('Tool started. Follow its activity below.'); }
}, 'submit');
action($('run-investigation'), () => startInvestigation(currentCase));
action($('continue-investigation'), () => continueInvestigation(currentCase));
action($('stop-investigation'), async () => {
  const report = displayedRun;
  if (!report) return;
  await api('/api/stop', {case: report.case_id, run_id: report.run_id});
  notice('Stopping after current request. Collected sources remain saved.');
});
action($('start-ai'), async () => { await api('/api/ai/start', {}); await refreshAI(); });
action($('stop-ai'), async () => { await api('/api/ai/stop', {}); await refreshAI(); });

let connectors = [], researchTimer, researchCase = '', connectorCase = '', selectedConnector = null;
let researchLoading = false, sourceSettingsDirty = false, researchSignature = '', searchServiceLoaded = false, relationshipWorkspace = null, connectorOpenVersion = 0;
const connectorFallbackFields = {
  crossref: ['query'], github_public: ['username'], public_profile: ['username', 'site'],
  website_sources: ['url'], wayback: ['url'], email_headers: ['evidence_id'],
  public_place: ['query'], searxng: ['query']
};
const sourceFieldLabels = {query: 'Approved public query', username: 'Approved public username', site: 'Public profile site', url: 'Public HTTPS URL', evidence_id: 'Saved evidence ID'};
function connectorReady(connector) {
  if (typeof connector.availability === 'boolean') return connector.availability;
  return connector.available !== false && connector.availability?.ready !== false && connector.ready !== false;
}
function connectorReason(connector) {
  return connector.availability?.reason || connector.availability_message || connector.reason || '';
}
function connectorOnline(connector) { return connector.network !== undefined ? connector.network !== false : (connector.outbound_fields || connector.domains || ['public']).length > 0; }
function connectorFields(connector) {
  const fields = connector.inputs || connector.input_kinds || connectorFallbackFields[connector.id] || ['query'];
  return (Array.isArray(fields) ? fields : Object.entries(fields).map(([name, field]) => typeof field === 'object' ? {name, ...field} : {name, type: field})).map(field => typeof field === 'string' ? {name: field} : {...field, name: field.name || field.id});
}
function lineValues(id) { return $(id).value.split('\n').map(value => value.trim()).filter(Boolean); }
function sourceSettings() {
  if (!$('keep-working').checked && (!$('runtime-seconds').reportValidity() || !Number.isSafeInteger(Number($('runtime-seconds').value)))) throw new Error('Use a positive whole number for collection time.');
  return {packs: [...document.querySelectorAll('[name="source-pack"]:checked')].map(input => input.value), public_usernames: lineValues('public-usernames'), seed_urls: lineValues('seed-urls'), runtime_seconds: $('keep-working').checked ? 0 : Number($('runtime-seconds').value), keep_working: $('keep-working').checked};
}
function setSourceSettings(settings = {}) {
  for (const input of document.querySelectorAll('[name="source-pack"]')) input.checked = (settings.packs || []).includes(input.value);
  $('public-usernames').value = (settings.public_usernames || []).join('\n');
  $('seed-urls').value = (settings.seed_urls || []).join('\n');
  $('runtime-seconds').value = settings.runtime_seconds > 0 ? settings.runtime_seconds : 300;
  $('keep-working').checked = settings.keep_working === true;
  $('runtime-seconds').disabled = $('keep-working').checked;
  sourceSettingsDirty = false;
}
async function saveSourceSettings(caseId = currentCase) {
  if (!caseId || caseId !== currentCase) throw new Error('Select a case first.');
  await api('/api/workspace/settings', {case: caseId, settings: sourceSettings()});
  if (currentCase === caseId) sourceSettingsDirty = false;
}
async function loadConnectors() {
  const result = await api('/api/connectors');
  connectors = result.connectors || [];
  const selectedPacks = [...document.querySelectorAll('[name="source-pack"]:checked')].map(input => input.value);
  const hadPackOptions = $('source-packs').childElementCount > 0;
  const packs = $('source-packs'); packs.replaceChildren();
  const catalogue = $('connector-catalogue'); catalogue.replaceChildren();
  for (const connector of connectors) {
    const enabled = connectorReady(connector), online = connectorOnline(connector);
    const label = node('label'), input = node('input'); input.type = 'checkbox'; input.name = 'source-pack'; input.value = connector.id;
    input.checked = hadPackOptions && selectedPacks.includes(connector.id);
    label.append(input, node('span', connector.label + (!enabled ? ' · unavailable' : ''))); packs.append(label);
    const card = node('article', undefined, 'tool-step connector-card');
    const heading = node('div', undefined, 'step-heading'); heading.append(node('h4', connector.label), node('span', enabled ? online ? 'PUBLIC SOURCE' : 'LOCAL TOOL' : 'UNAVAILABLE', 'step-status')); card.append(heading);
    card.append(node('p', connector.description || '', 'small'));
    const targets = connector.targets || connector.target_types;
    if (targets?.length) card.append(node('p', 'Targets: ' + targets.join(', '), 'small muted'));
    if (connector.domains?.length) card.append(node('p', 'Destinations: ' + connector.domains.join(', '), 'small muted'));
    if (connector.license) card.append(node('p', 'Licence: ' + connector.license, 'small muted'));
    if (connectorReason(connector)) card.append(node('p', connectorReason(connector), enabled ? 'small' : 'step-error'));
    const run = node('button', 'Configure & run', 'secondary'); run.type = 'button'; run.dataset.connector = connector.id;
    run.disabled = !enabled || online && !status.network;
    action(run, () => openConnector(connector)); card.append(run); catalogue.append(card);
  }
  if (!connectors.length) catalogue.append(node('p', 'No source tools are installed.', 'small'));
  renderSearchService(result.search_service || {});
}
function renderSearchService(service) {
  const state = service.state || 'stopped';
  $('search-service-status').textContent = 'Local metasearch: ' + state + (service.error || service.reason || service.message ? ' · ' + (service.error || service.reason || service.message) : '') + (service.upstream_fanout ? ` · up to ${service.upstream_fanout} external engines per search` : '');
  if (!searchServiceLoaded) {
    $('search-service-image').value = service.image || '';
    if (service.engines) for (const input of document.querySelectorAll('[name="metasearch-engine"]')) input.checked = service.engines.includes(input.value);
    searchServiceLoaded = true;
  }
  $('start-search-service').disabled = ['ready', 'running', 'loading', 'starting'].includes(state);
  $('stop-search-service').hidden = !['ready', 'running', 'loading', 'starting'].includes(state);
}
function researchAction(area, label, callback) {
  const button = node('button', label, 'secondary'); button.type = 'button'; action(button, callback); area.append(button); return button;
}
function renderResearch(workspace, caseId) {
  const tasks = workspace.tasks || [], counts = workspace.counts || {};
  const pending = tasks.filter(task => ['pending', 'planned', 'queued', 'running', 'cooldown', 'interrupted', 'interrupted_after_dispatch', 'blocked'].includes(task.state || task.status));
  const running = tasks.filter(task => (task.state || task.status) === 'running').length;
  $('workspace-progress').textContent = `${tasks.length} saved actions · ${pending.length} awaiting work · ${running} running`;
  $('resume-workspace').disabled = !pending.length || running > 0 || displayedRun?.status === 'running';
  const metrics = $('research-counts'); metrics.replaceChildren();
  for (const [key, label] of [['leads', 'discovery leads'], ['readable_documents', 'readable documents'], ['corroborated_claims', 'corroborated claims']]) {
    const metric = node('div'); metric.append(node('strong', String(counts[key] || 0)), node('span', label)); metrics.append(metric);
  }
  const expandedAttempts = new Set([...document.querySelectorAll('[data-task-attempts]')].filter(details => details.open).map(details => details.dataset.taskAttempts));
  const queue = $('research-tasks'), queueScroll = queue.scrollTop; queue.replaceChildren();
  if (!tasks.length) queue.append(node('p', 'No queued actions yet. Run an investigation or configure a source above.', 'small muted'));
  for (const task of tasks) {
    const state = task.state || task.status || 'planned', card = node('article', undefined, 'tool-step queue-task');
    const heading = node('div', undefined, 'step-heading');
    heading.append(node('h4', connectors.find(connector => connector.id === task.connector)?.label || task.connector || 'Research action'), node('span', state.replaceAll('_', ' '), 'step-status')); card.append(heading);
    if (task.reason) card.append(node('p', task.reason, 'small'));
    card.append(argumentList(task.arguments));
    const attempts = Array.isArray(task.attempts) ? task.attempts.length : task.attempts || 0;
    const retryAt = task.not_before || task.retry_at;
    card.append(node('p', `Priority ${task.priority ?? 0} · ${attempts} attempts` + (retryAt ? ' · eligible after ' + new Date(retryAt).toLocaleString() : ''), 'small muted'));
    if (task.error) card.append(node('p', String(task.error), 'step-error'));
    if (task.result) card.append(node('p', typeof task.result === 'string' ? task.result : task.result.summary || task.result.status || 'Result saved with this action.', 'small'));
    const sources = [...new Set([...(task.evidence_ids || []), ...(task.result?.evidence_ids || [])])];
    for (const id of sources) sourceLink(card, caseId, id, 'Action evidence');
    const history = task.attempt_history || (Array.isArray(task.attempts) ? task.attempts : []);
    if (history.length) {
      const details = node('details'); details.dataset.taskAttempts = task.id; details.open = expandedAttempts.has(task.id); details.append(node('summary', 'Inspect previous attempts'));
      for (const attempt of history) {
        const item = node('div', undefined, 'attempt-entry');
        item.append(node('p', [attempt.state || attempt.status, attempt.started_at, attempt.finished_at ? 'finished ' + attempt.finished_at : ''].filter(Boolean).join(' · '), 'small'));
        if (attempt.error) item.append(node('p', attempt.error, 'step-error'));
        if (attempt.result) item.append(node('pre', typeof attempt.result === 'string' ? attempt.result : JSON.stringify(attempt.result, null, 2), 'evidence-text'));
        details.append(item);
      }
      card.append(details);
    }
    const controls = node('div', undefined, 'tool-actions');
    const changeTask = async (operation, extra = {}) => {
      await api('/api/task', {case: caseId, id: task.id, operation, ...extra}); await refreshResearch(caseId);
    };
    if (state !== 'running') {
      researchAction(controls, 'Retry', () => changeTask('retry'));
      const connector = connectors.find(item => item.id === task.connector);
      if (connector) researchAction(controls, 'Edit & run', () => openConnector(connector, task.arguments || {}));
      if (!['finished', 'complete', 'completed', 'success', 'skipped'].includes(state)) researchAction(controls, 'Skip', () => changeTask('skip'));
      researchAction(controls, 'Prioritize', () => changeTask('priority', {priority: Number(task.priority || 0) + 10}));
    }
    card.append(controls); queue.append(card);
  }
  queue.scrollTop = queueScroll;
  const candidates = $('research-candidates'); candidates.replaceChildren();
  for (const candidate of workspace.candidates || []) {
    const card = node('article', undefined, 'tool-step'); card.append(node('p', 'CANDIDATE · ' + (candidate.status || 'UNVERIFIED'), 'eyebrow'), node('h3', candidate.label || candidate.name || candidate.id));
    if (candidate.context) card.append(node('p', typeof candidate.context === 'string' ? candidate.context : JSON.stringify(candidate.context), 'small'));
    for (const id of [...new Set([...(candidate.evidence_ids || []), candidate.source_id].filter(Boolean))]) sourceLink(card, caseId, id, 'Candidate evidence');
    candidates.append(card);
  }
  if (!workspace.candidates?.length) candidates.append(node('p', 'No candidate identities derived yet.', 'small muted'));
  const claims = $('research-claims'); claims.replaceChildren();
  for (const claim of workspace.claims || []) {
    const card = node('article', undefined, 'tool-step claim-card');
    const candidate = workspace.candidates?.find(item => item.id === claim.candidate_id);
    card.append(node('p', [candidate?.label || claim.candidate_id, claim.status || 'unverified'].filter(Boolean).join(' · '), 'eyebrow'), node('h4', [claim.predicate, typeof claim.value === 'string' ? claim.value : JSON.stringify(claim.value)].filter(Boolean).join(': ')));
    if (claim.quote) card.append(node('blockquote', claim.quote, 'source-quote'));
    for (const reference of claim.references || claim.refs || []) {
      card.append(node('blockquote', reference.quote || '', 'source-quote'), node('p', [reference.relation || 'support', reference.extractor_version || '', reference.extraction_id ? 'Extraction ' + reference.extraction_id : '', Number.isInteger(reference.start) ? `characters ${reference.start}–${reference.end}` : ''].filter(Boolean).join(' · '), 'small muted'));
      sourceLink(card, caseId, reference.evidence_id, 'Inspect cited evidence');
    }
    if (claim.extraction_version || claim.start !== undefined) card.append(node('p', ['Extraction ' + (claim.extraction_version || 'saved'), claim.start !== undefined ? `characters ${claim.start}–${claim.end}` : ''].filter(Boolean).join(' · '), 'small muted'));
    sourceLink(card, caseId, claim.evidence_id || claim.source_id, 'Inspect supporting passage'); claims.append(card);
  }
  if (!workspace.claims?.length) claims.append(node('p', 'No sourced observations yet. Search snippets remain discovery leads.', 'small muted'));
  relationshipWorkspace = {workspace, caseId};
  const candidateFilter = $('relationship-candidate'), previousFilter = candidateFilter.value;
  const allCandidates = node('option', 'All candidates'); allCandidates.value = ''; candidateFilter.replaceChildren(allCandidates);
  for (const candidate of workspace.candidates || []) {
    const option = node('option', candidate.label || candidate.id); option.value = candidate.id; candidateFilter.append(option);
  }
  candidateFilter.value = (workspace.candidates || []).some(candidate => candidate.id === previousFilter) ? previousFilter : '';
  renderRelationships(workspace, caseId);
}
function renderRelationships(workspace, caseId) {
  const selected = $('relationship-candidate').value;
  const claims = (workspace.claims || []).filter(claim => !selected || claim.candidate_id === selected);
  const edges = claims.flatMap(claim => (claim.references || claim.refs || []).map(reference => ({claim, reference})));
  const area = $('evidence-relationships'); area.replaceChildren();
  if (!edges.length) area.append(node('p', 'No source-linked relationships for this selection yet.', 'small muted'));
  else {
    const table = node('table', undefined, 'relationship-table');
    table.append(node('caption', 'Candidate → observation → original evidence. These links describe source provenance, not confirmed identity or ownership.'));
    const head = node('thead'), heading = node('tr');
    for (const label of ['Candidate', 'Observation', 'Evidence relationship', 'Original evidence']) { const cell = node('th', label); cell.scope = 'col'; heading.append(cell); }
    head.append(heading); table.append(head);
    const body = node('tbody');
    for (const {claim, reference} of edges.slice(0, 80)) {
      const row = node('tr'), candidate = workspace.candidates?.find(item => item.id === claim.candidate_id);
      row.append(node('td', candidate?.label || 'Unassigned candidate'), node('td', [claim.predicate, claim.value, '(' + (claim.status || 'unconfirmed') + ')'].filter(Boolean).join(' · ')), node('td', reference.relation === 'contradict' ? 'Contradicts observation' : 'Supports observation'));
      const evidence = node('td');
      if (reference.quote) evidence.append(node('blockquote', reference.quote, 'source-quote'));
      if (reference.evidence_id) researchAction(evidence, 'Inspect original evidence', () => showEvidence(caseId, reference.evidence_id));
      row.append(evidence); body.append(row);
    }
    table.append(body); area.append(table);
    if (edges.length > 80) area.append(node('p', `Showing 80 of ${edges.length} evidence relationships. Select a candidate to narrow the view; exports retain all references.`, 'small muted'));
  }
  const timeline = $('observation-timeline'); timeline.replaceChildren();
  const ordered = [...claims].sort((left, right) => String(left.created_at || '').localeCompare(String(right.created_at || '')) || String(left.id).localeCompare(String(right.id)));
  if (!ordered.length) { timeline.append(node('p', 'No recorded observations for this selection.', 'small muted')); return; }
  const list = node('ol', undefined, 'observation-timeline');
  for (const claim of ordered.slice(-80)) {
    const item = node('li'), candidate = workspace.candidates?.find(value => value.id === claim.candidate_id);
    const time = node('time', claim.created_at ? new Date(claim.created_at).toLocaleString() : 'Recorded time unavailable');
    if (claim.created_at) time.dateTime = claim.created_at;
    item.append(time, node('p', [candidate?.label || 'Unassigned candidate', claim.predicate, claim.value, claim.status || 'unconfirmed'].filter(Boolean).join(' · ')));
    for (const reference of (claim.references || claim.refs || []).slice(0, 4)) if (reference.evidence_id) researchAction(item, reference.relation === 'contradict' ? 'Contradicting evidence' : 'Supporting evidence', () => showEvidence(caseId, reference.evidence_id));
    list.append(item);
  }
  timeline.append(list);
  if (ordered.length > 80) timeline.append(node('p', `Showing the latest 80 of ${ordered.length} recorded observations. Select a candidate to narrow the view; exports retain all observations.`, 'small muted'));
}
async function refreshResearch(caseId = currentCase, loadSettings = false) {
  clearTimeout(researchTimer);
  if (!caseId || currentCase !== caseId) return;
  if (researchLoading) { researchTimer = setTimeout(() => refreshResearch(currentCase).catch(error => notice(error.message)), 1500); return; }
  researchLoading = true;
  let refreshEvidence = false;
  try {
    const workspace = await api('/api/workspace?' + new URLSearchParams({case: caseId}));
    if (currentCase !== caseId) return;
    const signature = JSON.stringify((workspace.tasks || []).map(task => [task.id, task.state || task.status, task.attempts, task.updated_at]));
    refreshEvidence = researchCase === caseId && researchSignature !== signature && !!researchSignature;
    researchSignature = signature;
    if (loadSettings || researchCase !== caseId) { setSourceSettings(workspace.settings || {}); researchCase = caseId; }
    renderResearch(workspace, caseId);
    if (workspace.search_service) renderSearchService(workspace.search_service);
  } finally {
    researchLoading = false;
    if (!pageLeaving && currentCase) researchTimer = setTimeout(() => refreshResearch(currentCase).catch(error => notice(error.message)), 2500);
  }
  if (refreshEvidence && currentCase === caseId) await refresh();
}
async function openConnector(connector, args = {}) {
  if (!currentCase) return;
  const caseId = currentCase, openVersion = ++connectorOpenVersion;
  selectedConnector = connector; connectorCase = currentCase;
  $('connector-dialog-title').textContent = connector.label;
  $('connector-description').textContent = connector.description || '';
  const fields = $('connector-fields'); fields.replaceChildren();
  let savedEvidenceSelect;
  for (const field of connectorFields(connector)) {
    if (!field.name) continue;
    const savedEmail = connector.id === 'email_headers' && field.name === 'evidence_id';
    const label = node('label', savedEmail ? 'Saved email or header evidence' : field.label || sourceFieldLabels[field.name] || field.name); label.htmlFor = 'connector-' + field.name;
    const options = field.options || (field.name === 'site' ? connector.sites : null);
    const input = node(options || savedEmail ? 'select' : 'input'); input.id = label.htmlFor; input.dataset.connectorField = field.name;
    if (savedEmail) {
      const waiting = node('option', 'Loading saved evidence…'); waiting.value = ''; input.append(waiting); input.disabled = true; savedEvidenceSelect = input;
    } else if (options) for (const choice of options) { const option = node('option', typeof choice === 'string' ? choice : choice.label || choice.id); option.value = typeof choice === 'string' ? choice : choice.id || choice.value; input.append(option); }
    else { input.type = field.name === 'url' ? 'url' : field.type === 'number' ? 'number' : 'text'; input.maxLength = field.name === 'url' ? 2000 : 500; }
    const initial = args[field.name] ?? field.default ?? (field.name === 'query' ? $('investigation-query').value : field.name === 'username' ? lineValues('public-usernames')[0] || '' : field.name === 'url' ? lineValues('seed-urls')[0] || '' : '');
    if (initial !== '') input.value = String(initial);
    input.required = field.required !== false; fields.append(label, input);
    if (field.description) fields.append(node('p', field.description, 'small muted'));
  }
  $('connector-disclosure').textContent = !connectorOnline(connector) ? 'Processes saved evidence on this machine. No external request.' : 'Only the inputs shown here are sent to this source. Saved private identifiers are not included. A successful lookup does not establish identity.';
  $('run-connector').disabled = !!savedEvidenceSelect || !connectorReady(connector) || connectorOnline(connector) && !status.network;
  if (!$('connector-dialog').open) $('connector-dialog').showModal();
  fields.querySelector('input, select')?.focus();
  if (savedEvidenceSelect) {
    const active = () => currentCase === caseId && connectorCase === caseId && connectorOpenVersion === openVersion && $('connector-dialog').open;
    try {
      const view = await api('/api/view?' + new URLSearchParams({case: caseId}));
      if (!active()) return;
      const evidence = (view.evidence || []).filter(item => !String(item.source || '').startsWith('local-osint:'));
      if (args.evidence_id && !evidence.some(item => item.id === args.evidence_id)) {
        const previous = await api('/api/evidence?' + new URLSearchParams({case: caseId, id: args.evidence_id}));
        if (!active()) return;
        if (!String(previous.source || '').startsWith('local-osint:')) evidence.push(previous);
      }
      const preference = item => /\.eml(?:$|[?#])/i.test(item.title || '') || /\.eml(?:$|[?#])/i.test(item.source || '') ? 0 : item.format === 'text' ? 1 : 2;
      evidence.sort((left, right) => preference(left) - preference(right));
      const placeholder = node('option', evidence.length ? 'Choose saved email or header evidence' : 'No saved evidence available'); placeholder.value = '';
      savedEvidenceSelect.replaceChildren(placeholder);
      for (const item of evidence) { const option = node('option', [item.title || 'Untitled source', item.source || item.format].filter(Boolean).join(' · ')); option.value = item.id; savedEvidenceSelect.append(option); }
      savedEvidenceSelect.value = evidence.some(item => item.id === args.evidence_id) ? args.evidence_id : '';
      savedEvidenceSelect.disabled = !evidence.length;
      $('run-connector').disabled = !evidence.length || !connectorReady(connector);
      fields.append(node('p', evidence.length ? 'Email (.eml) and plain-text originals appear first. Select a source by title; processing stays local and original bytes remain intact.' : 'Import an email (.eml) or paste its headers under Import a file or paste notes, then reopen this tool. Original message bytes stay on this machine.', 'small muted'));
      if (view.evidence?.length === view.limits?.evidence) fields.append(node('p', 'The newest 200 saved sources are listed. An earlier source remains selected when you edit one of its saved actions.', 'small muted'));
      if (evidence.length) savedEvidenceSelect.focus();
    } catch (error) {
      if (!active()) return;
      savedEvidenceSelect.replaceChildren(node('option', 'Saved evidence could not be loaded'));
      fields.append(node('p', error.message || 'Close this tool and retry loading saved evidence.', 'step-error'));
      $('run-connector').disabled = true;
    }
  }
}
action($('source-settings-form'), async () => { await saveSourceSettings(); notice('Source settings saved for this case.'); }, 'submit');
$('source-settings-form').addEventListener('input', () => { sourceSettingsDirty = true; });
$('keep-working').addEventListener('change', () => { $('runtime-seconds').disabled = $('keep-working').checked; });
action($('resume-workspace'), async () => { await saveSourceSettings(); const caseId = currentCase; await api('/api/resume', {case: caseId}); await refreshResearch(caseId); await refreshRun(caseId); notice('Saved research resumed. Watch the action queue for outcomes.'); });
action($('start-search-service'), async () => {
  const engines = [...document.querySelectorAll('[name="metasearch-engine"]:checked')].map(input => input.value);
  if (!engines.length) throw new Error('Select at least one metasearch engine.');
  const image = $('search-service-image').value.trim();
  $('search-service-status').textContent = 'Preparing owned metasearch. The first image download can take a few minutes…';
  const service = await api('/api/search-service/start', {settings: {engines, ...(image ? {image} : {})}});
  renderSearchService(service); await loadConnectors(); notice('Metasearch setup requested. Service status shows availability and setup errors.');
});
action($('stop-search-service'), async () => { renderSearchService(await api('/api/search-service/stop', {})); await loadConnectors(); });
action($('close-connector'), () => $('connector-dialog').close());
action($('relationship-candidate'), () => {
  if (relationshipWorkspace?.caseId === currentCase) renderRelationships(relationshipWorkspace.workspace, currentCase);
}, 'change');
action($('connector-form'), async () => {
  if (connectorCase !== currentCase || !selectedConnector) throw new Error('Select the source again for the current case.');
  const args = {};
  for (const input of document.querySelectorAll('[data-connector-field]')) args[input.dataset.connectorField] = input.type === 'number' ? Number(input.value) : input.value.trim();
  const caseId = currentCase;
  if (sourceSettingsDirty) await saveSourceSettings(caseId);
  await api('/api/action', {case: caseId, connector: selectedConnector.id, arguments: args});
  $('connector-dialog').close(); await refreshResearch(caseId); await refreshRun(caseId); notice('Source action queued. Its inputs, attempts and evidence are saved.');
}, 'submit');
