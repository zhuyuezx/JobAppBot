const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = iso => iso ? iso.slice(0, 16).replace('T', ' ') : '';
const isRecent = iso => Date.now() - Date.parse(iso) < 24 * 3600 * 1000;
const money = (a, b) => { const f = n => n == null ? null : '$' + Math.round(n).toLocaleString(); const x = f(a), y = f(b); return x && y ? `${x} - ${y}` : (x || y || ''); };
async function api(path, body) {
  const r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined);
  return r.json();
}
const VIA_LABEL = { hiringcafe: 'hiring.cafe', simplify: 'Simplify', startupjobs: 'startup.jobs' };
const STATUS_CLASS = { queued: '', running: 'warn', review_ready: 'ok', needs_answer: 'warn', needs_login: 'warn', captcha: 'warn', already_applied: '', failed: 'bad', submitted: 'ok', skipped: '' };
const STATUS_LABEL = { queued: 'queued', running: 'working', review_ready: 'ready to submit', needs_answer: 'needs your answer', needs_login: 'needs login / code', captcha: 'CAPTCHA', already_applied: 'already applied', failed: 'failed', submitted: 'submitted', skipped: 'skipped' };
const badge = st => st ? `<span class="badge ${STATUS_CLASS[st] || ''}">${esc(STATUS_LABEL[st] || st)}</span>` : '';
const SP_LABEL = { likely: 'sponsor: likely', unlikely: 'sponsor: unlikely', unknown: 'sponsor: unknown' };
function spBadge(sc) {
  if (!sc) return '<span class="badge sp-pending" title="not screened yet">unscreened</span>';
  if (sc.status !== 'ok') return '<span class="badge sp-pending" title="screening failed">screen failed</span>';
  const v = sc.verdict || 'unknown';
  return `<span class="badge sp-${esc(v)}" title="${esc(sc.summary || '')}">${esc(SP_LABEL[v] || v)}${sc.fit_score != null ? ' · fit ' + sc.fit_score : ''}</span>`
       + (sc.new_grad_fit === 0 ? '<span class="badge warn" title="the screening judged this is not a 0-1 year role">not new-grad</span>' : '');
}
function screenBlock(r) {
  const sc = r.screening;
  const btn = `<button class="btn" data-act="screen">${sc ? 'Screen again' : 'Screen now'} · ${screeningProvider === 'codex' ? 'GPT' : 'Claude'}</button>`;
  if (!sc) return `<div class="screen"><p class="sum muted" style="white-space:normal">Not screened yet. ${screeningEnabled ? 'New jobs are screened automatically after each hourly scan.' : 'Automatic screening is paused. You can still screen this job manually.'}</p>${btn}</div>`;
  if (sc.status !== 'ok') return `<div class="screen"><p class="sum">Screening failed: ${esc(sc.summary || '')}</p>${btn}</div>`;
  const ev = (sc.evidence || []).map(e => `<li>${esc(e)}</li>`).join('');
  const src = (sc.sources || []).map(u => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\//, '').split('/')[0])}</a>`).join('');
  return `<div class="screen">
    <p class="sum">${spBadge(sc)} ${esc(sc.summary || '')}</p>
    <div class="muted" style="white-space:normal">posting says: <b>${esc(sc.statement || '?')}</b>${sc.requires_citizenship ? ' · requires citizenship/clearance' : ''}${sc.new_grad_fit === 0 ? ' · not a new-grad role' : ''} · ${esc(sc.model || '')} · ${fmt(sc.screened_at)}</div>
    ${ev ? `<ul>${ev}</ul>` : ''}
    ${src ? `<div class="src">${src}</div>` : ''}
    <div class="actions">${btn}</div>
  </div>`;
}

// ---------------- tabs ----------------
let tab = 'jobs';
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  clearTimeout(appsTimer);
  tab = b.dataset.tab;
  history.replaceState(null, '', '#' + tab);
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b));
  ['jobs', 'apps', 'profile', 'settings'].forEach(t => $('#tab-' + t).hidden = t !== tab);
  $('#jobsControls').style.display = tab === 'jobs' ? 'contents' : 'none';
  $('#count').textContent = '';
  if (tab === 'jobs') { load(); refreshProviders(); } if (tab === 'apps') loadApps(); if (tab === 'profile') loadProfile(); if (tab === 'settings') loadSettings();
});

