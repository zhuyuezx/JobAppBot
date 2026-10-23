// Isolated real-DOM regression: JOBFILTER_TEST_BROWSER=1 node --test tests/test_question_browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');

test('application categories preserve questions, collapse completed work, and surface new failures', {
  skip: process.env.JOBFILTER_TEST_BROWSER !== '1',
}, async () => {
  process.env.PLAYWRIGHT_BROWSERS_PATH = path.resolve('data/browser-tools/browsers');
  const {chromium} = require('../data/browser-tools/node_modules/playwright');
  const browser = await chromium.launch({headless: true, channel: 'chromium'});
  try {
    const page = await browser.newPage();
    await page.setContent(readFileSync('jobFilter/static/index.html', 'utf8').replace(/<script.*?<\/script>/gs, ''));
    await page.addStyleTag({content: readFileSync('jobFilter/static/styles.css', 'utf8')});
    await page.addScriptTag({content: `
      const $ = s => document.querySelector(s);
      let tab = 'apps';
      const esc = s => String(s ?? ''), fmt = esc, badge = esc;
      const rowActions = () => '', reviewBadges = () => '', STATUS_LABEL = {};
      const handleRowAction = async () => false;
      let saved = [], releaseSave, rejectSave;
      const record = {job_id:'test', status:'needs_answer', attempts:1,
        job:{title:'Test application', company:'Fictional'}, questions:[
          {id:1, question:'Start date?', status:'open'},
          {id:2, question:'Relocate?', status:'open'},
          {id:3, question:'Portfolio?', status:'open'}]};
      const archived = ['submitted', 'unavailable', 'failed', 'already_applied', 'skipped'].map(status => ({
        job_id: status, status, attempts:1, job:{title:'Example ' + status, company:'Fictional'},
        summary: status === 'failed' ? 'Previous attempt failed' : '', questions:[]
      }));
      async function api(url, body) {
        if (url === '/api/questions/answer') {
          saved.push(body);
          await new Promise((resolve, reject) => { releaseSave = resolve; rejectSave = reject; });
          record.questions.find(q => q.id === body.id).status = 'answered';
          return {requeued:false};
        }
        if (url === '/api/engine') return {error:'no settings in fixture'};
        if (url === '/api/applications') return [{...record, questions:undefined}, ...archived];
        if (!url.endsWith('id=test')) return structuredClone(archived.find(a => url.endsWith('id=' + a.job_id)));
        return structuredClone(record);
      }
    `});
    const source = readFileSync('jobFilter/static/app.js', 'utf8');
    await page.addScriptTag({content: source.split('// ---------------- applications ----------------')[1]
      .split('// ---------------- profile ----------------')[0]});
    await page.evaluate(async () => {
      document.querySelector('#tab-apps').hidden = false;
      await loadApps();
      clearTimeout(appsTimer);
      window.firstInput = document.querySelector('[data-qid="1"] input');
      window.thirdInput = document.querySelector('[data-qid="3"] input');
    });
    await page.locator('[data-qid="1"] input').fill('March');
    await page.locator('[data-qid="2"] input').fill('Yes');
    await page.locator('[data-qid="3"] input').fill('My portfolio');
    await page.locator('[data-qid="2"] [data-act="answer-once"]').click();
    assert.equal(await page.locator('#apps .q').count(), 3);
    assert.equal(await page.locator('[data-qid="2"] [data-act="answer"]').isDisabled(), true);
    await page.locator('[data-qid="1"] input').fill('March 2027');
    await page.evaluate(() => releaseSave());
    await page.waitForFunction(() => document.querySelectorAll('#apps .q').length === 2);
    await page.evaluate(async () => {
      clearTimeout(appsTimer);
      document.activeElement.blur();
      await loadApps();
      clearTimeout(appsTimer);
    });
    assert.equal(await page.evaluate(() => firstInput === document.querySelector('[data-qid="1"] input')), true);
    assert.equal(await page.evaluate(() => thirdInput === document.querySelector('[data-qid="3"] input')), true);
    assert.equal(await page.locator('[data-qid="1"] input').inputValue(), 'March 2027');
    assert.equal(await page.locator('[data-qid="3"] input').inputValue(), 'My portfolio');
    assert.equal(await page.locator('[data-qid="2"]').count(), 0);
    await page.locator('[data-qid="3"] [data-act="answer"]').click();
    await page.evaluate(() => rejectSave(new Error('offline')));
    await page.waitForFunction(() => document.querySelector('[data-qid="3"] [data-answer-status]').textContent.includes('offline'));
    assert.equal(await page.locator('#apps .q').count(), 2);
    assert.equal(await page.locator('[data-qid="3"] input').inputValue(), 'My portfolio');
    // Collapsing and reopening a category must retain drafts, including after a poll.
    await page.locator('[data-app-group-toggle="active"]').click();
    assert.equal(await page.locator('#app-group-active').isVisible(), false);
    await page.evaluate(async () => { await loadApps(); clearTimeout(appsTimer); });
    await page.locator('[data-app-group-toggle="active"]').focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('[data-qid="3"] input'));
    assert.equal(await page.locator('[data-qid="3"] input').inputValue(), 'My portfolio');
    await page.evaluate(async () => {
      record.status = 'submitted'; record.questions = [];
      await loadApps(); clearTimeout(appsTimer);
    });
    assert.equal(await page.locator('#app-group-submitted').isVisible(), false);
    assert.equal(await page.locator('[data-id="test"]').evaluate(el => el.classList.contains('open')), false);
    await page.locator('[data-app-group-toggle="submitted"]').click();
    assert.equal(await page.locator('#app-group-submitted').isVisible(), true);
    await page.locator('[data-id="test"] .row').click();
    await page.waitForFunction(() => document.querySelector('[data-id="test"] [data-log]'));
    assert.equal(await page.locator('[data-id="test"] .details').isVisible(), true);
    await page.locator('[data-app-group-toggle="submitted"]').click();
    await page.evaluate(async () => {
      record.status = 'running'; await loadApps();
      record.status = 'failed'; record.summary = 'Could not reach the application form';
      await loadApps(); clearTimeout(appsTimer);
    });
    assert.equal(await page.locator('#app-group-failed').isVisible(), true);
    assert.equal(await page.locator('[data-id="test"] .details').isVisible(), true);
    assert.equal(await page.locator('#app-group-submitted').isVisible(), false);
    assert.deepEqual(await page.locator('[data-app-group]').evaluateAll(els => els.map(el => el.dataset.appGroup)),
      ['failed', 'submitted', 'already_applied', 'unavailable', 'skipped']);
    await page.locator('[data-app-group-toggle="failed"]').click();
    await page.screenshot({path:'/tmp/jobfilter-application-categories.png', fullPage:true});
  } finally {
    await browser.close();
  }
});
