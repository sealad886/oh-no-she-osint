'use strict';
const $ = id => document.getElementById(id);
let currentCase = '', currentSubject = '', optionsCase = '', status = {}, requestVersion = 0, aiReady = false;

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
    $('mode').textContent = status.network ? 'LOCAL PROCESSING' : 'OFFLINE MODE';
    $('create-investigation').textContent = status.network ? 'Start investigation ↗' : 'Create offline case';
    $('start-notice').hidden = !status.network;
    $('run-investigation').disabled = !status.network;
    await refreshAI();
    $('workspace').hidden = false;
    await loadCases();
  } catch (error) { $('locked').hidden = false; notice(error.message); }
})();

let displayedRun = null, runTimer, aiTimer;
function setOptions(options = {}) {
  $('investigation-query').value = options.query || ('"' + currentSubject + '"');
  $('investigation-context').value = options.context || '';
  $('request-limit').value = options.max_requests || 24;
  $('page-limit').value = options.page_limit || 4;
  const tools = options.tools || ['web_search', 'wikipedia', 'wikidata', 'news', 'domain'];
  for (const input of document.querySelectorAll('[name="investigation-tool"]')) input.checked = tools.includes(input.value);
}
function getOptions() {
  const tools = [...document.querySelectorAll('[name="investigation-tool"]:checked')].map(input => input.value);
  if (!tools.length) throw new Error('Select at least one investigation tool.');
  for (const id of ['request-limit', 'page-limit']) if (!$(id).reportValidity()) throw new Error('Check the investigation limits.');
  if (['request-limit', 'page-limit'].some(id => !Number.isSafeInteger(Number($(id).value)))) throw new Error('Use whole numbers within the browser’s exact integer range for investigation limits.');
  return {query: $('investigation-query').value.trim(), context: $('investigation-context').value.trim(), tools, max_requests: Number($('request-limit').value), page_limit: Number($('page-limit').value)};
}
function updateRunControls() {
  const busy = displayedRun?.status === 'running';
  $('run-investigation').disabled = busy || !status.network || !currentCase;
  $('stop-investigation').hidden = !busy;
  $('investigation-options').disabled = busy || !status.network;
  $('new-tool').disabled = busy || !currentCase;
  $('retry-ai').disabled = busy || !currentCase || !aiReady;
  for (const button of document.querySelectorAll('[data-tool-action]')) button.disabled = busy || !currentCase || (!status.network && button.dataset.toolAction !== 'read_evidence') || (button.dataset.toolAction === 'analyze' && !aiReady);
  if ($('tool-dialog').open) updateToolSubmit();
}
async function startInvestigation(caseId) {
  notice('Investigation started. Sources and progress appear below.');
  const report = await api('/api/investigate', {case: caseId, ai: $('use-ai').checked, options: getOptions()});
  if (currentCase === caseId) renderRun(report, caseId);
}
function sourceLink(area, caseId, id, label = 'View source') {
  if (id) area.append(resultButton('source', label, 'Open ↗', () => showEvidence(caseId, id)));
}
const toolLabels = {web_search: 'Public web search', search_subject: 'Public profile search', wikipedia: 'Wikipedia candidates', wikidata: 'Wikidata candidates', news_search: 'Recent news search', news: 'Recent news search', collect_page: 'Public page collection', domain_signals: 'Domain signals', dns: 'DNS addresses', registration: 'Domain registration', certificate: 'TLS certificate', website: 'Public website', read_evidence: 'Read saved evidence', analyze: 'Local AI analysis', local_ai: 'Local AI request', ai_request: 'Local AI request'};
const argumentLabels = {query: 'Query', context: 'Public context', url: 'URL', domain: 'Domain', provider: 'Provider', signals: 'Signals', id: 'Evidence ID'};
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
    if (Object.keys(args).length) {
      const argumentsList = node('dl', undefined, 'tool-arguments');
      for (const [key, value] of Object.entries(args)) {
        if (value === undefined || value === null || value === '') continue;
        argumentsList.append(node('dt', argumentLabels[key] || key), node('dd', Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value)));
      }
      card.append(argumentsList);
    }
    if (step.result_summary) card.append(node('p', step.result_summary, 'step-result'));
    if (step.error) card.append(node('p', step.error, 'step-error'));
    sourceLink(card, caseId, step.source_id, 'Saved tool evidence');
    if (editable && stepStatus !== 'running') addToolButton(card, stepStatus === 'failed' ? 'Edit & retry' : 'Edit & run again', caseId, editable.tool, editable.args);
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
  if (ai.state === 'loading') aiTimer = setTimeout(() => refreshAI().catch(error => notice(error.message)), 1500);
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
  if (['web_search', 'search_subject', 'news_search'].includes(tool)) return {query: $('tool-query').value.trim(), ...(tool === 'search_subject' ? {provider: $('tool-provider').value} : {})};
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
action($('stop-investigation'), async () => {
  const report = displayedRun;
  if (!report) return;
  await api('/api/stop', {case: report.case_id, run_id: report.run_id});
  notice('Stopping after current request. Collected sources remain saved.');
});
action($('start-ai'), async () => { await api('/api/ai/start', {}); await refreshAI(); });
action($('stop-ai'), async () => { await api('/api/ai/stop', {}); await refreshAI(); });
