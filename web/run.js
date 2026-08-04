/* Ovládání běhu orchestrace: pošli instrukci, poslouchej události ze serveru.
 * Server posílá SSE proud (/api/events), typy: log, plan, step, snapshot,
 * telemetry, state, finished. */

let cfg = null;
let planSteps = [];

const el = (id) => document.getElementById(id);

function logLine(level, message) {
  const box = el('log');
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const line = document.createElement('div');
  line.className = level;
  const time = new Date().toLocaleTimeString('cs-CZ');
  line.textContent = `${time}  ${message}`;
  box.appendChild(line);
  while (box.childElementCount > 800) box.removeChild(box.firstChild);
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function renderPlan(marks = {}) {
  const list = el('plan');
  list.innerHTML = '';
  if (!planSteps.length) {
    list.innerHTML = '<li>zatím žádný</li>';
    return;
  }
  planSteps.forEach((step, i) => {
    const li = document.createElement('li');
    li.textContent = `${i + 1}. ${step}`;
    if (marks[i]) li.classList.add(marks[i]);
    list.appendChild(li);
  });
}

const stepMarks = {};

function setState(state) {
  const pill = el('state');
  pill.textContent = state;
  pill.className = `status-pill ${state}`;
  const running = ['PLANNING', 'EXECUTING', 'VERIFYING'].includes(state);
  el('start').disabled = running;
  el('stop').disabled = !running;
}

function showSummary(data) {
  const box = el('summary');
  const ok = data.success;
  const steps = (data.steps || []).map(
    (s) => `<div>${s.success ? '✓' : '✗'} <code class="inline">${s.step}</code> — ${s.tag}${s.reason ? ` · ${s.reason}` : ''}</div>`
  ).join('');
  box.innerHTML = `<b class="${ok ? 'ok' : 'fail'}">${ok ? 'ÚSPĚCH' : 'NEÚSPĚCH'}</b>
    · ${data.duration_s} s · ${(data.steps || []).length} kroků
    ${data.error ? `<div style="color:var(--red);margin-top:6px">${data.error}</div>` : ''}
    <div style="margin-top:8px">${steps}</div>`;
  box.style.color = 'var(--text)';
}

function handleEvent(event) {
  switch (event.type) {
    case 'run_started':
      Object.keys(stepMarks).forEach((k) => delete stepMarks[k]);
      planSteps = [];
      renderPlan();
      el('log').innerHTML = '';
      logLine('EVENT', `▶ Běh spuštěn: „${event.instruction}"`);
      break;
    case 'log':
      logLine(event.level || 'INFO', event.message);
      break;
    case 'plan':
      planSteps = event.steps || [];
      Object.keys(stepMarks).forEach((k) => delete stepMarks[k]);
      renderPlan(stepMarks);
      logLine('EVENT', `Plán${event.replan ? ` (re-plán ${event.replan})` : ''}: [${planSteps.join(', ')}]`);
      break;
    case 'step':
      if (event.phase === 'start') {
        stepMarks[event.index] = 'active';
        logLine('EVENT', `Krok ${event.index + 1}/${event.total}: ${event.step} — model ${event.policy}`);
      } else if (event.phase === 'executed') {
        logLine('INFO', `Krok '${event.step}' ukončen: ${event.reason}`);
      } else if (event.phase === 'verified') {
        stepMarks[event.index] = event.success ? 'ok' : 'fail';
        logLine(event.success ? 'SUCCESS' : 'WARN',
          `Verifikace '${event.step}': ${event.success ? 'úspěch' : `selhání ${event.tag}`}`);
      }
      renderPlan(stepMarks);
      break;
    case 'snapshot':
      el('snapshot').src = `data:image/jpeg;base64,${event.image}`;
      el('snapshot').style.display = 'block';
      el('no-snapshot').style.display = 'none';
      break;
    case 'telemetry':
      el('telemetry').textContent = event.message;
      break;
    case 'state':
      setState(event.state);
      break;
    case 'finished':
      showSummary(event);
      logLine('EVENT', `■ Konec běhu (${event.success ? 'úspěch' : 'neúspěch'}, ${event.duration_s} s)`);
      break;
    default:
      break;
  }
}

function connectEvents() {
  const source = new EventSource('/api/events');
  source.onmessage = (e) => {
    try {
      handleEvent(JSON.parse(e.data));
    } catch (_) { /* neplatná zpráva se ignoruje */ }
  };
  source.onerror = () => logLine('WARN', 'Spojení se serverem přerušeno — zkouším znovu…');
}

function summarizeConfig() {
  const steps = derive.steps(cfg);
  el('cfg-summary').innerHTML = `
    Úloha <b>${cfg.task_slug}</b> · kroky:
    ${steps.map((s) => `<code class="inline">${s.slug}</code>`).join(' → ') || '—'}<br>
    Robot ${cfg.robot_type} na ${cfg.robot_port || '(bez portu → simulace)'} ·
    modely z <code class="inline">${cfg.output_root}/</code> ·
    LM Studio ${cfg.lm_url} (${cfg.llm_model} / ${cfg.vlm_model}).
    Změny se dělají <a href="index.html">v setupu</a>.`;
}

(async function init() {
  cfg = await loadConfig();
  summarizeConfig();
  el('instruction').value = cfg.task_description || '';

  el('start').addEventListener('click', async () => {
    const body = {
      instruction: el('instruction').value,
      skip_planner: el('skip-planner').checked,
      skip_inspector: el('skip-inspector').checked,
    };
    el('start').disabled = true;
    try {
      const resp = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!data.ok) {
        logLine('ERROR', data.error || 'Běh se nepodařilo spustit.');
        el('start').disabled = false;
      }
    } catch (_) {
      logLine('ERROR', 'Server neběží — spusť ho příkazem: python server.py');
      el('start').disabled = false;
    }
  });

  el('stop').addEventListener('click', async () => {
    await fetch('/api/stop', { method: 'POST' }).catch(() => {});
  });

  try {
    const status = await (await fetch('/api/status')).json();
    if (status.running) setState('EXECUTING');
    connectEvents();
  } catch (_) {
    logLine('WARN', 'Server neběží. Stránka je jen popis schématu — běh spustíš po `python server.py`.');
  }
})();
