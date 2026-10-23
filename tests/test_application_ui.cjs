const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const vm = require('node:vm');

// Exercise the actual application renderer and polling transitions with API fixtures.
function frontend() {
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: 'submitted-desc', checked: false, hidden: false, textContent: '', innerHTML: '',
      addEventListener() {}, setAttribute() {}, querySelectorAll: () => [], classList: {toggle() {}},
    });
    return elements.get(selector);
  };
  const context = vm.createContext({
    $, tab: 'jobs', document: {activeElement: null},
    esc: value => String(value ?? ''), fmt: value => value || '', badge: value => value,
    rowActions: () => '', reviewBadges: () => '', STATUS_LABEL: {},
    setTimeout: () => 1, clearTimeout() {},
    api: async () => context.records,
  });
  const source = readFileSync('jobFilter/static/app.js', 'utf8');
  vm.runInContext(source.split('// ---------------- applications ----------------')[1]
    .split('// ---------------- profile ----------------')[0], context);
  return {context, $, run: code => vm.runInContext(code, context)};
}

function application(status, id = status) {
  return {job_id: id, status, job: {title: id, company: id}, attempts: 1,
    updated_at: '2026-09-23T12:00:00Z',
    submitted_at: status === 'submitted' ? '2026-09-23T12:00:00Z' : null};
}

const pending = ['queued', 'running', 'review_ready', 'needs_answer', 'needs_login', 'captcha'];
const terminal = ['submitted', 'already_applied', 'unavailable', 'skipped', 'failed'];

