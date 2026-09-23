/* DOM behavior checks, not a replacement for visual browser QA. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('../artifacts/ui_checks/node_modules/jsdom');

async function main() {
  const failures = [], downloads = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', e => failures.push(e.message));
  const dom = new JSDOM(fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8'), {
    url: 'file:///dashboard.html', runScripts: 'dangerously', virtualConsole,
    beforeParse(window) {
      window.scrollTo = () => {};
      window.Blob = global.Blob;
      window.URL.createObjectURL = blob => { downloads.push(blob); return 'blob:test'; };
      window.URL.revokeObjectURL = () => {};
      window.HTMLAnchorElement.prototype.click = () => {};
      window.HTMLElement.prototype.scrollIntoView = () => {};
    }
  });
  const { window } = dom, d = window.document;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  await wait(30);
  assert.equal(failures.length, 0, failures.join('\n'));
  assert.match(d.getElementById('originLabel').textContent, /31.*2026/);
  assert.match(d.getElementById('countValue').textContent, /96/);
  assert.equal(d.querySelectorAll('#powerChart path').length, 2);
  assert.equal(d.getElementById('dataNotice').hidden, false);
  assert.equal(d.getElementById('connectionText').textContent, 'Офлайн');
  d.querySelector('[data-turbine="1"]').click();
  assert.equal(d.querySelectorAll('#powerChart path').length, 1);
  assert.match(d.getElementById('countValue').textContent, /48/);
  d.querySelector('[data-horizon="24"]').click();
  await wait(20);
  assert.match(d.getElementById('countValue').textContent, /24/);
  assert.equal(d.getElementById('horizonTitle').textContent, '24 часа');
  d.getElementById('daySlider').value = '28';
  d.getElementById('daySlider').dispatchEvent(new window.Event('change'));
  // A browser normally emits input before change.
  d.getElementById('daySlider').dispatchEvent(new window.Event('input'));
  await wait(260);
  assert.equal(d.getElementById('datePicker').value, '2026-02-28');
  assert.match(d.getElementById('originLabel').textContent, /28.*2026/);
  assert.equal(d.getElementById('errorNotice').hidden, true);
  d.getElementById('download').click();
  assert.equal(downloads.length, 1);
  const csv = await downloads[0].text();
  assert.equal(csv.trim().split('\r\n').length, 25);
  assert.match(csv, /weather_issued_at/);
  d.querySelector('.nav-btn[data-page="agent"]').click();
  assert.equal(d.getElementById('page-agent').hidden, false);
  assert.equal(d.querySelectorAll('#agentSteps .step').length, 4);
  d.querySelector('.nav-btn[data-page="quality"]').click();
  assert.equal(d.getElementById('page-quality').hidden, false);
  assert.equal(d.querySelectorAll('#qualityTable tr').length, 2);
  assert.match(d.getElementById('qualityTable').textContent, /0,2093/);
  d.querySelector('[data-turbine="both"]').click();
  assert.equal(d.querySelectorAll('#qualityTable tr').length, 2);
  d.getElementById('liveButton').click();
  assert.match(d.getElementById('toast').textContent, /через сервер/);
  assert.equal(d.getElementById('liveNotice').hidden, true);
  assert.equal(failures.length, 0, failures.join('\n'));
  console.log('PASS: real embedded data, turbine/horizon switches, final timeline day, CSV, all three screens, quality table, honest offline live-mode refusal.');
  dom.window.close();
}
main().catch(error => { console.error(error); process.exitCode = 1; });
