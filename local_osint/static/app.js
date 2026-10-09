'use strict';
const $ = id => document.getElementById(id);
let currentCase = '', status = {}, requestVersion = 0;

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
    finally { if (button) button.disabled = false; }
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
  if (!$('discovery-query').dataset.case || $('discovery-query').dataset.case !== caseId) {
    $('discovery-query').value = data.case.name; $('discovery-query').dataset.case = caseId;
    $('domain').value = data.case.focus_type === 'domain' ? data.case.name : '';
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
  await refreshRun(caseId);
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
  const area = detail(evidence.title);
  area.append(node('p', 'Source: ' + evidence.source, 'detail-meta'), node('p', 'Collected: ' + evidence.collected_at, 'detail-meta'), node('p', 'SHA-256: ' + evidence.sha256, 'detail-meta'), node('pre', evidence.text, 'evidence-text'), node('p', `${evidence.mentions.length} indicator mentions. Offsets refer to extracted searchable text; original bytes are in the evidence export.`, 'small'));
}
async function showEntity(caseId, id) {
  const entity = await api('/api/entity?' + new URLSearchParams({case: caseId, id}));
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
action($('collect-form'), async () => {
  notice('Collecting one public page…');
  const caseId = currentCase;
  const result = await api('/api/collect', {case: caseId, url: $('url').value});
  if (caseId === currentCase) await refresh();
  notice(result.duplicate ? 'This page is already in your case.' : 'Page collected and analyzed locally.');
}, 'submit');
action($('discovery-form'), async () => {
  const sources = [...$('discovery-form').querySelectorAll('input[type=checkbox]:checked')].map(input => input.value);
  if (!sources.length) throw new Error('Select at least one discovery source.');
  const caseId = currentCase;
  notice('Searching selected public sources…');
  const result = await api('/api/discover', {case: caseId, sources, query: $('discovery-query').value});
  if (caseId === currentCase) await refresh();
  const area = detail('Candidates: ' + result.query);
  area.append(node('p', result.interpretation, 'small'));
  for (const source of result.results) {
    area.append(node('h3', source.source));
    if (!source.ok) { area.append(node('p', source.error)); continue; }
    area.append(resultButton('evidence', 'Open original discovery response', 'Open ↗', () => showEvidence(caseId, source.id)));
    if (!source.candidates.length) area.append(node('p', 'No candidates found. Try an alias or collect a known public URL.'));
    for (const candidate of source.candidates) {
      area.append(node('h3', candidate.label), node('p', candidate.description), node('p', candidate.url, 'detail-meta'));
      const collectButton = node('button', 'Collect candidate page', 'secondary');
      action(collectButton, async () => {
        notice('Collecting candidate page…');
        // Wikipedia page URLs use their stable numeric page ID; use title URLs for collection.
        const url = source.source === 'wikipedia' ? 'https://en.wikipedia.org/wiki/' + encodeURIComponent(candidate.label.replaceAll(' ', '_')) : candidate.url;
        await api('/api/collect', {case: caseId, url});
        if (currentCase === caseId) await refresh();
        notice('Candidate page saved. Identity remains unverified.');
      });
      area.append(collectButton);
    }
  }
  notice('Discovery finished. Assess candidates before drawing conclusions.');
}, 'submit');
action($('research-form'), async () => {
  const signals = [...$('research-form').querySelectorAll('input[type=checkbox]:checked')].map(input => input.value);
  if (!signals.length) throw new Error('Select at least one signal.');
  notice(`Collecting ${signals.length} public signal${signals.length === 1 ? '' : 's'}…`);
  const caseId = currentCase;
  const result = await api('/api/research', {case: caseId, domain: $('domain').value.trim(), signals});
  if (caseId === currentCase) await refresh();
  const area = detail('Collection results: ' + result.domain);
  for (const signal of result.results) {
    area.append(node('h3', signal.signal), node('p', signal.ok ? (signal.duplicate ? 'Already saved in this case.' : 'Saved and analyzed locally.') : signal.error));
    if (signal.ok) area.append(resultButton('source', 'Open collected evidence', 'Open ↗', () => showEvidence(caseId, signal.id)));
  }
  notice(`${result.results.filter(item => item.ok).length} / ${result.results.length} signals collected. See results for individual errors.`);
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
    $('network-note').textContent = status.network ? 'Collection enabled. Only the page you request is fetched.' : 'Collection disabled. Start with --allow-network to collect public signals.';
    $('research-note').textContent = status.network ? 'Choose public signals. Collection goes online; analysis stays here.' : 'Start with --allow-network to enable public signal collection.';
    $('collect-button').disabled = !status.network;
    $('research-button').disabled = !status.network;
    $('discovery-button').disabled = !status.network;
    $('create-investigation').textContent = status.network ? 'Start investigation ↗' : 'Create offline case';
    $('start-notice').hidden = !status.network;
    $('run-investigation').disabled = !status.network;
    await refreshAI();
    $('workspace').hidden = false;
    await loadCases();
  } catch (error) { $('locked').hidden = false; notice(error.message); }
})();

