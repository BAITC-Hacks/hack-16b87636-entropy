"""One-time, exact scoped edits; embedded forecast data stays byte-for-byte intact."""
from pathlib import Path

path = Path('dashboard.html')
html = path.read_text(encoding='utf-8')
snapshot = html.split('/*DATA_START*/', 1)[1].split('/*DATA_END*/', 1)[0]

def replace(old, new):
    global html
    assert old in html, old[:100]
    html = html.replace(old, new)

replace('<html lang="ru">', '<html lang="ru" data-theme="light">')
replace('<meta name="theme-color" content="#075e70">', '''<meta name="theme-color" content="#f4f8fa">
<script>
// Apply the saved theme before first paint; storage may be blocked in file/private mode.
try { const saved=localStorage.getItem('windpilot-theme'); if(saved==='dark'||saved==='light') document.documentElement.dataset.theme=saved; } catch (_) {}
</script>''')
replace('--ink:#173039;--muted:#698087;--teal:#08677a;--blue:#638fa8;', '--text:#173039;--ink:var(--text);--muted:#698087;--accent:#1e6ff2;--accent-hover:#175bd1;--teal:var(--accent);--blue:#638fa8;--card-bg:#fff;--on-accent:#fff;--chart-one:#1e6ff2;--chart-two:#638fa8;--chart-grid:#e4edf1;--chart-grid-secondary:#eff3f5;--chart-label:#5f7d88;--chart-marker:#80a9b9;--chart-marker-bg:#e7f2f6;--chart-marker-text:#276779;')
replace('--paper:#fff;', '--paper:var(--card-bg);')
replace('.btn.primary:hover{background:#064b5d}', '.btn.primary:hover{background:var(--accent-hover)}')
replace('.metric .metric-value{font-weight:330;font-size:49px;letter-spacing:-2.3px;margin:11px 0 8px;color:var(--ink)}', '.metric .metric-value{font-weight:330;font-size:49px;letter-spacing:-2.3px;margin:11px 0 8px;color:var(--accent)}')
replace('.swatch{width:23px;height:0;border-top:2px solid var(--teal)}', '.swatch{width:23px;height:0;border-top:2px solid var(--chart-one)}')
replace('.swatch.blue{border-color:var(--blue)}', '.swatch.blue{border-color:var(--chart-two)}')
css = '''
/* Presentation-only refinements. Light card and background colors are preserved. */
:root{color-scheme:light;--surface-soft:#edf3f6;--surface-active:#dfedf2;--surface-notice:#edf5f8;--surface-context:#edf5f7;--surface-live:#f8fafb;--surface-agent:#e7f1f5;--surface-table:#fafcfc;--surface-row:#edf6f8;--tooltip-bg:#fffffff5;--loading-bg:#f6fafbcc;--subtle-text:#506e7b;--wind-color:#1e6ff2}
:root[data-theme="dark"]{color-scheme:dark;--bg:#101821;--text:#e5edf7;--muted:#a8b9ca;--card-bg:#192432;--accent:#82adff;--accent-hover:#a4c3ff;--on-accent:#10213e;--blue:#b7cedb;--pale:#233447;--line:#344558;--surface-soft:#223145;--surface-active:#2b405e;--surface-notice:#1d3045;--surface-context:#1c2d3e;--surface-live:#172432;--surface-agent:#203247;--surface-table:#1b2939;--surface-row:#21354e;--tooltip-bg:#192432f5;--loading-bg:#101821dd;--subtle-text:#bdcddd;--chart-one:#82adff;--chart-two:#c1d6e3;--chart-grid:#3b4d61;--chart-grid-secondary:#2c3c4f;--chart-label:#b7c8da;--chart-marker:#8ba9cb;--chart-marker-bg:#263e5a;--chart-marker-text:#c9ddff;--wind-color:#82adff}
.rail,.topbar,.context-icon,.btn,.limitations{background:var(--card-bg)}
.wordmark{color:var(--text)}.nav-btn.active{background:var(--surface-active)}
.segmented{background:var(--surface-soft);border-color:var(--line)}.segmented button{color:var(--muted)}
.segmented button.active,.btn.primary,.brand-mark{background:var(--accent);color:var(--on-accent)}
.btn.primary:hover{background:var(--accent-hover);color:var(--on-accent)}
.theme-toggle{width:34px;height:34px;padding:7px;background:transparent;border:1px solid var(--line);border-radius:7px;display:grid;place-items:center;color:var(--accent);flex-shrink:0}
.theme-toggle:hover{background:var(--pale)}.theme-toggle .icon{width:18px;height:18px}
.btn:hover{background:var(--pale)}.btn,.arrow-btn,.step-tag{border-color:var(--line)}
.legend,.chart-caption,.timeline-ticks,.timeline-note,.site-footer,.footer-link,.step p,.live-card p,.context-card p,.limitations,.quality-table th,.step-tag{color:var(--muted)}
.chart-svg text{fill:var(--chart-label)}.weather-badge{background:var(--pale);color:var(--subtle-text)}
.tooltip{background:var(--tooltip-bg);border-color:var(--line)}.chart-summary,.summary-cell+.summary-cell,.quality-table th,.quality-table td,.table-note{border-color:var(--line)}
.context-card{background:var(--surface-context)}.live-card{background:var(--surface-live)}
.notice{background:var(--surface-notice);color:var(--subtle-text)}.agent-summary{background:var(--surface-agent)}
.quality-table th{background:var(--surface-table)}.quality-table tbody tr:first-child{background:var(--surface-row)}
.loading-layer{background:var(--loading-bg)}.step-meta{color:var(--subtle-text)}
.agent-entry{margin-top:14px;border-color:var(--accent);color:var(--accent);padding:10px 13px}
.calendar-help{display:inline-flex;position:relative;align-items:center;gap:4px}
.calendar-help-button{background:none;border:0;color:var(--muted);display:grid;place-items:center;padding:4px;cursor:help}
.calendar-help-button .icon{width:15px;height:15px}
.calendar-tooltip{visibility:hidden;opacity:0;position:absolute;z-index:15;right:0;top:calc(100% + 8px);width:min(310px,60vw);padding:12px 14px;border:1px solid var(--line);border-radius:7px;background:var(--card-bg);color:var(--text);box-shadow:0 6px 24px #0002;font-size:11px;line-height:1.65;transition:opacity .15s}
.calendar-help:hover .calendar-tooltip,.calendar-help:focus-within .calendar-tooltip{visibility:visible;opacity:1}
.calendar-boundary{margin:12px 0 0;font-size:10px;line-height:1.6;color:var(--muted)}
.arrow-btn:disabled{cursor:not-allowed}
#page-forecast>.hero{position:relative;isolation:isolate;overflow:hidden}
#page-forecast>.hero>div{position:relative;z-index:1}
.wind-decor{position:absolute;inset:0 0 0 24%;width:76%;height:100%;z-index:0;pointer-events:none;mask-image:linear-gradient(to right,transparent,#000 48%,#000)}
.wind-decor path{fill:none;stroke:var(--wind-color);stroke-width:1;opacity:.12;animation:wind-drift 14s linear infinite;animation-delay:var(--delay,0s)}
@keyframes wind-drift{0%{transform:translateX(-110px);opacity:0}20%,75%{opacity:.14}100%{transform:translateX(150px);opacity:0}}
:root[data-theme="dark"] .error{background:#302b26;color:#e4cdb3;border-color:#806b52}
:root[data-theme="dark"] .context-card,:root[data-theme="dark"] .live-card,:root[data-theme="dark"] .notice{border-color:var(--line)}
:root[data-theme="dark"] .step:not(:last-child):before{background:var(--line)}
:root[data-theme="dark"] .step-dot{box-shadow:0 0 0 7px var(--pale)}
:root[data-theme="dark"] .timeline-input{background:linear-gradient(to right,#42689a 0%,#42689a var(--progress,0%),#293e56 var(--progress,0%),#293e56 100%)}
:root[data-theme="dark"] .timeline-input::-webkit-slider-thumb{border-color:#294263;box-shadow:0 0 0 1px #557ca9}
:root[data-theme="dark"] .timeline-input::-moz-range-thumb{border-color:#294263}
:root[data-theme="dark"] .summary-cell .number .unit,:root[data-theme="dark"] .nav-btn,:root[data-theme="dark"] .connection.offline,:root[data-theme="dark"] .live-card .eyebrow{color:var(--muted)}
'''
replace('@media(min-width:1600px)', css+'\n@media(min-width:1600px)')
replace('<symbol id="i-wind"', '<symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></symbol>\n<symbol id="i-moon" viewBox="0 0 24 24"><path d="M20.5 14A9 9 0 0 1 10 3.5 9 9 0 1 0 20.5 14Z"/></symbol>\n<symbol id="i-wind"')
replace('<div class="segmented" id="turbineSwitch"', '<button class="theme-toggle" id="themeToggle" type="button" aria-label="Включить тёмную тему" title="Включить тёмную тему" aria-pressed="false"><svg class="icon" aria-hidden="true"><use id="themeIcon" href="#i-moon"/></svg></button>\n<div class="segmented" id="turbineSwitch"')
wind = '<svg class="wind-decor" viewBox="0 0 1000 180" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
for i in range(9):
    y = 10 + i*20
    wind += f'<path style="--delay:-{i*1.5}s" d="M-150 {y} C120 {y-50},260 {y+45},490 {y} S800 {y-35},1160 {y+5}"/>'