// ---------------- jobs ----------------
let mode = '24h', rows = [];
function jobDetails(j, r) {
  return `<dl class="kv">
    <dt>Workplace</dt><dd>${esc(j.workplace_type || '')}</dd>
    <dt>Seniority / min YoE</dt><dd>${esc(j.seniority || '')} ${j.min_yoe != null ? '/ ' + j.min_yoe + ' yr' : ''}</dd>
    <dt>Category</dt><dd>${esc(j.category || '')}</dd>
    <dt>Clearance</dt><dd>${esc(j.security_clearance || '')}</dd>
    <dt>Salary (yearly)</dt><dd>${money(j.yearly_min_comp, j.yearly_max_comp)}</dd>
    <dt>Tools</dt><dd>${esc((j.technical_tools || []).join(', '))}</dd>
    <dt>Requirements</dt><dd>${esc(j.requirements_summary || '')}</dd>
    <dt>Company site</dt><dd>${esc(j.company_website || '')}</dd>
    <dt>Source</dt><dd>${esc(j.source || '')}${j.via ? ' via ' + esc(VIA_LABEL[j.via] || j.via) : ''}</dd>
    <dt>Posted</dt><dd>${fmt(j.published_at)}</dd>
    <dt>Found</dt><dd>${fmt(r.first_seen)} via ${esc(VIA_LABEL[j.via] || 'hiring.cafe')} (seen ${r.seen_count}x)</dd>
  </dl>`;
}
let srcFilter = 'all';
const SRC_ORDER = ['hiringcafe', 'simplify', 'startupjobs'];
const localDay = iso => { const d = new Date(iso); return isNaN(d) ? '' : `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
const localTime = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };
function groupKey(r) {
  const d = localDay(r.first_seen), today = localDay(new Date().toISOString());
  return d === today ? `Found today, ${d}` : `Found ${d}`;
}
function renderSrcTabs() {
  const counts = {}; rows.forEach(r => { const v = r.job.via || 'hiringcafe'; counts[v] = (counts[v] || 0) + 1; });
  const present = [...SRC_ORDER.filter(v => counts[v]), ...Object.keys(counts).filter(v => !SRC_ORDER.includes(v))];
  $('#srcTabs').innerHTML = `<button data-src="all" class="${srcFilter === 'all' ? 'on' : ''}">All sources <span class="badge">${rows.length}</span></button>` +
    present.map(v => `<button data-src="${esc(v)}" class="${srcFilter === v ? 'on' : ''}">${esc(VIA_LABEL[v] || v)} <span class="badge">${counts[v]}</span></button>`).join('');
  if (srcFilter !== 'all' && !counts[srcFilter]) srcFilter = 'all';
}
// Keep per-job choices while filtering or refreshing the list; save them with the application.
const launchDrafts = new Map();
let launchDefaults = {}, launchModels = [];
function launchChoice(id) {
  return launchDrafts.get(id) || {engine: applicationEngine,
    model: applicationEngine === 'codex-playwright' ? (launchDefaults.codex_model || 'gpt-5.6-luna') : (launchDefaults.model || 'opus'),
    effort: launchDefaults.codex_reasoning_effort || ''};
}
function launchControls(id) {
  const choice = launchChoice(id), gpt = choice.engine === 'codex-playwright';
  const models = [...new Set([...(gpt ? launchModels : ['opus', 'sonnet', 'haiku']), ...(choice.model ? [choice.model] : [])])];
  return `<div class="launch-options settings-fields" data-launch-id="${esc(id)}">
    <label>Application provider<select data-run-engine><option value="claude-chrome" ${!gpt ? 'selected' : ''}>Claude Code</option><option value="codex-playwright" ${gpt ? 'selected' : ''}>ChatGPT / Codex</option></select></label>
    <label>Application model<select data-run-model><option value="">Choose a model</option>${models.map(m => `<option value="${esc(m)}" ${choice.model === m ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select></label>
    ${gpt ? `<label>Thinking level<select data-run-effort>${[['','Model default'],['low','Low'],['medium','Medium'],['high','High'],['xhigh','Extra high']].map(([v,label]) => `<option value="${v}" ${choice.effort === v ? 'selected' : ''}>${label}</option>`).join('')}</select></label>` : ''}
    <span class="muted">Your last-used choices are preselected. Starting an application remembers these choices for next time.</span>
  </div>`;
}
$('#list').addEventListener('change', e => {
  const box = e.target.closest('[data-launch-id]'); if (!box) return;
  const id = box.dataset.launchId, choice = {...launchChoice(id)};
  if (e.target.matches('[data-run-engine]')) {
    choice.engine = e.target.value;
    choice.model = choice.engine === 'codex-playwright' ? (launchDefaults.codex_model || 'gpt-5.6-luna') : (launchDefaults.model || 'opus');
    choice.effort = launchDefaults.codex_reasoning_effort || '';
  } else { choice.model = box.querySelector('[data-run-model]').value; choice.effort = box.querySelector('[data-run-effort]')?.value || ''; }
  launchDrafts.set(id, choice);
  if (e.target.matches('[data-run-engine]')) box.outerHTML = launchControls(id);
});
function rowHtml(r) {
  const j = r.job, link = j.apply_url || r.hc_url;
  return `<div class="job" data-id="${esc(r.id)}">
      <div class="row">
        <div class="title"><span class="chev" aria-hidden="true">&#9654;</span><a href="${esc(link)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(j.title)}</a>
          ${j.visa_sponsorship ? '<span class="badge ok">visa</span>' : ''}${isRecent(r.first_seen) ? '<span class="badge warn">new</span>' : ''}${(srcFilter === 'all') ? `<span class="badge" title="found via ${esc(j.via || 'hiringcafe')}">${esc(VIA_LABEL[j.via] || 'hiring.cafe')}</span>` : ''}${spBadge(r.screening)}${badge(r.app_status)}</div>
        <div class="muted" title="${esc(j.company)}">${esc(j.company)}</div>
        <div class="muted" title="${esc(j.location)}">${esc(j.location || '')}</div>
        <div class="found" title="found ${esc(fmt(r.first_seen))} · posted ${esc(fmt(j.published_at))}">found ${localTime(r.first_seen)}<small>posted ${esc((j.published_at || '').slice(0, 10))}</small></div>
      </div>
      <div class="details">
        ${screenBlock(r)}
        ${jobDetails(j, r)}
        ${!r.app_status ? launchControls(r.id) : ''}
        <div class="actions">
          ${r.app_status ? `<button class="btn" data-act="goapps">Open in Applications</button>` : `<button class="btn primary" data-act="prepare">Prepare application</button>`}
          ${j.apply_url ? `<a class="btn" href="${esc(j.apply_url)}" target="_blank" rel="noopener">Apply page</a>` : ''}
          <a class="btn" href="${esc(r.hc_url)}" target="_blank" rel="noopener">${esc(VIA_LABEL[j.via] || 'hiring.cafe')}</a>
        </div>
      </div>
    </div>`;
}
function render() {
  const q = $('#q').value.trim().toLowerCase();
  renderSrcTabs();
  // Order: day found (desc) -> posting day (desc) -> company -> title -> location.
  // Posting time is only day-accurate for most sources and "found" seconds differ per scan,
  // so sorting on either to the second scatters a company's postings; day granularity keeps them together.
  const postedDay = r => (r.job.published_at || '').slice(0, 10);
  const hideUnlikely = $('#hideUnlikely').checked;
  const shown = rows.filter(r => srcFilter === 'all' || (r.job.via || 'hiringcafe') === srcFilter)
                    .filter(r => !hideUnlikely || !(r.screening && r.screening.status === 'ok' && (r.screening.verdict === 'unlikely' || r.screening.new_grad_fit === 0)))
                    .filter(r => !q || [r.title, r.company, r.location].join(' ').toLowerCase().includes(q))
                    .sort((a, b) => localDay(b.first_seen).localeCompare(localDay(a.first_seen))
                                 || postedDay(b).localeCompare(postedDay(a))
                                 || (a.job.company || '').localeCompare(b.job.company || '', undefined, { sensitivity: 'base' })
                                 || (a.job.title || '').localeCompare(b.job.title || '')
                                 || (a.job.location || '').localeCompare(b.job.location || ''));
  $('#count').textContent = `${shown.length} job${shown.length === 1 ? '' : 's'}` + (srcFilter !== 'all' || q ? ` (of ${rows.length})` : '');
  if (!shown.length) { $('#list').innerHTML = '<div class="empty">Nothing here. Run <code>jobfilter run</code> to fetch.</div>'; return; }
  const groups = new Map();
  for (const r of shown) { const k = groupKey(r); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(r); }
  const withRuns = list => list.map((r, i) => {
    const same = x => x && (x.job.company || '').toLowerCase() === (r.job.company || '').toLowerCase();
    const cls = same(list[i - 1]) || same(list[i + 1]) ? (same(list[i - 1]) ? 'run' : 'run runstart') : '';
    return rowHtml(r).replace('<div class="job"', `<div class="job ${cls}"`);
  });
  $('#list').innerHTML = [...groups.keys()].map(k => `<div class="ghead">${esc(k)} <span class="n">${groups.get(k).length}</span></div>` + withRuns(groups.get(k)).join('')).join('');
}
async function load() {
  let path = '/api/jobs';
  if (mode === '24h') path += '?since=24'; else if (mode === '72h') path += '?since=72';
  else if (mode === 'date') path += '?date=' + encodeURIComponent($('#date').value || '');
  rows = await api(path); render();
}
async function loadDates() {
  const dates = await api('/api/dates');
  $('#date').innerHTML = dates.map(d => `<option value="${d.date}">${d.date} (${d.count})</option>`).join('') || '<option value="">no data</option>';
}
$('#mode').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  mode = b.dataset.mode;
  document.querySelectorAll('#mode button').forEach(x => x.classList.toggle('on', x === b));
  $('#date').hidden = mode !== 'date'; load();
});
$('#date').addEventListener('change', load);
$('#srcTabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  srcFilter = b.dataset.src;
  try { localStorage.setItem('jf.src', srcFilter); } catch (_) {}
  render();
});
try { srcFilter = localStorage.getItem('jf.src') || 'all'; } catch (_) {}
$('#q').addEventListener('input', render);
$('#hideUnlikely').addEventListener('change', () => { try { localStorage.setItem('jf.hideUnlikely', $('#hideUnlikely').checked ? '1' : ''); } catch (_) {} render(); });
try { $('#hideUnlikely').checked = localStorage.getItem('jf.hideUnlikely') === '1'; } catch (_) {}
$('#list').addEventListener('click', async e => {
  const job = e.target.closest('.job'); if (!job) return;
  const act = e.target.closest('[data-act]');
  if (act) {
    if (act.dataset.act === 'screen') {
      act.disabled = true; act.textContent = 'Screening... (about 30s)';
      await api('/api/screen', { job_id: job.dataset.id });
      const poll = async (n) => { await new Promise(res => setTimeout(res, 5000)); await load(); const row = rows.find(x => x.id === job.dataset.id); if (row && row.screening && n < 24) { /* done */ } else if (n < 24) poll(n + 1); };
      poll(0); return;
    }
    if (act.dataset.act === 'prepare') {
      const box = job.querySelector('[data-launch-id]');
      const engine = box.querySelector('[data-run-engine]').value;
      const model = box.querySelector('[data-run-model]').value;
      if (!model) { alert('Choose an application model first.'); return; }
      const settings = engine === 'codex-playwright' ? {codex_model: model, codex_reasoning_effort: box.querySelector('[data-run-effort]').value} : {model};
      act.disabled = true; act.textContent = 'Queued...';
      const r = await api('/api/applications/queue', { job_id: job.dataset.id, engine, settings });
      if (r.error) { alert(r.error); act.disabled = false; act.textContent = 'Prepare application'; return; }
      launchDrafts.delete(job.dataset.id);
      await refreshProviders();
      await load(); $('#tabs button[data-tab=apps]').click();
    } else if (act.dataset.act === 'goapps') { $('#tabs button[data-tab=apps]').click(); }
    return;
  }
  if (e.target.closest('a')) return;
  if (!e.target.closest('.row')) return;          // clicks inside the expanded details do nothing
  job.classList.toggle('open');
});