let displayedRun = null, runTimer, aiTimer;
async function startInvestigation(caseId) {
  notice('Investigation started. Sources and progress appear below.');
  const report = await api('/api/investigate', {case: caseId, ai: $('use-ai').checked});
  if (currentCase === caseId) renderRun(report, caseId);
}
function sourceLink(area, caseId, id, label = 'View source') {
  if (id) area.append(resultButton('source', label, 'Open ↗', () => showEvidence(caseId, id)));
}
function renderRun(report, caseId) {
  if (currentCase !== caseId) return;
  clearTimeout(runTimer);
  displayedRun = report;
  const running = report?.status === 'running';
  $('run-investigation').disabled = running || !status.network;
  $('stop-investigation').hidden = !running;
  $('run-progress').textContent = report ? `${report.stage} · ${report.steps.filter(s => s.ok).length} collected steps / ${report.steps.length} attempted · ${report.request_units || 0} of 24 request units` : 'Enter subject and run investigation. No manual evidence required.';
  const area = $('dossier'); area.replaceChildren();
  if (report) {
    area.append(node('p', report.interpretation, 'small'));
    if (report.error) area.append(node('p', report.error));
    for (const profile of report.profiles) {
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
    if (report.articles.length) {
      area.append(node('h3', 'Recent news mentioning this name'));
      for (const article of report.articles) {
        const card = node('article', undefined, 'profile-card');
        card.append(node('h4', article.title), node('p', article.url, 'detail-meta'), node('p', [article.seendate, article.language, article.sourcecountry].filter(Boolean).join(' · '), 'small'));
        sourceLink(card, caseId, article.page_source_id || article.source_id, article.page_source_id ? 'Collected article' : 'News search source');
        area.append(card);
      }
    }
    if (report.domain_lead) area.append(node('p', `Domain lead: ${report.domain_lead.domain} · ${report.domain_lead.basis}`, 'small'));
    if (!report.profiles.length && report.candidates.length) {
      area.append(node('h3', 'Public search leads'));
      for (const candidate of report.candidates.slice(0, 10)) {
        area.append(node('p', candidate.label + ' — ' + candidate.description));
        sourceLink(area, caseId, candidate.discovery_source_id);
      }
    }
    if (report.ai) {
      area.append(node('h3', 'Local AI draft — verify against sources'), node('pre', report.ai.summary || report.ai.error || 'No draft returned.', 'evidence-text'));
      if (report.ai.status === 'truncated') area.append(node('p', 'AI draft reached its output limit and is incomplete. Read collected sources for the full evidence.', 'small'));
      for (const id of report.ai.source_ids || report.source_ids) sourceLink(area, caseId, id, 'AI evidence reference');
      if (report.ai.error) area.append(node('p', report.ai.error, 'small'));
      area.append(node('p', `${report.ai.tool_calls?.length || 0} local AI tool calls. Model output may be wrong.`, 'small'));
    }
    const steps = node('details'); steps.append(node('summary', 'Collection log and unavailable sources'));
    for (const step of report.steps) {
      steps.append(node('p', `${step.ok ? 'Collected' : 'Unavailable'} · ${step.label}${step.error ? ' · ' + step.error : ''}`, 'small'));
      sourceLink(steps, caseId, step.source_id);
    }
    if (!report.source_ids.length && !running) area.append(node('p', 'No public evidence collected. See source errors; try a known alias or public website identifier.'));
    area.append(steps);
  }
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
async function refreshRun(caseId) {
  const report = await api('/api/investigation?' + new URLSearchParams({case: caseId}));
  if (currentCase === caseId) renderRun(report, caseId);
}
async function refreshAI() {
  clearTimeout(aiTimer);
  const ai = await api('/api/ai');
  $('ai-status').textContent = 'Local AI: ' + ai.state + (ai.error ? ' · ' + ai.error : '');
  $('use-ai').disabled = ai.state !== 'ready';
  if (ai.state !== 'ready') $('use-ai').checked = false;
  $('start-ai').disabled = ['ready', 'loading'].includes(ai.state);
  $('stop-ai').hidden = !['ready', 'loading'].includes(ai.state);
  if (ai.state === 'loading') aiTimer = setTimeout(() => refreshAI().catch(error => notice(error.message)), 1500);
}
action($('run-investigation'), () => startInvestigation(currentCase));
action($('stop-investigation'), async () => {
  const report = displayedRun;
  if (!report) return;
  await api('/api/stop', {case: report.case_id, run_id: report.run_id});
  notice('Stopping after current request. Collected sources remain saved.');
});
action($('start-ai'), async () => { await api('/api/ai/start', {}); await refreshAI(); });
action($('stop-ai'), async () => { await api('/api/ai/stop', {}); await refreshAI(); });