wind += '</svg>'
replace('<div class="hero"><div><h1>Прогноз мощности', '<div class="hero">'+wind+'<div><h1>Прогноз мощности')
replace('<button class="btn" id="download">', '<button class="btn primary" id="download">')
replace('<button class="btn" id="liveButton">', '<button class="btn primary" id="liveButton">')
replace('<button class="text-link" data-page="agent">Посмотреть шаги агента', '<button class="btn agent-entry" data-page="agent">Посмотреть шаги агента')
hint = "Архивный прогноз погоды доступен только для прошедших дат. Для сегодняшней даты используйте 'Живой прогноз' ниже."
replace('<input type="date" id="datePicker" min="2026-01-31" max="2026-02-28" value="2026-01-31" aria-label="Дата момента прогноза">', f'''<span class="calendar-help" title="{hint}"><input type="date" id="datePicker" min="2026-01-31" max="2026-02-28" value="2026-01-31" aria-label="Дата момента прогноза" aria-describedby="calendarBoundary calendarHint"><button class="calendar-help-button" type="button" aria-label="Почему даты ограничены" aria-describedby="calendarHint"><svg class="icon" aria-hidden="true"><use href="#i-info"/></svg></button><span class="calendar-tooltip" id="calendarHint" role="tooltip">{hint}</span></span>''')
replace('<div class="range-wrap"><input', '<p class="calendar-boundary" id="calendarBoundary">Бэктест: до 28 февраля 2026 включительно. Для сегодняшней даты — «Живой прогноз» ниже.</p>\n<div class="range-wrap"><input')
replace("const COLORS={1:'#08677a',2:'#638fa8'}, DAY=86400000", "const COLORS={1:'var(--chart-one)',2:'var(--chart-two)'}, DAY=86400000")
for old, new in {'stroke="#e4edf1"':'stroke="var(--chart-grid)"', 'stroke="#eff3f5"':'stroke="var(--chart-grid-secondary)"', 'fill:#5f7d88':'fill:var(--chart-label)', 'stroke="#80a9b9"':'stroke="var(--chart-marker)"', 'fill="#e7f2f6"':'fill="var(--chart-marker-bg)"', 'fill:#276779':'fill:var(--chart-marker-text)', 'stroke="#a2b9c4"':'stroke="var(--chart-marker)"', 'fill="white"':'fill="var(--card-bg)"', 'color:#7a9098':'color:var(--muted)', 'opacity=".42"':'opacity=".68"'}.items():
    replace(old,new)
