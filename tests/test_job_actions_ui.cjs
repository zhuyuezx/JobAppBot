const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const vm = require('node:vm');

function frontend() {
  const elements = new Map(), calls = [], timers = [];
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: '', checked: false, hidden: false, textContent: '', innerHTML: '',
      addEventListener() {}, classList: {toggle() {}},
    });
    return elements.get(selector);
  };
  const job = {id: 'test', job: {title:'Engineer', company:'Example'}, first_seen:'2020-01-01T00:00:00Z',
    review: {state:'needs_review', automatic_state:'needs_review', reasons:[], tags:{}, tag_overrides:{}, custom_tags:[]}};
  const context = vm.createContext({
    document: {querySelector: element, querySelectorAll: () => []},
    localStorage: {getItem() {return null;}}, console,
    alert: message => {throw new Error(message);},
    setTimeout: callback => {timers.push(callback); return timers.length;}, clearTimeout() {},
    fetch: async (path, options) => {
      calls.push({path, body: options ? JSON.parse(options.body) : null});
      if (path === '/api/jobs/review') job.review.pending = JSON.parse(options.body).pending;
      return {json: async () => path.startsWith('/api/jobs?pending=1') ? (job.review.pending ? [job] : []) : path.startsWith('/api/jobs') && !options ? [job] : {}};
    },
  });
  vm.runInContext(readFileSync('jobFilter/static/app.js','utf8').split('// ---------------- applications ----------------')[0]
    + "\nlet screeningProvider='claude', screeningEnabled=true, applicationEngine='claude-chrome'; async function loadApps() {}", context);
  return {job, calls, timers, element, run: code => vm.runInContext(code, context)};
}

test('pending toggle saves a job without queuing and the all-date pending view can clear it', async () => {
  const ui = frontend();
  await ui.run('load()');
  await ui.run("handleRowAction({dataset:{act:'toggle-pending'}}, 'test')");
  assert.equal(ui.job.review.pending, true);
  assert.ok(ui.calls.some(c => c.path === '/api/jobs/review' && c.body.pending));
  assert.ok(!ui.calls.some(c => c.path.includes('/applications/')));
  await ui.run("mode='pending'; load()");
  assert.equal(ui.calls.at(-1).path, '/api/jobs?pending=1');
  assert.match(ui.element('#list').innerHTML, /Clear pending/);
  assert.match(ui.element('#list').innerHTML, />Pending<\/span>/);
  await ui.run("handleRowAction({dataset:{act:'toggle-pending'}}, 'test')");
  assert.equal(ui.job.review.pending, false);
  assert.match(ui.element('#list').innerHTML, /No pending jobs/);
});

test('left screening button tracks a re-screen even when an old result exists', async () => {
  const ui = frontend();
  ui.job.screening = {status:'ok', summary:'old result'};
  ui.job.screening_running = true;
  await ui.run('load()');
  const html = ui.element('#list').innerHTML;
  assert.ok(html.indexOf('data-act="screen"') < html.indexOf('class="chev"'));
  assert.match(html, /aria-label="Screening suitability…" disabled/);
  assert.match(html, /activity-spinner/);
  assert.equal(ui.timers.length, 1, 'old result must not end polling');
  ui.job.screening_running = false;
  ui.job.screening.summary = 'new result';
  await ui.run('load()');
  assert.equal(ui.timers.length, 1, 'finished screening must not schedule another poll');
  assert.match(ui.element('#list').innerHTML, /Re-screen suitability/);
  assert.match(ui.element('#list').innerHTML, /new result/);
  assert.doesNotMatch(ui.element('#list').innerHTML, /activity-spinner/);
});

test('duplicate warnings distinguish confirmed matches and possible repeats with the correct source status', () => {
  const ui = frontend();
  const exact = ui.run("duplicateBadge({first_seen:'2026-10-03', duplicates:[{via:'hiringcafe', first_seen:'2026-10-01', match_type:'exact', app_status:'submitted'}]})");
  assert.match(exact, /seen before on hiring.cafe · submitted/);
  assert.match(exact, /Same employer posting/);
  const possible = ui.run("duplicateBadge({first_seen:'2026-10-03', duplicates:[{via:'hiringcafe', first_seen:'2026-09-01', match_type:'exact'}, {via:'speedyapply', first_seen:'2026-10-01', match_type:'possible', app_status:'submitted'}]})");
  assert.match(possible, /Possible repeat on SpeedyApply · submitted/);
  assert.match(possible, /may be a different requisition/);
});
