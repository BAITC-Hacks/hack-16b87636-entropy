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
  // Theme switching must not reload/recompute the displayed forecast.
  const originalChart = d.getElementById('powerChart').innerHTML;
  d.getElementById('themeToggle').click();
  assert.equal(d.documentElement.dataset.theme, 'dark');
  assert.equal(d.getElementById('themeIcon').getAttribute('href'), '#i-sun');
  assert.equal(d.getElementById('powerChart').innerHTML, originalChart);
  d.getElementById('themeToggle').click();
  assert.equal(d.documentElement.dataset.theme, 'light');
  assert.match(d.querySelector('#page-forecast .hero .wind-decor').getAttribute('aria-hidden'), /true/);
  assert.equal(d.querySelectorAll('.wind-decor path').length, 9);
  assert.match(d.getElementById('calendarHint').textContent, /Архивный прогноз погоды доступен только для прошедших дат/);
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
  const calendar = d.getElementById('datePicker');
  calendar.value = '2026-03-01';
  assert.equal(calendar.validity.rangeOverflow, true);
  calendar.dispatchEvent(new window.Event('change'));
  assert.equal(calendar.value, '2026-02-28');
  assert.match(d.getElementById('toast').textContent, /Живой прогноз/);
  assert.match(d.getElementById('originLabel').textContent, /28.*2026/);
  d.getElementById('download').click();
  assert.equal(downloads.length, 1);
  const csv = await downloads[0].text();
  assert.equal(csv.trim().split('\r\n').length, 25);
  assert.match(csv, /weather_issued_at/);
  d.querySelector('.agent-entry[data-page="agent"]').click();
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
  console.log('PASS: real snapshots, switches, date limits/help, CSV, agent entry, quality, honest live refusal, decoration and theme without changing forecast.');
  dom.window.close();

  // Simulated storage: saved preference is applied before the app renders.
  const storage = new Map([['windpilot-theme', 'dark']]);
  const openStored = () => new JSDOM(fs.readFileSync(path.join(__dirname, '../dashboard.html'), 'utf8'), {
    url: 'file:///dashboard.html', runScripts: 'dangerously', virtualConsole,
    beforeParse(w) {
      w.scrollTo = () => {};
      Object.defineProperty(w, 'localStorage', {value: {
        getItem: key => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, String(value)),
      }});
    }
  });
  const first = openStored();
  await wait(20);
  assert.equal(first.window.document.documentElement.dataset.theme, 'dark');
  first.window.document.getElementById('themeToggle').click();
  assert.equal(storage.get('windpilot-theme'), 'light');
  first.window.close();
  const reloaded = openStored();
  await wait(20);
  assert.equal(reloaded.window.document.documentElement.dataset.theme, 'light');
  reloaded.window.close();
  assert.equal(failures.length, 0, failures.join('\n'));
  console.log('PASS: saved theme reload; blocked storage handled in earlier file-mode checks.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