replace("$('datePicker').addEventListener('change',e=>{if(e.target.value)setDay(Math.round((Date.parse(e.target.value+'T00:00:00Z')-FIRST)/DAY))});", """$('datePicker').addEventListener('change',e=>{const input=e.target;if(!input.value||!input.validity.valid){toast($('calendarHint').textContent);syncDate();return}setDay(Math.round((Date.parse(input.value+'T00:00:00Z')-FIRST)/DAY))});""")
replace('async function init(){syncDate();', '''function applyTheme(theme,persist=false){
document.documentElement.dataset.theme=theme;
const dark=theme==='dark',label=dark?'Включить светлую тему':'Включить тёмную тему';
$('themeToggle').setAttribute('aria-label',label);$('themeToggle').title=label;$('themeToggle').setAttribute('aria-pressed',String(dark));$('themeIcon').setAttribute('href',dark?'#i-sun':'#i-moon');
document.querySelector('meta[name="theme-color"]').content=dark?'#101821':'#f4f8fa';
if(persist)try{localStorage.setItem('windpilot-theme',theme)}catch(_){}
}
$('themeToggle').addEventListener('click',()=>applyTheme(document.documentElement.dataset.theme==='dark'?'light':'dark',true));
async function init(){applyTheme(document.documentElement.dataset.theme);syncDate();''')
assert snapshot == html.split('/*DATA_START*/', 1)[1].split('/*DATA_END*/', 1)[0]
path.write_text(html, encoding='utf-8')
print('Presentation updated; saved forecasts unchanged.')
