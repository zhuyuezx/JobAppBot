const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const vm = require('node:vm');

function frontend() {
  const elements = new Map(), calls = [], timers = [];
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, {disabled:false, hidden:true, textContent:'',
      classList:{toggle() {}}, addEventListener() {}});
    return elements.get(selector);
  };
  const context = vm.createContext({$, VIA_LABEL:{}, response:{running:false, status:'idle'},
    setTimeout: (fn, ms) => {timers.push({fn, ms}); return timers.length;}, clearTimeout() {},
    api: async (path, body) => {calls.push({path, body}); return context.response;},
    loadDates: async () => calls.push('dates'), load: async () => calls.push('jobs')});
  vm.runInContext(readFileSync('jobFilter/static/app.js', 'utf8').split('// ---------------- scanning ----------------')[1]
    .split('// ---------------- applications ----------------')[0], context);
  return {context, calls, timers, $, run: code => vm.runInContext(code, context)};
}

test('scan button prevents double clicks, shows progress, and refreshes completed jobs once', async () => {
  const ui = frontend();
  ui.context.response = {running:true, message:'Fetching jobs…'};
  await Promise.all([ui.run('startScan()'), ui.run('startScan()')]);
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0].path, '/api/scan');
  assert.ok(ui.calls[0].body);
  assert.equal(ui.$('#scanNow').disabled, true);
  assert.equal(ui.$('#scanSpinner').hidden, false);
  assert.equal(ui.$('#scanLabel').textContent, 'Scanning…');
  ui.context.response = {running:false, status:'complete', finished_at:'2026-10-06T10:00:00Z', message:'Scan complete · 3 new jobs saved'};
  await ui.run('refreshScan()');
  assert.equal(ui.$('#scanNow').disabled, false);
  assert.equal(ui.$('#scanSpinner').hidden, true);
  assert.match(ui.$('#scanStatus').textContent, /3 new/);
  assert.deepEqual(ui.calls.slice(-2), ['dates', 'jobs']);
  await ui.run('refreshScan()');
  assert.equal(ui.calls.filter(c => c === 'jobs').length, 1);
});

test('idle polling does not repeatedly reload the list; opening mid-scan follows its completion', async () => {
  const ui = frontend();
  await ui.run('refreshScan()'); await ui.run('refreshScan()');
  assert.ok(!ui.calls.includes('jobs'));
  ui.context.response = {running:true, message:'Screening suitability…'};
  await ui.run('refreshScan()');
  assert.equal(ui.$('#scanNow').disabled, true);
  ui.context.response = {running:false, status:'failed', message:'Source unavailable'};
  await ui.run('refreshScan()');
  assert.equal(ui.$('#scanNow').disabled, false);
  assert.equal(ui.$('#scanStatus').textContent, 'Source unavailable');
  assert.ok(ui.calls.includes('jobs'));
});

test('start failures show an actionable error and allow retry', async () => {
  const ui = frontend();
  ui.context.response = {error:'Unable to start scanner'};
  await ui.run('startScan()');
  assert.equal(ui.$('#scanNow').disabled, false);
  assert.match(ui.$('#scanStatus').textContent, /Unable to start scanner/);
  ui.context.response = {running:true, message:'Fetching jobs…'};
  await ui.run('startScan()');
  assert.equal(ui.$('#scanNow').disabled, true);
});
