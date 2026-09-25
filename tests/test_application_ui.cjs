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
      addEventListener() {}, setAttribute() {}, classList: {toggle() {}},
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
    assert.match(ui.$('#apps').innerHTML, /^<div class="app open [^"]*" data-id="current"/);
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
      assert.match(ui.$('#apps').innerHTML, /^<div class="app [^"]*" data-id="spacex"/, `${status} / ${state}`);
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