// ---------------- applications ----------------
let apps = [], openApp = null, appsTimer = null;
let applicationEngine = 'claude-chrome', screeningProvider = 'claude', screeningEnabled = true;
const ATTENTION_STATUSES = ['review_ready', 'needs_answer', 'needs_login', 'captcha'];
function updateAttention(count) { $('#appsBadge').hidden = !count; $('#appsBadge').textContent = count; }
const providerName = engine => ({'codex-playwright': 'ChatGPT / Codex', 'codex': 'ChatGPT / Codex'}[engine] || 'Claude Code');
function engineBanner(st) {
  applicationEngine = (st.launch_settings || st.settings).engine;
  $('#engine').innerHTML = `<span>Next application: <strong>${providerName(applicationEngine)}</strong> · remembers your last-used choices</span><a href="#settings">AI settings</a>`;

}
async function refreshProviders() {
  try {
    const [st, sc] = await Promise.all([api('/api/engine'), api('/api/screening-settings')]);
    if (st.error) throw new Error(st.error);
    launchDefaults = st.launch_settings || st.settings;
    applicationEngine = launchDefaults.engine;
    launchModels = st.codex_models || [];
    updateAttention(ATTENTION_STATUSES.reduce((n, status) => n + (st.counts[status] || 0), 0));
    if (sc.settings) { screeningProvider = sc.settings.provider; screeningEnabled = sc.settings.enabled; }
    const screening = sc.settings ? (sc.settings.enabled ? providerName(sc.settings.provider) : 'Paused') : 'Unavailable';
    $('#providerSummary').innerHTML = `<span>Screening: <strong>${screening}</strong></span><span>Applications: <strong>${providerName(applicationEngine)}</strong></span><a href="#settings">Change AI providers</a>`;
    if (tab === 'jobs') render();
  } catch (_) { $('#providerSummary').innerHTML = 'Could not load providers. <a href="#settings">Open AI settings</a>'; }
}
function providerCards(name, selected, application) {
  return `<fieldset><legend>${application ? 'Application provider' : 'Screening provider'}</legend><div class="provider-options">
    <label class="provider-card"><input type="radio" name="${name}" value="claude" ${selected === 'claude' ? 'checked' : ''}><span><strong>Claude Code</strong><small>${application ? 'Starts automatically when you queue a job. Uses Claude in Chrome.' : 'Checks sponsorship and fit using your Claude subscription.'}</small></span></label>
    <label class="provider-card"><input type="radio" name="${name}" value="codex" ${selected === 'codex' ? 'checked' : ''}><span><strong>ChatGPT / Codex</strong><small>${application ? 'Starts automatically when you click Prepare with GPT. Uses a dedicated Chrome window.' : 'Checks sponsorship and fit automatically using your ChatGPT subscription.'}</small></span></label>
    </div></fieldset>`;
}
function thinkingSelect(id, selected = '') {
  return `<label>GPT thinking level<select id="${id}">${[['', 'Model default'], ['low', 'Low · faster'], ['medium', 'Medium · balanced'], ['high', 'High · more thorough'], ['xhigh', 'Extra high · most thorough']].map(([value, label]) => `<option value="${value}" ${selected === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>`;
}
function settingMessage(id, text, error = false) { const el = $(id); el.textContent = text; el.classList.toggle('error', error); }
function renderApplicationSettings(st) {
  const saved = st.settings, selected = saved.engine === 'codex-playwright' ? 'codex' : 'claude';
  $('#applicationSettings').innerHTML = `<h2>Application defaults</h2><p class="help">Saved provider: <strong id="savedAppProvider">${providerName(saved.engine)}</strong>. Job controls remember your last-used provider, model and thinking level; these defaults apply before your first use. Existing applications keep their saved choices.</p>
    ${providerCards('applicationProvider', selected, true)}
    <div id="applicationGuide"></div>
    <div id="claudeSettings" class="settings-fields"><label>Claude model<input id="modelInp" type="text" value="${esc(saved.model)}" list="claudeModels"><datalist id="claudeModels">${st.model_choices.map(m => `<option value="${esc(m)}">`).join('')}</datalist></label><label>Maximum turns<input id="turnsInp" type="text" inputmode="numeric" value="${saved.max_turns}"></label></div>
    <div id="gptSettings" class="settings-fields">${thinkingSelect('appThinking', saved.codex_reasoning_effort)}<label>GPT application model (optional)<input id="appCodexModel" type="text" placeholder="Use Codex default" value="${esc(saved.codex_model || '')}"></label><label>Timeout in seconds<input id="appCodexTimeout" type="number" min="30" max="3600" value="${saved.codex_timeout || 900}"></label></div>
    <p class="help">Profile: ${st.profile.profile_exists ? 'saved' : 'not saved'} · Resume: ${st.profile.resume ? esc(st.profile.resume.split('/').pop()) : 'not added'}. <a href="#profile">Edit profile and answers</a></p>
    <div class="actions"><button class="btn primary" id="saveApplication">Save application provider</button><span id="applicationMsg" class="status-message" role="status"></span></div>`;
  function preview() {
    const automatic = $('input[name="applicationProvider"]:checked').value === 'codex';
    $('#claudeSettings').hidden = automatic;
    $('#gptSettings').hidden = !automatic;
    $('#applicationGuide').innerHTML = automatic ? `<div class="provider-guide"><strong>Prepare → answer questions → review</strong><p>Save this provider, then open a job and click “Prepare with GPT”. The browser starts automatically. Answers entered here continue the application automatically.</p><p>A separate Chrome profile keeps its sign-ins and review tabs. Sign in there once if a job requires it, then click Run again. Review and submit yourself.</p><p>${st.codex ? 'Codex CLI found; uses your saved ChatGPT login.' : 'Install Codex and run codex login with ChatGPT first.'} ${st.bridge?.installed ? (st.bridge.running ? 'Browser bridge is running.' : 'Browser bridge is installed and starts on demand.') : 'Install the bridge first; see docs/CODEX_INTEGRATION.md.'}</p><button class="btn" id="startBridge">Open automation browser</button><span id="bridgeMsg" role="status"></span></div>` : `<div class="provider-guide"><strong>Queue a job and Claude starts automatically</strong><p>In Jobs, open a listing and choose “Prepare with Claude”. Claude fills the form and stops for your review.</p><p>${st.claude && st.chrome_host_installed ? 'Claude Code and its Chrome integration are installed.' : 'Setup needed: install Claude Code and Claude in Chrome, then run claude --chrome once.'}</p></div>`;
  }
  $('#applicationSettings').onclick = async e => {
    if (e.target.id !== 'startBridge') return;
    e.target.disabled = true;
    try { const r = await api('/api/bridge/start', {}); if (r.error) throw new Error(r.error); $('#bridgeMsg').textContent = ' Browser ready. Use its Chrome window for sign-in and review.'; }
    catch (err) { $('#bridgeMsg').textContent = ' ' + err.message; }
    finally { e.target.disabled = false; }
  };
  preview();
  $('#applicationSettings').onchange = () => { preview(); settingMessage('#applicationMsg', 'Unsaved changes — save to use this provider.'); };
  $('#saveApplication').onclick = async () => {
    const button = $('#saveApplication'); button.disabled = true;
    try {
      const r = await api('/api/settings', {engine: {claude: 'claude-chrome', codex: 'codex-playwright'}[$('input[name="applicationProvider"]:checked').value], codex_reasoning_effort: $('#appThinking').value, codex_model: $('#appCodexModel').value, codex_timeout: Number($('#appCodexTimeout').value), model: $('#modelInp').value, max_turns: Number($('#turnsInp').value) || 120});
      if (r.error) throw new Error(r.error);
      $('#modelInp').value = r.model; $('#turnsInp').value = r.max_turns;
      applicationEngine = r.engine; $('#savedAppProvider').textContent = providerName(r.engine);
      settingMessage('#applicationMsg', `Saved. New applications will use ${providerName(r.engine)}.`);
    } catch (e) { settingMessage('#applicationMsg', 'Could not save: ' + e.message, true); }
    finally { button.disabled = false; }
  };
}
function renderScreeningSettings(sc) {
  const saved = sc.settings;
  $('#screeningSettings').innerHTML = `<h2>Job screening</h2><p class="help">Sponsorship checks and fit scores. Independent of application filling. Saved provider: <strong id="savedScreenProvider">${providerName(saved.provider)}</strong>.</p>
    ${providerCards('screeningProvider', saved.provider, false)}
    <label><input id="screenEnabled" type="checkbox" ${saved.enabled ? 'checked' : ''}> Screen new jobs automatically after each scan</label>
    <div id="screeningGuide" class="provider-guide"></div>
    <div class="settings-fields"><label id="screenClaudeField">Claude screening model<input id="screenClaudeModel" type="text" value="${esc(saved.model)}"></label>
    <div id="screenCodexField">${thinkingSelect('screenThinking', saved.codex_reasoning_effort)}<label>GPT screening model (optional)<input id="screenCodexModel" type="text" placeholder="Use Codex default" value="${esc(saved.codex_model)}"></label></div></div>
    <div class="actions"><button class="btn primary" id="saveScreening">Save screening provider</button><span id="screeningMsg" class="status-message" role="status"></span></div>`;
  function preview() {
    const gpt = $('input[name="screeningProvider"]:checked').value === 'codex';
    $('#screenClaudeField').hidden = gpt; $('#screenCodexField').hidden = !gpt;
    $('#screeningGuide').innerHTML = gpt ? `<strong>Automatic screening with your ChatGPT subscription</strong><p>${sc.codex_found ? 'Codex CLI is installed.' : 'Install Codex CLI first.'} Sign in with ChatGPT using <code>codex login</code>. No API key is needed.</p><p>Save to use GPT for the next scan and “Screen now”. Existing scores stay unchanged until you screen those jobs again.</p>` : `<strong>Automatic screening with your Claude subscription</strong><p>${sc.claude_found ? 'Claude Code is installed.' : 'Install Claude Code and sign in first.'} Save to use Claude for the next scan and “Screen now”.</p>`;
  }
  preview();
  $('#screeningSettings').onchange = () => { preview(); settingMessage('#screeningMsg', 'Unsaved changes — save to apply.'); };
  $('#saveScreening').onclick = async () => {
    const button = $('#saveScreening'); button.disabled = true;
    try {
      const r = await api('/api/screening-settings', {provider: $('input[name="screeningProvider"]:checked').value, enabled: $('#screenEnabled').checked, model: $('#screenClaudeModel').value, codex_model: $('#screenCodexModel').value, codex_reasoning_effort: $('#screenThinking').value});
      if (r.error) throw new Error(r.error);
      $('#savedScreenProvider').textContent = providerName(r.provider);
      settingMessage('#screeningMsg', 'Saved. Applies to the next scan and manual screenings.');
    } catch (e) { settingMessage('#screeningMsg', 'Could not save: ' + e.message, true); }
    finally { button.disabled = false; }
  };
}
async function loadSettings() {
  $('#applicationSettings').textContent = 'Loading application settings…';
  $('#screeningSettings').textContent = 'Loading screening settings…';
  const results = await Promise.allSettled([api('/api/engine'), api('/api/screening-settings')]);
  for (const [i, res] of results.entries()) {
    const id = i === 0 ? '#applicationSettings' : '#screeningSettings';
    if (res.status === 'rejected' || res.value.error) { $(id).textContent = 'Could not load settings. Reload this page to retry.'; continue; }
    (i === 0 ? renderApplicationSettings : renderScreeningSettings)(res.value);
  }
}
function appDetails(a) {
  const j = a.job, qs = (a.questions || []).filter(q => q.status === 'open');
  return `<div class="details">
    <dl class="kv">
      <dt>Status</dt><dd>${badge(a.status)} <span class="muted">attempt ${a.attempts}, updated ${fmt(a.updated_at)}</span></dd>
      <dt>Provider</dt><dd>${providerName(a.engine)}</dd>
      <dt>Submitted</dt><dd>${a.submitted_at ? fmt(a.submitted_at) + (a.submitted_at_estimated ? ' (estimated from legacy last update)' : '') : 'Not recorded'}</dd>
      ${a.settings ? `<dt>Model</dt><dd>${esc(a.engine === 'codex-playwright' ? a.settings.codex_model : a.settings.model)}${a.engine === 'codex-playwright' ? ' · thinking: ' + esc(a.settings.codex_reasoning_effort || 'model default') : ''}</dd>` : ''}
      <dt>Summary</dt><dd>${esc(a.summary || '')}</dd>
      ${a.page_url ? `<dt>Tab</dt><dd><a href="${esc(a.page_url)}" target="_blank" rel="noopener">${esc(a.page_url)}</a></dd>` : ''}
      <dt>Note</dt><dd><input type="text" data-note value="${esc(a.note || '')}" placeholder="your note, saved with status changes" style="width:100%"></dd>
    </dl>
    ${qs.length ? `<h2>Questions for you (${qs.length})</h2>` + qs.map(q => `<div class="q" data-qid="${q.id}">
        <div class="qt">${esc(q.question)}</div>
        ${q.options ? `<div class="muted" style="white-space:normal">Options: ${esc(q.options.join(' | '))}</div>` : ''}
        <input type="text" data-answer placeholder="your answer">
        <div class="actions"><button class="btn primary" data-act="answer">Answer &amp; save to bank</button><button class="btn" data-act="answer-once">Answer once</button></div>
      </div>`).join('') : ''}
    <div class="actions">
      ${['review_ready','needs_login','captcha'].includes(a.status) ? `<a class="btn primary" href="${esc(a.page_url || j.apply_url)}" target="_blank" rel="noopener">Open the tab and finish</a>` : ''}
      ${['review_ready','needs_login','captcha'].includes(a.status) ? `<button class="btn" data-act="status" data-status="submitted">Mark submitted</button>` : ''}
      ${['failed','needs_login','captcha','already_applied','skipped','submitted'].includes(a.status) ? `<button class="btn" data-act="retry">Run again</button>` : ''}
      ${['queued','running'].includes(a.status) ? `<button class="btn" data-act="status" data-status="skipped">Cancel</button>` : `<button class="btn" data-act="status" data-status="skipped">Skip</button>`}
      <button class="btn" data-act="refresh">Refresh</button>
    </div>
    ${a.screenshot ? `<img class="shot" src="/api/file?path=${encodeURIComponent(a.screenshot)}" alt="review page">` : ''}
    <h2>Activity</h2>
    <pre class="log" data-log>${esc((a.log || []).join('\n')) || '(no log yet)'}</pre>
  </div>`;
}
$('#appSort').addEventListener('change', renderApps);
function renderApps() {
  const counts = {}; apps.forEach(a => counts[a.status] = (counts[a.status] || 0) + 1);
  $('#count').textContent = Object.entries(counts).map(([k, v]) => `${v} ${STATUS_LABEL[k] || k}`).join(' · ');
  updateAttention(apps.filter(a => ATTENTION_STATUSES.includes(a.status)).length);
  if (!apps.length) { $('#apps').innerHTML = '<div class="empty">No applications yet. Open a job in Jobs and choose Prepare with Claude or Prepare with GPT.</div>'; return; }
  const order = ['needs_answer', 'review_ready', 'needs_login', 'captcha', 'running', 'queued', 'failed', 'already_applied', 'submitted', 'skipped'];
  const sort = $('#appSort').value;
  const recent = (x, y) => (y.updated_at || '').localeCompare(x.updated_at || '') || x.job_id.localeCompare(y.job_id);
  apps.sort((x, y) => {
    if (sort.startsWith('submitted-')) {
      if (!!x.submitted_at !== !!y.submitted_at) return x.submitted_at ? -1 : 1;
      return (sort === 'submitted-asc' ? 1 : -1) * (x.submitted_at || '').localeCompare(y.submitted_at || '') || recent(x, y);
    }
    if (sort === 'attention') return order.indexOf(x.status) - order.indexOf(y.status) || recent(x, y);
    if (sort === 'company') return (x.job.company || '').localeCompare(y.job.company || '') || recent(x, y);
    return recent(x, y);
  });
  $('#apps').innerHTML = apps.map((a, rank) => `<div class="app ${openApp === a.job_id ? 'open' : ''}" data-id="${esc(a.job_id)}">
      <div class="row">
        <div class="title"><span class="badge">#${rank + 1}</span><span class="chev" aria-hidden="true">&#9654;</span><a href="${esc(a.job.apply_url || a.hc_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(a.job.title)}</a>${badge(a.status)}<span class="badge">${providerName(a.engine)}</span>${a.open_questions ? `<span class="badge warn">${a.open_questions} question${a.open_questions > 1 ? 's' : ''}</span>` : ''}</div>
        <div class="muted">${esc(a.job.company)}</div>
        <div class="muted">${esc(a.summary || '').slice(0, 80)}</div>
        <div class="muted">${a.submitted_at ? 'Submitted ' + fmt(a.submitted_at) + (a.submitted_at_estimated ? ' (est.)' : '') : 'Not submitted · updated ' + fmt(a.updated_at)}</div>
      </div>
      ${openApp === a.job_id ? appDetails(a) : '<div class="details"></div>'}
    </div>`).join('');
}
async function loadApps() {
  if (tab !== 'apps') return;
  const [st, list] = await Promise.all([api('/api/engine'), api('/api/applications')]);
  if (tab !== 'apps') return;
  apps = list;
  engineBanner(st);
  if (openApp) { const full = await api('/api/application?id=' + encodeURIComponent(openApp)); const i = apps.findIndex(a => a.job_id === openApp); if (i >= 0 && !full.error) apps[i] = full; }
  renderApps();
  clearTimeout(appsTimer);
  if (tab === 'apps' && apps.some(a => ['queued', 'running'].includes(a.status))) appsTimer = setTimeout(loadApps, 4000);
}
$('#apps').addEventListener('click', async e => {
  const el = e.target.closest('.app'); if (!el) return;
  const id = el.dataset.id, act = e.target.closest('[data-act]');
  if (act) {
    const kind = act.dataset.act;
    if (kind === 'status') await api('/api/applications/status', { job_id: id, status: act.dataset.status, note: el.querySelector('[data-note]')?.value || '' });
    else if (kind === 'retry') await api('/api/applications/retry', { job_id: id });
    else if (kind === 'answer' || kind === 'answer-once') {
      const qel = act.closest('.q'); const ans = qel.querySelector('[data-answer]').value.trim();
      if (!ans) return;
      const r = await api('/api/questions/answer', { id: Number(qel.dataset.qid), answer: ans, save: kind === 'answer' });
      if (r.requeued) $('#count').textContent = 'All answered. The application will continue automatically.';
    }
    await loadApps(); return;
  }
  if (e.target.closest('a') || e.target.closest('input') || e.target.closest('textarea')) return;
  if (!e.target.closest('.row')) return;          // only the header bar toggles; the details area is inert
  openApp = openApp === id ? null : id;
  await loadApps();
});

// ---------------- profile ----------------
async function loadProfile() {
  const [{ profile, status }, answers] = await Promise.all([api('/api/profile'), api('/api/answers')]);
  $('#profileStatus').innerHTML = [
    status.profile_exists ? '✓ setup/profile.json' : '✗ not saved yet (showing the template)',
    status.resume ? '✓ resume: ' + esc(status.resume.split('/').pop()) + ` (${status.resume_text_chars} chars extracted)` : '✗ put a PDF in setup/resume/',
    `${answers.length} bank answers`].map(x => `<span>${x}</span>`).join('');
  $('#profileText').value = JSON.stringify(profile, null, 2);
  $('#answers').innerHTML = answers.length ? '<tr><th>Question</th><th>Answer</th><th></th></tr>' + answers.map(a =>
    `<tr><td>${esc(a.question)}</td><td>${esc(a.answer)}</td><td><button class="btn" data-del="${a.id}">✕</button></td></tr>`).join('') : '<tr><td class="muted">empty</td></tr>';
}
$('#saveProfile').addEventListener('click', async () => {
  let obj; try { obj = JSON.parse($('#profileText').value); } catch (e) { $('#profileMsg').textContent = 'Invalid JSON: ' + e.message; return; }
  const r = await api('/api/profile', { profile: obj });
  $('#profileMsg').textContent = r.ok ? 'Saved.' : (r.error || 'error'); loadProfile();
});
$('#addAnswer').addEventListener('click', async () => {
  const q = $('#newQ').value.trim(), a = $('#newA').value.trim(); if (!q) return;
  await api('/api/answers', { question: q, answer: a }); $('#newQ').value = ''; $('#newA').value = ''; loadProfile();
});
$('#answers').addEventListener('click', async e => {
  const b = e.target.closest('[data-del]'); if (!b) return;
  await api('/api/answers/delete', { id: Number(b.dataset.del) }); loadProfile();
});

function showTab(name) { const b = document.querySelector(`#tabs button[data-tab="${name}"]`); if (b) b.click(); }
window.addEventListener('hashchange', () => showTab(location.hash.slice(1) || 'jobs'));
refreshProviders();
loadDates().then(load).then(() => { if (location.hash && location.hash !== '#jobs') showTab(location.hash.slice(1)); });