test('working and pending applications precede terminal entries in every sort mode', async () => {
  const ui = frontend();
  ui.context.records = [...terminal, ...pending].map(status => application(status));
  await ui.run('loadApps()');
  for (const sort of ['submitted-desc', 'submitted-asc', 'updated', 'attention', 'company']) {
    ui.$('#appSort').value = sort;
    ui.run('renderApps()');
    const ids = [...ui.$('#apps').innerHTML.matchAll(/class="app [^"]*" data-id="([^"]+)"/g)].map(m => m[1]);
    assert.equal(ids.length, 11);
    assert.deepEqual(new Set(ids.slice(0, 6)), new Set(pending), sort);
  }
  const popup = ui.$('#activityItems').innerHTML;
  for (const status of pending) assert.ok(popup.includes(`data-activity-id="${status}"`));
  for (const status of terminal) assert.ok(!popup.includes(`data-activity-id="${status}"`));
});

test('blocked transitions stay pinned, open details, and leave activity only at a terminal state', async () => {
  const ui = frontend();
  for (const status of ['running', 'needs_answer', 'queued', 'needs_login', 'captcha', 'review_ready']) {
    ui.context.records = [application('submitted', 'old'), application(status, 'current')];
    await ui.run('loadApps()');
    ui.run('renderApps()');
    assert.match(ui.$('#apps').innerHTML, /<div class="app open [^"]*" data-id="current"/);
    assert.equal(ui.$('#applicationActivity').hidden, false);
    assert.match(ui.$('#activityItems').innerHTML, /data-activity-id="current"/);
    // A user's manual collapse survives polling while the status is unchanged.
    ui.run("expandedApps.delete('current')");
    await ui.run('loadApps()');
    assert.equal(ui.run("expandedApps.has('current')"), false);
  }
  for (const status of terminal) {
    ui.context.records = [application(status, 'current')];
    await ui.run('loadApps()');
    assert.equal(ui.$('#applicationActivity').hidden, true, status);
    assert.ok(!ui.$('#activityItems').innerHTML.includes('data-activity-id="current"'));
  }
});

test('pending jobs stay pinned whatever their suitability, until a terminal state', async () => {
  const ui = frontend();
  for (const status of ['captcha', 'needs_login', 'needs_answer', 'review_ready']) {
    for (const state of ['needs_review', 'not_suitable']) {
      const job = {...application(status, 'spacex'), review: {state}};
      ui.context.records = [application('submitted', 'finished'), job];
      await ui.run('loadApps()');
      ui.run('renderApps()');
      assert.match(ui.$('#apps').innerHTML, /<div class="app [^"]*" data-id="spacex"/, `${status} / ${state}`);
      assert.match(ui.$('#activityItems').innerHTML, /data-activity-id="spacex"/);
      assert.equal(ui.$('#appsBadge').textContent, 1);
    }
  }
  for (const status of terminal) {
    ui.context.records = [application('submitted', 'finished'), {...application(status, 'spacex'), review: {state: 'needs_review'}}];
    await ui.run('loadApps()');
    assert.equal(ui.$('#applicationActivity').hidden, true, status);
  }
});

test('unsuitable jobs remain visible while automation is still queued or running', async () => {
  const ui = frontend();
  for (const status of ['queued', 'running']) {
    ui.context.records = [{...application(status), review: {state: 'not_suitable'}}];
    await ui.run('loadApps()');
    assert.equal(ui.$('#applicationActivity').hidden, false);
    assert.equal(ui.$('#appsBadge').hidden, true);
  }
});

test('active is expanded, terminal categories are collapsed, and failed precedes submitted', async () => {
  const ui = frontend();
  ui.context.records = [...terminal, ...pending].map(status => application(status));
  await ui.run('loadApps()');
  ui.run('renderApps()');
  const html = ui.$('#apps').innerHTML;
  assert.match(html, /data-app-group-toggle="active" aria-expanded="true"/);
  for (const status of terminal) {
    assert.match(html, new RegExp(`data-app-group-toggle="${status}" aria-expanded="false"`));
    assert.equal(ui.run(`expandedApps.has('${status}')`), false);
  }
  assert.ok(html.indexOf('data-app-group="failed"') < html.indexOf('data-app-group="submitted"'));
  ui.run("appGroupChoices.set('active', false); appGroupChoices.set('submitted', true)");
  await ui.run('loadApps()');
  assert.equal(ui.run("appGroupOpen('active')"), false);
  assert.equal(ui.run("appGroupOpen('submitted')"), true);
});

test('completion collapses the previous active row while manual expansion survives later polls', async () => {
  const ui = frontend();
  for (const status of ['submitted', 'already_applied', 'unavailable', 'skipped']) {
    ui.context.records = [application('review_ready', 'current')];
    await ui.run('loadApps()');
    assert.equal(ui.run("expandedApps.has('current')"), true);
    ui.context.records = [application(status, 'current')];
    await ui.run('loadApps()');
    assert.equal(ui.run("expandedApps.has('current')"), false, status);
    ui.run("expandedApps.add('current')");
    await ui.run('loadApps()');
    assert.equal(ui.run("expandedApps.has('current')"), true);
  }
});

test('a new failure opens its category and details, but a user can collapse it again', async () => {
  const ui = frontend();
  ui.context.records = [application('running', 'current')];
  await ui.run('loadApps()');
  ui.context.records = [application('failed', 'current')];
  await ui.run('loadApps()');
  assert.equal(ui.run("appGroupOpen('failed')"), true);
  assert.equal(ui.run("expandedApps.has('current')"), true);
  ui.run("appGroupChoices.set('failed', false)");
  await ui.run('loadApps()');
  assert.equal(ui.run("appGroupOpen('failed')"), false);
  ui.$('#unfinishedOnly').checked = true;
  ui.run('renderApps()');
  assert.match(ui.$('#apps').innerHTML, /data-app-group="failed"/);
});

test('a slow detail refresh never publishes a summary-only application', async () => {
  const ui = frontend();
  ui.context.records = [{...application('needs_answer', 'current'),
    questions: [{id: 1, question: 'Start date?', status: 'open'}]}];
  await ui.run('loadApps()');
  ui.context.tab = 'apps';
  let resolveDetails;
  ui.context.api = async path => {
    if (path.startsWith('/api/application?')) return new Promise(resolve => resolveDetails = resolve);
    if (path === '/api/applications') return [application('needs_answer', 'current')];
    return {error: 'settings unavailable'};
  };
  const poll = ui.run('loadApps()');
  await new Promise(resolve => setImmediate(resolve));
  ui.run('renderApps()');
  assert.match(ui.$('#apps').innerHTML, /Start date\?/);
  resolveDetails({error: 'temporary detail failure'});
  await poll;
  assert.match(ui.$('#apps').innerHTML, /Start date\?/);
});

function questionForm(ui) {
  const entries = [1, 2, 3].map(id => {
    const input = {value: `draft ${id}`};
    const message = {textContent: ''};
    const buttons = [{disabled: false}, {disabled: false}];
    const q = {dataset: {qid: String(id)}, input, message, buttons,
      querySelector: s => s === '[data-answer]' ? input : message,
      querySelectorAll: () => buttons,
      remove() { entries.splice(entries.indexOf(q), 1); }};
    return q;
  });
  const heading = {textContent: '', remove() {}};
  const el = {dataset: {id: 'current'}, querySelectorAll: () => entries,
    querySelector: () => heading};
  const q = entries[1];
  const act = {dataset: {act: 'answer-once'}, closest: () => q};
  ui.context.el = el;
  ui.context.act = act;
  ui.context.records = [{...application('needs_answer', 'current'),
    questions: entries.map(e => ({id: Number(e.dataset.qid), question: `Question ${e.dataset.qid}`, status: 'open'}))}];
  return {entries, q, heading};
}

test('answering the second question preserves other nodes and drafts, and blocks double clicks', async () => {
  const ui = frontend();
  const {entries, q, heading} = questionForm(ui);
  const first = entries[0], third = entries[2];
  await ui.run('loadApps()');
  let resolveAnswer, calls = 0;
  ui.context.api = () => { calls++; return new Promise(resolve => resolveAnswer = resolve); };
  const save = ui.run('submitApplicationAnswer(el, act)');
  assert.ok(q.buttons.every(b => b.disabled));
  await ui.run('submitApplicationAnswer(el, act)');
  assert.equal(calls, 1);
  assert.equal(entries.length, 3);
  first.input.value = 'edited while saving';
  resolveAnswer({requeued: false});
  await save;
  assert.deepEqual(entries, [first, third]);
  assert.equal(first.input.value, 'edited while saving');
  assert.equal(third.input.value, 'draft 3');
  assert.equal(heading.textContent, 'Questions for you (2)');
  assert.equal(ui.run("apps[0].questions[1].status"), 'answered');
  assert.equal(ui.run('apps[0].open_questions'), 2);
});

test('a failed answer save retains every input and permits retry', async () => {
  const ui = frontend();
  const {entries, q} = questionForm(ui);
  await ui.run('loadApps()');
  ui.context.api = async () => { throw new Error('offline'); };
  await ui.run('submitApplicationAnswer(el, act)');
  assert.equal(entries.length, 3);
  assert.equal(q.input.value, 'draft 2');
  assert.match(q.message.textContent, /Could not save: offline/);
  assert.ok(q.buttons.every(b => !b.disabled));
  assert.equal(ui.run('savingAnswers.size'), 0);
});
