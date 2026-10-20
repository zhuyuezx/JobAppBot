// Isolated real-DOM regression: JOBFILTER_TEST_BROWSER=1 node --test tests/test_question_browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');

test('question inputs survive answer submission, failed saves, and background refreshes', {
  skip: process.env.JOBFILTER_TEST_BROWSER !== '1',
}, async () => {
  process.env.PLAYWRIGHT_BROWSERS_PATH = path.resolve('data/browser-tools/browsers');
  const {chromium} = require('../data/browser-tools/node_modules/playwright');
  const browser = await chromium.launch({headless: true, channel: 'chromium'});
  try {
    const page = await browser.newPage();
    await page.setContent(readFileSync('jobFilter/static/index.html', 'utf8').replace(/<script.*?<\/script>/gs, ''));
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
      async function api(url, body) {
        if (url === '/api/questions/answer') {
          saved.push(body);
          await new Promise((resolve, reject) => { releaseSave = resolve; rejectSave = reject; });
          record.questions.find(q => q.id === body.id).status = 'answered';
          return {requeued:false};
        }
        if (url === '/api/engine') return {error:'no settings in fixture'};
        if (url === '/api/applications') return [{...record, questions:undefined}];
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
  } finally {
    await browser.close();
  }
});
