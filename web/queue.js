/* Fronta tréninků (train_queue.py) — jedno tlačítko + živý průběh.
 *
 * Zdroj pravdy, že fronta běží, je train_queue.lock (PID uvnitř), který si
 * train_queue.py píše samo — ne stav v téhle stránce ani v paměti serveru.
 * Zavřená stránka nebo restart server.py frontu nezastaví; při dalším
 * otevření se stav znovu načte z /api/train-queue/status.
 */

let plan = null;
let pollTimer = null;

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  const j = await r.json();
  if (j && j.ok === false) throw new Error(j.error || 'chyba serveru');
  return j;
}

function fmtPct(x) { return `${(x * 100).toFixed(1)} %`; }

function fmtElapsed(iso) {
  if (!iso) return '';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m ${String(sec).padStart(2, '0')}s`;
}

const STATE_LABEL = {
  done: 'hotovo', running: 'probíhá', failed: 'SELHALO', interrupted: 'přerušeno',
  missing: 'nové', partial: 'rozdělané',
};

function stateBadge(state, fallback) {
  const s = state || fallback || 'čeká';
  const label = STATE_LABEL[s] || s;
  const cls = (s === 'done' || s === 'hotovo') ? 'ok' : (s === 'failed' ? 'warn' : '');
  return `<span class="model-badge ${cls}" style="display:inline-block; padding:2px 10px;">${label}</span>`;
}

function renderPlan(planData, statusData) {
  const jobStatus = (statusData && statusData.jobs) || {};
  const host = document.getElementById('plan');
  const rows = planData.jobs.map((j, i) => {
    const live = jobStatus[j.key];
    // Živý stav (train_queue_status.json) je přesnější než diskový odhad z
    // /api/train-queue/plan (ten neví o běhu, který právě probíhá) — použij
    // ho, pokud existuje.
    const state = live ? live.state : (j.state === 'hotovo' ? 'done' : (j.state === 'doběhne' ? 'partial' : 'missing'));
    const warn = j.padding_frac > 0.35 ? ' ⚠' : '';
    const steps = live && live.steps ? `${live.steps}/${j.steps}` : String(j.steps);
    return `<tr>
      <td>${i + 1}</td>
      <td>${j.title}, ${j.n} ep</td>
      <td>${steps}</td>
      <td>${fmtPct(j.padding_frac)}${warn}</td>
      <td>${stateBadge(state)}</td>
      <td><code class="inline">${j.out_dir}</code></td>
    </tr>`;
  }).join('');
  host.innerHTML = `
    <p style="color:var(--muted); font-size:13px; margin:0 0 10px;">
      Zdroj dat <code class="inline">${planData.source_slug}</code> → projekt
      <code class="inline">${planData.dest_slug}</code>, chunk_size=${planData.chunk_size},
      tiery ${planData.tiers.join('/')} ep.
    </p>
    <table class="plain">
      <thead><tr><th>#</th><th>cíl</th><th>kroků</th><th>padding</th><th>stav</th><th>výstup</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderProgress(statusData) {
  const progressEl = document.getElementById('progress');
  const logEl = document.getElementById('log-tail');
  const startBtn = document.getElementById('start-btn');
  const stopBtn = document.getElementById('stop-btn');

  startBtn.disabled = statusData.running;
  stopBtn.disabled = !statusData.running;

  if (!statusData.running) {
    const done = Object.values(statusData.jobs || {}).filter((j) => j.state === 'done').length;
    const failed = Object.entries(statusData.jobs || {}).filter(([, j]) => j.state === 'failed');
    const interrupted = Object.entries(statusData.jobs || {}).filter(([, j]) => j.state === 'interrupted');
    const notes = [];
    if (failed.length) notes.push(`<span style="color:var(--red);">${failed.length} selhalo (${failed.map(([k]) => k).join(', ')})</span>`);
    if (interrupted.length) notes.push(`${interrupted.length} přerušeno (${interrupted.map(([k]) => k).join(', ')})`);
    progressEl.innerHTML = Object.keys(statusData.jobs || {}).length
      ? `<p>Fronta neběží. Hotovo ${done}/8.${notes.length ? ` ${notes.join(', ')} — Spustit frontu je zkusí doučit.` : ''}</p>`
      : '<p style="color:var(--muted);">Fronta ještě nikdy neběžela.</p>';
    logEl.style.display = 'none';
    return;
  }

  const cur = statusData.current;
  progressEl.innerHTML = cur
    ? `<p>Fronta běží (PID ${statusData.pid}) — právě: <b>${cur.key}</b>,
        ${cur.target_steps ? `cíl ${cur.target_steps} kroků, ` : ''}běží ${fmtElapsed(cur.started_at)}.</p>`
    : `<p>Fronta běží (PID ${statusData.pid}).</p>`;

  if (cur && cur.log_tail && cur.log_tail.length) {
    logEl.style.display = 'block';
    logEl.textContent = cur.log_tail.join('\n');
    logEl.scrollTop = logEl.scrollHeight;
  } else {
    logEl.style.display = 'none';
  }
}

async function pollStatus() {
  try {
    const statusData = await getJSON('/api/train-queue/status');
    document.getElementById('server-warn').style.display = 'none';
    // Plán (kroky/padding/výstup) se natahuje jen jednou nebo tlačítkem
    // Obnovit — mění se jen tím, že se natrénuje víc epizod/založí nový
    // checkpoint, ne každé 3 s. Živý stav (probíhá/hotovo/selhalo) je ale
    // potřeba přerenderovat do stejné tabulky při každém pollu.
    if (plan) renderPlan(plan, statusData);
    renderProgress(statusData);
  } catch (_) {
    document.getElementById('server-warn').style.display = 'inline';
  }
}

async function loadPlan() {
  const host = document.getElementById('plan');
  host.innerHTML = 'načítám…';
  try {
    const planData = await getJSON('/api/train-queue/plan');
    plan = planData;
    const statusData = await getJSON('/api/train-queue/status').catch(() => ({ ok: true, running: false, jobs: {} }));
    renderPlan(planData, statusData);
    renderProgress(statusData);
    document.getElementById('server-warn').style.display = 'none';
  } catch (e) {
    host.innerHTML = `<p style="color:var(--red);">Nepodařilo se načíst plán: ${e.message}</p>`;
    document.getElementById('server-warn').style.display = 'inline';
  }
}

document.getElementById('start-btn').addEventListener('click', async () => {
  const btn = document.getElementById('start-btn');
  btn.disabled = true;
  try {
    await getJSON('/api/train-queue/start', { method: 'POST' });
    await pollStatus();
  } catch (e) {
    alert(`Nepodařilo se spustit: ${e.message}`);
    btn.disabled = false;
  }
});

document.getElementById('stop-btn').addEventListener('click', async () => {
  if (!confirm('Zastavit frontu? Rozdělaný trénink se přeruší (příští Spustit ho doučí od posledního uloženého checkpointu).')) return;
  const btn = document.getElementById('stop-btn');
  btn.disabled = true;
  try {
    await getJSON('/api/train-queue/stop', { method: 'POST' });
    await pollStatus();
  } catch (e) {
    alert(`Nepodařilo se zastavit: ${e.message}`);
  }
});

document.getElementById('reload-btn').addEventListener('click', () => loadPlan());

loadPlan();
pollTimer = setInterval(pollStatus, 3000);
window.addEventListener('beforeunload', () => clearInterval(pollTimer));
