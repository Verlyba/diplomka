/* Stránka Setup — formulář a z něj generované příkazy.
 *
 * Jediná logika, kterou stránka má: vezmi konfiguraci, poskládej z ní přesné
 * příkazové řádky a dej k nim tlačítko „kopírovat". Nic nespouští. */

let cfg = null;
let modelStatus = null; // { baseline: {trained, steps, path}, steps: {slug: {...}} } — z /api/models

/** Nastaví text/třídu/title existujícího <span class="model-badge"> podle
 *  stavu checkpointu — mutace na místě, ne vkládání nového <span> přes
 *  innerHTML (to by ho vnořilo do sebe sama). Stejná cesta, co by načetl
 *  daemon (viz orchestrator.checkpoint_status) — appka a live běh se tu
 *  nemůžou rozejít.
 *
 *  Fajfka = natrénováno na (aspoň) aktuálně nastavený cíl. Křížek = buď
 *  žádný checkpoint, nebo existuje, ale má míň kroků, než je teď v poli
 *  „Kroků tréninku" — zvýšil jsi cíl a ještě jsi to nedotrénoval. */
function applyStatusBadge(el, info) {
  if (!info) {
    el.textContent = '…'; el.className = 'model-badge'; el.title = 'Stav se zjišťuje…';
    return;
  }
  const target = info.target_steps != null ? ` / cíl ${info.target_steps}` : '';
  if (info.sufficient) {
    el.textContent = '✓'; el.className = 'model-badge ok';
    el.title = `${info.path}\nnatrénováno: ${info.steps} kroků${target}`;
  } else if (info.trained) {
    el.textContent = '✗'; el.className = 'model-badge warn';
    el.title = `${info.path}\nnatrénováno jen ${info.steps} kroků${target} — dotrénovat`;
  } else {
    el.textContent = '✗'; el.className = 'model-badge warn';
    el.title = `${info.path}\nnetrénováno${target}`;
  }
}

async function refreshModelStatus() {
  try {
    const resp = await fetch('/api/models');
    if (!resp.ok) return;
    modelStatus = await resp.json();
  } catch (_) {
    return; // server neběží / cesta nedostupná — badge zůstane "zjišťuje se"
  }
  document.querySelectorAll('.step-row [data-badge-for]').forEach((el) => {
    applyStatusBadge(el, modelStatus.steps[el.dataset.badgeFor]);
  });
  document.querySelectorAll('[data-badge-for="__baseline__"]').forEach((el) => {
    applyStatusBadge(el, modelStatus.baseline);
  });
  const legend = document.getElementById('model-status-legend');
  if (legend) legend.style.display = '';
}

/* ── Formulář ──────────────────────────────────────────────────────────── */

function fillForm() {
  document.querySelectorAll('[data-key]').forEach((el) => {
    const value = cfg[el.dataset.key];
    if (value === undefined) return;
    if (el.type === 'checkbox') el.checked = Boolean(value);
    else el.value = value;
  });
  document.getElementById('boundaries').placeholder =
    Array.from({ length: Math.max(derive.steps(cfg).length - 1, 1) },
      (_, i) => (4 + i * 5).toFixed(1)).join(',');
  renderSteps();
  render();
  isFormDirty = false;
}

function readForm() {
  document.querySelectorAll('[data-key]').forEach((el) => {
    if (el.type === 'checkbox') cfg[el.dataset.key] = el.checked;
    else if (el.type === 'number') cfg[el.dataset.key] = Number(el.value);
    else cfg[el.dataset.key] = el.value;
  });
}

function renderSteps() {
  const host = document.getElementById('steps-editor');
  host.innerHTML = '';
  (cfg.steps || []).forEach((step, i) => {
    const row = document.createElement('div');
    row.className = 'step-row';
    const isCustomPolicy = Boolean(step.policy_path);
    const docBtnClass = isCustomPolicy ? 'active-custom' : '';
    const docBtnTitle = isCustomPolicy ? `Zvolen vlastní checkpoint: ${step.policy_path}` : 'Správa modelů a nasekaných datasetů pro tento krok';

    row.innerHTML = `
      <div class="idx">${i + 1}.</div>
      <input class="slug" type="text" placeholder="slug_kroku" value="${escapeAttr(step.slug || '')}"
        title="ID dovednosti pro LLM plánovač; jde i do jmen datasetu a checkpointu">
      <input class="desc" type="text" placeholder="popis akce → dataset + LLM plánovač"
        title="Anotace datasetu a popis dovednosti pro LLM plánovače. Záložní text pro VLM
inspektora, pokud níže nevyplníš cílový stav."
        value="${escapeAttr(step.description || '')}">
      <label class="checkline" title="→ LLM plánovač (popis dovednosti) a daemon (protokol B — proud gripperu)">
        <input class="grasp" type="checkbox" ${step.grasp ? 'checked' : ''}> úchop
      </label>
      <span class="model-badge" data-badge-for="${escapeAttr(step.slug || '')}" title="Stav se zjišťuje…">…</span>
      <button type="button" class="btn-doc btn-step-modal ${docBtnClass}" title="${escapeAttr(docBtnTitle)}">📄</button>
      <button type="button" class="btn-remove-step" title="Odebrat krok">✕</button>
      <div class="idx"></div>
      <input class="hint" type="text" placeholder="cílový stav → jen VLM inspektor (nepovinné, jinak se použije popis)"
        title="→ VLM inspektor. Popis kroku výše je AKCE („nájezd nad kostku“), inspektor ale
potřebuje pozorovatelný VÝSLEDEK („gripper je přímo nad kostkou, čelisti otevřené“)."
        value="${escapeAttr(step.verify_hint || '')}">
      <input class="train-steps" type="number" min="1" step="500" placeholder="výchozí"
        title="Kroků tréninku pro tento krok — prázdné = použije se globální „Kroků tréninku" níže.
Podle tohohle čísla se i pozná, jestli je checkpoint hotový (fajfka), nebo jen částečně
natrénovaný (křížek) — appka porovnává, kolik kroků checkpoint skutečně má."
        value="${step.train_steps != null ? step.train_steps : ''}">`;
    row.querySelector('.slug').addEventListener('input', (e) => {
      cfg.steps[i].slug = e.target.value.trim(); render(); isFormDirty = true;
    });
    row.querySelector('.desc').addEventListener('input', (e) => {
      cfg.steps[i].description = e.target.value; render(); isFormDirty = true;
    });
    row.querySelector('.grasp').addEventListener('change', (e) => {
      cfg.steps[i].grasp = e.target.checked; isFormDirty = true;
    });
    row.querySelector('.hint').addEventListener('input', (e) => {
      const v = e.target.value.trim();
      if (v === '') delete cfg.steps[i].verify_hint;
      else cfg.steps[i].verify_hint = v;
      isFormDirty = true;
    });
    row.querySelector('.train-steps').addEventListener('input', (e) => {
      const v = e.target.value.trim();
      if (v === '') delete cfg.steps[i].train_steps;
      else cfg.steps[i].train_steps = Number(v);
      render(); isFormDirty = true;
    });
    row.querySelector('.btn-step-modal').addEventListener('click', () => {
      openModelModal(cfg.steps[i].slug);
    });
    row.querySelector('.btn-remove-step').addEventListener('click', () => {
      cfg.steps.splice(i, 1); renderSteps(); render(); isFormDirty = true;
    });
    host.appendChild(row);
  });
  refreshModelStatus();
}

function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

/* ── Vykreslení příkazu ────────────────────────────────────────────────── */

function cmdCard(title, desc, command) {
  const wrap = document.createElement('div');
  wrap.className = 'cmd';
  wrap.innerHTML = `
    <div class="cmd-head"><span class="title">${title}</span><span class="desc">${desc || ''}</span></div>
    <div class="cmd-box"><pre></pre><button class="copy" type="button">kopírovat</button></div>`;
  wrap.querySelector('pre').textContent = command;
  const button = wrap.querySelector('button');
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(command);
    } catch (_) {
      const range = document.createRange();
      range.selectNodeContents(wrap.querySelector('pre'));
      const sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(range);
      document.execCommand('copy');
    }
    button.textContent = 'zkopírováno';
    button.classList.add('done');
    setTimeout(() => { button.textContent = 'kopírovat'; button.classList.remove('done'); }, 1400);
  });
  return wrap;
}

function fill(hostId, cards) {
  const host = document.getElementById(hostId);
  host.innerHTML = '';
  cards.forEach(([title, desc, command]) => host.appendChild(cmdCard(title, desc, command)));
}

/* Argument s mezerami se uzavírá celý do uvozovek — takový tvar projde
 * v PowerShellu, cmd.exe i bashi, takže se nemusí generovat tři varianty. */
function arg(name, value) {
  const token = `--${name}=${value}`;
  return /[\s{}"]/.test(String(value)) ? `"${token}"` : token;
}

/* ── Generování všech příkazů ──────────────────────────────────────────── */

/** Ukáže bloky patřící ke zvolené strategii sběru dat a skryje ty druhé. */
function applyStrategy() {
  const strategy = cfg.data_strategy === 'merge' ? 'merge' : 'split';
  document.querySelectorAll('[data-only]').forEach((el) => {
    const isInline = el.tagName === 'SPAN';
    el.style.display = el.dataset.only === strategy ? (isInline ? 'inline' : '') : 'none';
  });
  document.querySelectorAll('[data-strategy]').forEach((el) => {
    el.checked = el.value === strategy;
  });
  document.getElementById('strategy-note').innerHTML = strategy === 'split'
    ? `Jedna nahrávka je <b>metodicky bezpečnější</b>: dílčí datasety jsou doslova výřezy týchž
       epizod, takže baseline i kroky vidí identické snímky a nikdo nemůže namítnout, že se
       porovnávají dvě různá data.`
    : `Pozor na jedno: složená epizoda má na spojích <b>švy</b> (mezi kroky se nahrávání zastavilo).
       Baseline se tedy učí z trajektorie, která nikdy nevznikla jedním plynulým pohybem — to je
       nevýhoda, kterou orchestrace nemá. Když to jde, zvol první variantu.`;
}

function render() {
  const c = cfg;
  const py = c.python || 'python';
  const steps = derive.steps(c);
  const cameras = derive.camerasArg(c);
  applyStrategy();

  // 1) instalace
  fill('cmds-install', [
    ['Vytvoření prostředí', 'jednou; potom už jen aktivace', `python -m venv .venv-lerobot`],
    ['Instalace LeRobota', 'dataset = datasety, training = trénink (accelerate), feetech = motory SO-100/101',
      `${py} -m pip install "lerobot[dataset,training,feetech]"`],
    ['Kontrola', 'vypíše verzi, když je vše na místě',
      `${py} -c "import lerobot; print(lerobot.__version__)"`],
  ]);

  // 2) detekce
  fill('cmds-detect', [
    ['Sériové porty', 'vypíše port ramene, které odpojíš a zase připojíš',
      `${py} -m lerobot.scripts.lerobot_find_port`],
    ['Kamery', 'vypíše indexy a rozlišení připojených kamer',
      `${py} -m lerobot.scripts.lerobot_find_cameras opencv`],
  ]);

  // 2) kalibrace
  fill('cmds-calib', [
    ['Kalibrace leader ramene', 'ovládací (pasivní) rameno',
      `${py} -m lerobot.scripts.lerobot_calibrate ${arg('teleop.type', c.teleop_type)} ${arg('teleop.port', c.teleop_port)} ${arg('teleop.id', c.teleop_id)}`],
    ['Kalibrace follower ramene', 'rameno, které se opravdu hýbe',
      `${py} -m lerobot.scripts.lerobot_calibrate ${arg('robot.type', c.robot_type)} ${arg('robot.port', c.robot_port)} ${arg('robot.id', c.robot_id)}`],
  ]);

  // 3) teleoperace
  const teleop = [`${py} -m lerobot.scripts.lerobot_teleoperate`,
    arg('robot.type', c.robot_type), arg('robot.port', c.robot_port), arg('robot.id', c.robot_id),
    arg('teleop.type', c.teleop_type), arg('teleop.port', c.teleop_port), arg('teleop.id', c.teleop_id)];
  if (cameras) teleop.push(arg('robot.cameras', cameras), '--display_data=true');
  fill('cmds-teleop', [
    ['Teleoperace', cameras ? 'včetně živého náhledu kamery (vyžaduje rerun-sdk)' : 'bez kamery',
      teleop.join(' ')],
  ]);

  // 4) nahrávání — přes wrapper, který navíc zapisuje značky hranic kroků
  const record = [`${py} record_with_marks.py`,
    arg('robot.type', c.robot_type), arg('robot.port', c.robot_port), arg('robot.id', c.robot_id),
    arg('teleop.type', c.teleop_type), arg('teleop.port', c.teleop_port), arg('teleop.id', c.teleop_id),
    arg('dataset.repo_id', derive.baselineRepo(c)),
    arg('dataset.single_task', c.task_description),
    arg('dataset.num_episodes', c.episodes),
    arg('dataset.episode_time_s', c.episode_time_s),
    arg('dataset.reset_time_s', c.reset_time_s),
    arg('dataset.fps', c.fps),
    // Bez no_stamp si LeRobot k názvu datasetu přilepí datum a čas — pak by
    // ho nenašel ani splitter, ani trénink podle jména z této stránky.
    '--dataset.no_stamp=true',
    '--dataset.push_to_hub=false',
    // Průběžné kódování videa drží nahrávací smyčku na stabilních FPS
    '--dataset.streaming_encoding=true', arg('dataset.encoder_threads', 2)];
  if (cameras) record.push(arg('robot.cameras', cameras));
  fill('cmds-record', [
    [`Nahrání ${c.episodes} demonstrací`,
      `dataset ${derive.baselineRepo(c)} + značky hranic kroků (mezerník)`, record.join(' ')],
    ['Dodatečné epizody do stejného datasetu',
      `stejný příkaz s --resume; přibude ${c.resume_episodes || c.episodes} epizod; --dataset.root je u resume povinný`,
      `${record.join(' ').replace(/--dataset\.num_episodes=\S+/, arg('dataset.num_episodes', c.resume_episodes || c.episodes))} --resume=true ${arg('dataset.root', derive.datasetRoot(c))}`],
    ['Nahrávání bez značek', 'čistý lerobot-record, když značky dělat nechceš',
      record.join(' ').replace(`${py} record_with_marks.py`, `${py} -m lerobot.scripts.lerobot_record`)],
  ]);

  // 5b) varianta „dílčí nahrávky" — jeden příkaz na každý krok
  fill('cmds-record-steps', steps.map((s, i) => {
    const cmd = record.slice();
    cmd[0] = `${py} -m lerobot.scripts.lerobot_record`;   // značky tu netřeba
    const at = cmd.findIndex((a) => a.startsWith('--dataset.repo_id='));
    cmd[at] = arg('dataset.repo_id', derive.stepRepo(c, s.slug));
    const taskAt = cmd.findIndex((a) => a.includes('--dataset.single_task='));
    cmd[taskAt] = arg('dataset.single_task', s.description || s.slug.replace(/_/g, ' '));
    return [`Krok ${i + 1}: ${s.slug}`, `dataset ${derive.stepRepo(c, s.slug)}`, cmd.join(' ')];
  }));

  const mergeCmd = [`${py} merge_datasets.py`,
    arg('task', c.task_slug), arg('steps', steps.map((s) => s.slug).join(',')),
    arg('single-task', c.task_description)].join(' ');
  fill('cmds-merge', [
    ['Sloučení kroků do celé úlohy', `vytvoří ${derive.baselineRepo(c)} + sidecar se švy`, mergeCmd],
  ]);
  const mergeTarget = document.getElementById('merge-target');
  if (mergeTarget) mergeTarget.textContent = `${derive.baselineRepo(c)}.`;

  // 6) rozdělení
  const boundaries = (document.getElementById('boundaries').value || '').trim();
  document.getElementById('split-count').textContent =
    steps.length ? `${steps.length} dílčích datasetů: ${steps.map((s) => derive.stepRepo(c, s.slug)).join(', ')}.`
      : 'zatím nic — nejdřív přidej kroky.';
  // Hranic je vždy o jednu méně než kroků — na tomhle stojí celé rozdělení,
  // tak ať to číslo uživatel vidí přímo u nahrávání.
  const needed = Math.max(steps.length - 1, 0);
  const neededText = steps.length
    ? `${needed} (kroků ${steps.length}: ${steps.map((s) => s.slug).join(' → ')}).`
    : '— nejdřív přidej kroky v konfiguraci.';
  document.getElementById('marks-needed').textContent = neededText;
  document.getElementById('marks-needed-2').textContent = steps.length ? `${needed}` : '(počet kroků − 1)';
  const split = [`${py} split_dataset.py`,
    arg('repo-id', derive.baselineRepo(c)),
    arg('steps', steps.map((s) => s.slug).join(','))];
  const splitFixed = split.concat(
    arg('boundaries', boundaries || document.getElementById('boundaries').placeholder));
  fill('cmds-split', [
    ['Rozdělení podle značek z nahrávání', 'sidecar soubor u datasetu si skript najde sám',
      split.join(' ')],
    ['Rozdělení podle pevných časů', 'když značky nemáš — stejné hranice pro všechny epizody',
      splitFixed.join(' ')],
  ]);

  const timeouts = [`${py} compute_step_timeouts.py`,
    arg('repo-id', derive.baselineRepo(c)),
    arg('steps', steps.map((s) => s.slug).join(',')), '--apply'];
  fill('cmds-timeouts', [
    ['Časové limity kroků ze skutečných dat', 'spočítá z marks.json a zapíše timeout_s do config.json',
      timeouts.join(' ')],
  ]);

  // 6) trénink
  const trainFlags = (repo, job, out, trainSteps) => {
    const steps = trainSteps || c.train_steps;
    // save_freq vyšší než cílové kroky by neuložilo žádný checkpoint —
    // ohlídat to tu, ne až v chybové hlášce lerobot-train.
    const saveFreq = Math.min(c.save_freq, steps);
    return [`${py} -m lerobot.scripts.lerobot_train`,
      arg('policy.type', c.policy_type), arg('dataset.repo_id', repo),
      arg('steps', steps), arg('batch_size', c.batch_size),
      arg('save_freq', saveFreq), arg('job_name', job),
      arg('policy.device', c.device), '--wandb.enable=false',
      arg('output_dir', out), '--policy.push_to_hub=false'].join(' ');
  };

  fill('cmds-train-baseline', [
    [`Baseline ${c.policy_type.toUpperCase()}`, `celý dataset → ${derive.baselineOut(c)}`,
      trainFlags(derive.baselineRepo(c), c.task_slug, derive.baselineOut(c))],
  ]);

  fill('cmds-train-steps', steps.map((s, i) => [
    `Krok ${i + 1}: ${s.slug}` + (s.train_steps ? ` (${s.train_steps} kroků)` : ''),
    `${derive.stepRepo(c, s.slug)} → ${derive.stepOut(c, s.slug)}`,
    trainFlags(derive.stepRepo(c, s.slug), `${c.task_slug}_${s.slug}`, derive.stepOut(c, s.slug), s.train_steps),
  ]));

  // 7) baseline běh
  const baseline = [`${py} inference_daemon.py`,
    arg('robot.type', c.robot_type), arg('robot.port', c.robot_port), arg('robot.id', c.robot_id),
    arg('policy.path', derive.baselineLoadPath(c)),
    arg('device', c.device), arg('fps', c.fps),
    '--no-triggers', arg('max-seconds', 60)];
  // inference_daemon.py má vlastní argparse, ne draccus — --robot.cameras tam
  // (na rozdíl od teleop/record/rollout) čeká striktní JSON, ne tenhle zápis
  // bez uvozovek. Bezpečnější jsou jeho jednotlivé --camera./--camera2. flagy
  // (bez závorek a uvozovek, nemusí se nic escapovat).
  if (c.camera_name) {
    baseline.push(arg('camera.name', c.camera_name), arg('camera.index', c.camera_index),
      arg('camera.width', c.camera_width), arg('camera.height', c.camera_height),
      arg('camera.fps', c.camera_fps));
  }
  if (c.camera2_name) {
    baseline.push(arg('camera2.name', c.camera2_name), arg('camera2.index', c.camera2_index),
      arg('camera2.width', c.camera2_width), arg('camera2.height', c.camera2_height),
      arg('camera2.fps', c.camera2_fps));
  }
  fill('cmds-baseline-run', [
    ['Baseline inference_daemon.py', 'daemon čeká na stdin po „DAEMON_READY"',
      baseline.join(' ')],
  ]);

  // 8) serve
  const serve = [`${c.python || 'python'} server.py`];
  fill('cmds-serve', [
    ['Spuštění serveru', 'obsluhuje web + orchestraci; LeRobot musí být dostupný v tomto Pythonu',
      serve.join(' ')],
  ]);
}



let isFormDirty = false;

/* ── Start ─────────────────────────────────────────────────────────────── */

(async function init() {
  cfg = await loadConfig();
  fillForm();

  document.querySelectorAll('[data-key]').forEach((el) => {
    const evt = el.type === 'checkbox' ? 'change' : 'input';
    el.addEventListener(evt, () => { readForm(); render(); isFormDirty = true; });
  });
  document.getElementById('boundaries').addEventListener('input', () => { render(); isFormDirty = true; });

  document.querySelectorAll('[data-strategy]').forEach((el) => {
    el.addEventListener('change', () => { cfg.data_strategy = el.value; render(); isFormDirty = true; });
  });

  document.getElementById('add-step').addEventListener('click', () => {
    cfg.steps = cfg.steps || [];
    cfg.steps.push({ slug: '', description: '', grasp: false });
    renderSteps(); render();
    isFormDirty = true;
  });

  document.getElementById('save').addEventListener('click', async () => {
    readForm();
    const ok = await saveConfig(cfg);
    isFormDirty = false;
    const status = document.getElementById('save-status');
    status.textContent = ok
      ? 'Uloženo do prohlížeče i do config.json.'
      : 'Uloženo do prohlížeče. Server neběží, config.json se nezapsal.';
    setTimeout(() => { status.textContent = ''; }, 4000);
  });

  setupModalEvents();
  await fetchProjects();
  setupProjectEvents();

  const refreshDsBtn = document.getElementById('btn-refresh-datasets');
  if (refreshDsBtn) refreshDsBtn.addEventListener('click', refreshDatasetOverview);
  const chkDsAll = document.getElementById('chk-datasets-all');
  if (chkDsAll) chkDsAll.addEventListener('change', refreshDatasetOverview);
  refreshDatasetOverview();
})();

/* ── Přehled datasetů na disku (local/*, sekce 5) ─────────────────────── */

let localDatasets = [];
// Vybrané epizody ke smazání, per dataset: { "local/test_2": Set([1, 4]) }
const selectedEpisodes = {};

async function refreshDatasetOverview() {
  const host = document.getElementById('dataset-overview');
  if (!host) return;
  const slugEl = document.getElementById('dataset-overview-slug');
  if (slugEl) slugEl.textContent = cfg.task_slug || 'task';
  const showAll = document.getElementById('chk-datasets-all');
  const query = showAll && showAll.checked ? '?all=1' : '';
  try {
    const resp = await fetch('/api/datasets/local' + query);
    if (!resp.ok) return;
    localDatasets = await resp.json();
  } catch (_) {
    host.innerHTML = '<div class="dataset-empty">Server neběží — přehled datasetů nejde načíst.</div>';
    return;
  }
  renderDatasetOverview();
}

function renderDatasetOverview() {
  const host = document.getElementById('dataset-overview');
  if (!host) return;
  if (!localDatasets.length) {
    const showAll = document.getElementById('chk-datasets-all');
    host.innerHTML = (showAll && showAll.checked)
      ? '<div class="dataset-empty">V local/ zatím vůbec žádný dataset.</div>'
      : `<div class="dataset-empty">Pro projekt "${escapeAttr(cfg.task_slug || '')}" zatím žádný dataset —
         nahraj demonstrace příkazem výše, nebo zaškrtni "zobrazit i ostatní projekty" výš.</div>`;
    return;
  }
  const list = document.createElement('div');
  list.className = 'dataset-list';
  localDatasets.forEach((ds) => {
    list.appendChild(datasetRow(ds));
  });
  host.innerHTML = '';
  host.appendChild(list);
}

function datasetRow(ds) {
  const row = document.createElement('div');
  row.className = 'dataset-row';

  const head = document.createElement('div');
  head.className = 'dataset-row-head';
  const episodesText = ds.episodes != null ? `${ds.episodes} epizod` : 'počet epizod se nepodařilo přečíst';
  const framesText = ds.frames != null ? ` · ${ds.frames.toLocaleString('cs-CZ')} snímků` : '';
  const fpsText = ds.fps != null ? ` · ${ds.fps} fps` : '';
  head.innerHTML = `
    <span class="chevron">▸</span>
    <span class="name">${escapeAttr(ds.repo_id)}</span>
    <span class="meta">${episodesText}${framesText}${fpsText}</span>
    ${ds.has_marks ? '<span class="marks-badge">.marks.json</span>' : ''}
    <button type="button" class="warn ds-del-dataset" style="padding:4px 10px;font-size:12px">🗑 Smazat dataset</button>
  `;
  head.querySelector('.ds-del-dataset').addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm(`Opravdu smazat celý dataset "${ds.repo_id}" (${episodesText})? Nevratné.`)) return;
    try {
      const resp = await fetch('/api/datasets/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_id: ds.repo_id }),
      });
      const res = await resp.json();
      if (res.ok) { delete selectedEpisodes[ds.repo_id]; await refreshDatasetOverview(); }
      else alert('Smazání selhalo: ' + (res.error || 'neznámá chyba'));
    } catch (err) { alert('Smazání selhalo: ' + err); }
  });
  head.addEventListener('click', () => {
    row.classList.toggle('open');
    if (row.classList.contains('open')) renderEpisodeBody(row, ds);
  });

  const body = document.createElement('div');
  body.className = 'dataset-row-body';

  row.appendChild(head);
  row.appendChild(body);
  return row;
}

function renderEpisodeBody(row, ds) {
  const body = row.querySelector('.dataset-row-body');
  if (!ds.episodes) {
    body.innerHTML = '<div class="dataset-empty">Počet epizod se nepodařilo zjistit (chybí meta/info.json).</div>';
    return;
  }
  if (!selectedEpisodes[ds.repo_id]) selectedEpisodes[ds.repo_id] = new Set();
  const selected = selectedEpisodes[ds.repo_id];

  const warn = ds.has_marks
    ? `<div class="note warn" style="margin:0 0 10px 0">Dataset má <code class="inline">.marks.json</code> —
       smazání epizod posune indexy zbylých a značky hranic kroků přestanou sedět. Po smazání je
       nutné značky ručně přemapovat (nebo znovu spočítat hranice).</div>`
    : '';

  const grid = document.createElement('div');
  grid.className = 'episode-grid';
  for (let i = 0; i < ds.episodes; i++) {
    const chip = document.createElement('span');
    chip.className = 'episode-chip' + (selected.has(i) ? ' checked' : '');
    chip.textContent = `#${i}`;
    chip.addEventListener('click', () => {
      if (selected.has(i)) selected.delete(i); else selected.add(i);
      chip.classList.toggle('checked');
      countBtn.textContent = `🗑 Smazat vybrané epizody (${selected.size})`;
      countBtn.disabled = selected.size === 0;
    });
    grid.appendChild(chip);
  }

  const countBtn = document.createElement('button');
  countBtn.type = 'button';
  countBtn.className = 'warn';
  countBtn.style.cssText = 'padding:5px 12px;font-size:12px';
  countBtn.disabled = selected.size === 0;
  countBtn.textContent = `🗑 Smazat vybrané epizody (${selected.size})`;
  countBtn.addEventListener('click', async () => {
    const indices = Array.from(selected).sort((a, b) => a - b);
    if (!indices.length) return;
    if (!confirm(`Smazat epizody [${indices.join(', ')}] z "${ds.repo_id}"? LeRobot zbylé přeindexuje ` +
      `a originál zálohuje do ${ds.name}_old. Může to chvíli trvat.`)) return;
    countBtn.disabled = true;
    countBtn.textContent = 'Mažu… (může trvat i několik minut)';
    try {
      const resp = await fetch('/api/datasets/delete-episodes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_id: ds.repo_id, episode_indices: indices }),
      });
      const res = await resp.json();
      if (res.ok) {
        delete selectedEpisodes[ds.repo_id];
        await refreshDatasetOverview();
      } else {
        alert('Smazání epizod selhalo: ' + (res.error || 'neznámá chyba'));
        countBtn.disabled = false;
        countBtn.textContent = `🗑 Smazat vybrané epizody (${indices.length})`;
      }
    } catch (err) {
      alert('Smazání epizod selhalo: ' + err);
      countBtn.disabled = false;
    }
  });

  body.innerHTML = warn;
  body.appendChild(grid);
  body.appendChild(countBtn);
}

async function fetchProjects() {
  try {
    const resp = await fetch('/api/projects');
    if (!resp.ok) return;
    const projects = await resp.json();
    const sel = document.getElementById('project-select');
    if (!sel) return;
    sel.innerHTML = '';
    projects.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.slug;
      opt.textContent = `${p.slug} (${p.steps_count} kroků) — ${p.description}`;
      if (p.active) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch (_) {}
}

function setupProjectEvents() {
  const sel = document.getElementById('project-select');
  const btnNew = document.getElementById('btn-new-project');
  const btnDelete = document.getElementById('btn-delete-project');

  const newModal = document.getElementById('new-project-modal');
  const newClose = document.getElementById('new-project-close');
  const newCancel = document.getElementById('new-project-cancel');
  const newSubmit = document.getElementById('new-project-submit');

  if (sel) {
    sel.addEventListener('change', async () => {
      const slug = sel.value;
      if (isFormDirty) {
        if (!confirm('Máš neuložené změny v konfiguraci. Pokud pokračuješ bez uložení, neuložené změny budou zahozeny. Přepnout projekt?')) {
          sel.value = cfg.task_slug;
          return;
        }
      }
      try {
        const resp = await fetch('/api/projects/select', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slug })
        });
        const res = await resp.json();
        if (res.ok && res.config) {
          cfg = res.config;
          fillForm();
          await refreshModelStatus();
          await refreshDatasetOverview();
          await fetchProjects();
        } else {
          alert('Přepnutí projektu selhalo: ' + (res.error || 'Neznámá chyba'));
          sel.value = cfg.task_slug;
        }
      } catch (err) {
        alert('Přepnutí projektu selhalo: ' + err);
        sel.value = cfg.task_slug;
      }
    });
  }

  if (btnNew && newModal) {
    btnNew.addEventListener('click', () => {
      document.getElementById('new-project-slug').value = '';
      document.getElementById('new-project-desc').value = '';
      newModal.style.display = 'flex';
    });
  }

  const closeNewModal = () => { if (newModal) newModal.style.display = 'none'; };
  if (newClose) newClose.addEventListener('click', closeNewModal);
  if (newCancel) newCancel.addEventListener('click', closeNewModal);
  if (newModal) {
    newModal.addEventListener('click', (e) => {
      if (e.target === newModal) closeNewModal();
    });
  }

  if (newSubmit) {
    newSubmit.addEventListener('click', async () => {
      const slug = document.getElementById('new-project-slug').value.trim();
      const desc = document.getElementById('new-project-desc').value.trim();
      if (!slug) {
        alert('Zadej název projektu!');
        return;
      }
      try {
        const resp = await fetch('/api/projects/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slug, description: desc })
        });
        const res = await resp.json();
        if (res.ok && res.config) {
          cfg = res.config;
          fillForm();
          closeNewModal();
          await refreshModelStatus();
          await refreshDatasetOverview();
          await fetchProjects();
        } else {
          alert('Chyba při vytváření projektu: ' + (res.error || 'Neznámá chyba'));
        }
      } catch (err) {
        alert('Vytvoření projektu selhalo: ' + err);
      }
    });
  }

  if (btnDelete) {
    btnDelete.addEventListener('click', async () => {
      const slug = sel ? sel.value : null;
      if (!slug) return;
      if (!confirm(`Opravdu chceš smazat projekt "${slug}"? Tato akce je nevratná.`)) return;
      try {
        const resp = await fetch('/api/projects/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slug })
        });
        const res = await resp.json();
        if (res.ok) {
          // Pokud byl smazán aktivní projekt, server přepnul na jiný →
          // načteme nový cfg a překreslíme formulář.
          if (res.active_changed && res.config) {
            cfg = res.config;
            fillForm();
            await refreshModelStatus();
            await refreshDatasetOverview();
          }
          await fetchProjects();
        } else {
          alert('Smazání projektu selhalo: ' + (res.error || 'Neznámá chyba'));
        }
      } catch (err) {
        alert('Chyba při mazání projektu: ' + err);
      }
    });
  }
}

/* ── Modal Správa modelů a datasetů ────────────────────────────────── */

let activeModalTarget = null; // null pro baseline, nebo slug kroku (string)

function setupModalEvents() {
  const modal = document.getElementById('model-modal');
  const closeBtn = document.getElementById('modal-close');
  const btnBaseline = document.getElementById('btn-baseline-modal');
  const policySelect = document.getElementById('modal-policy-type');
  const trainStepsInput = document.getElementById('modal-train-steps');
  const batchSizeInput = document.getElementById('modal-batch-size');
  const saveFreqInput = document.getElementById('modal-save-freq');
  const deviceSelect = document.getElementById('modal-device');
  const finetuneBaseSelect = document.getElementById('modal-finetune-base-select');
  const datasetSelect = document.getElementById('modal-dataset-select');
  const splitSourceSelect = document.getElementById('modal-split-source-ds');
  const copyBtn = document.getElementById('modal-copy-cmd');
  const copySplitBtn = document.getElementById('modal-copy-split-cmd');
  const modeNew = document.getElementById('train-mode-new');
  const modeFinetune = document.getElementById('train-mode-finetune');
  const finetuneBox = document.getElementById('finetune-box');
  const addDsBtn = document.getElementById('modal-add-ds-btn');
  const addDsInput = document.getElementById('modal-add-ds-input');

  if (closeBtn) closeBtn.addEventListener('click', () => { modal.style.display = 'none'; });
  if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) modal.style.display = 'none'; });
  if (btnBaseline) btnBaseline.addEventListener('click', () => { openModelModal(null); });

  [modeNew, modeFinetune].forEach(radio => {
    if (radio) radio.addEventListener('change', () => {
      const isFinetune = modeFinetune && modeFinetune.checked;
      if (finetuneBox) finetuneBox.style.display = isFinetune ? 'block' : 'none';
      updateModalCmdPreview();
    });
  });

  if (splitSourceSelect) {
    splitSourceSelect.addEventListener('input', updateSplitCmdPreview);
    splitSourceSelect.addEventListener('change', updateSplitCmdPreview);
  }

  [datasetSelect, policySelect, trainStepsInput, batchSizeInput, saveFreqInput, deviceSelect, finetuneBaseSelect].forEach(el => {
    if (el) {
      el.addEventListener('input', updateModalCmdPreview);
      el.addEventListener('change', updateModalCmdPreview);
    }
  });

  if (copySplitBtn) {
    copySplitBtn.addEventListener('click', async () => {
      const cmdText = document.getElementById('modal-split-cmd-preview').textContent;
      try {
        await navigator.clipboard.writeText(cmdText);
        const old = copySplitBtn.textContent;
        copySplitBtn.textContent = 'Zkopírováno! ✓';
        setTimeout(() => { copySplitBtn.textContent = old; }, 2000);
      } catch (err) { alert('Kopírování se nezdařilo: ' + err); }
    });
  }

  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      const cmdText = document.getElementById('modal-cmd-preview').textContent;
      try {
        await navigator.clipboard.writeText(cmdText);
        const old = copyBtn.textContent;
        copyBtn.textContent = 'Zkopírováno! ✓';
        setTimeout(() => { copyBtn.textContent = old; }, 2000);
      } catch (err) { alert('Kopírování se nezdařilo: ' + err); }
    });
  }

  // "Přidat dataset" tlačítko
  if (addDsBtn && addDsInput) {
    const doAdd = async () => {
      const repo_id = addDsInput.value.trim();
      if (!repo_id) { addDsInput.focus(); return; }
      try {
        const resp = await fetch('/api/datasets/pin', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ step_slug: activeModalTarget, repo_id })
        });
        const res = await resp.json();
        if (res.ok && res.config) {
          cfg = res.config;
          addDsInput.value = '';
          // Znovu otevřeme modal — načteme aktuální stav
          await openModelModal(activeModalTarget);
        } else {
          alert('Přidání datasetu selhalo: ' + (res.error || 'Neznámá chyba'));
        }
      } catch (err) { alert('Chyba: ' + err); }
    };
    addDsBtn.addEventListener('click', doAdd);
    addDsInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doAdd(); });
  }
}

/** Vykreslí seznam datasetů v sekci 1 modálu.
 *  datasets = pole objektů { repo_id, exists, pinned } z API */
function renderModalDatasets(datasets, stepSlug) {
  const container = document.getElementById('modal-dataset-list');
  if (!container) return;
  container.innerHTML = '';

  // Synchronizovat dataset select (sekce 2) se seznamem
  const datasetSelect = document.getElementById('modal-dataset-select');
  datasetSelect.innerHTML = '';

  // Split source select — jen datasety co existují (nebo aspoň první)
  const splitSourceSelect = document.getElementById('modal-split-source-ds');
  splitSourceSelect.innerHTML = '';

  const preferredTrainId = stepSlug
    ? `local/${cfg.task_slug}_${stepSlug}`
    : `local/${cfg.task_slug}`;

  datasets.forEach(ds => {
    const { repo_id, exists, pinned, episodes, frames } = ds;
    const epText = exists
      ? (episodes != null
          ? `${episodes} epizod${frames != null ? `, ${frames.toLocaleString('cs-CZ')} snímků` : ''}`
          : 'data nalezena')
      : '';

    // --- Dataset list item ---
    const row = document.createElement('div');
    row.className = `ds-item ${exists ? 'ds-exists' : 'ds-missing'}`;

    const icon = document.createElement('span');
    icon.className = 'ds-icon';
    icon.textContent = exists ? '✓' : '○';
    icon.style.color = exists ? 'var(--green)' : 'var(--muted)';
    icon.title = exists ? `Dataset existuje na disku (${epText})` : 'Zatím prázdné — čeká na nahrávku a nasekání';

    const name = document.createElement('span');
    name.className = 'ds-name';
    name.textContent = epText ? `${repo_id} (${epText})` : repo_id;
    name.title = exists ? `${repo_id} — ${epText}` : `${repo_id} — bude vytvořen split_dataset.py`;

    row.appendChild(icon);
    row.appendChild(name);

    if (pinned) {
      const badge = document.createElement('span');
      badge.className = 'ds-badge-pinned';
      badge.textContent = 'vlastní';
      row.appendChild(badge);

      // Odebrat vlastní dataset
      const removeBtn = document.createElement('button');
      removeBtn.className = 'ds-remove';
      removeBtn.textContent = '✕';
      removeBtn.title = 'Odebrat tento vlastní dataset';
      removeBtn.addEventListener('click', async () => {
        try {
          const resp = await fetch('/api/datasets/unpin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ step_slug: stepSlug, repo_id })
          });
          const res = await resp.json();
          if (res.ok && res.config) {
            cfg = res.config;
            await openModelModal(stepSlug);
          } else {
            alert('Odebrání selhalo: ' + (res.error || '?'));
          }
        } catch (err) { alert('Chyba: ' + err); }
      });
      row.appendChild(removeBtn);
    }

    // "Použít pro trénink" button
    const trainBtn = document.createElement('button');
    trainBtn.className = 'ds-select-train' + (repo_id === preferredTrainId ? ' active' : '');
    trainBtn.textContent = repo_id === preferredTrainId ? '✓ Pro trénink' : 'Pro trénink';
    trainBtn.title = 'Nastavit jako tréninkový dataset v sekci 2';
    trainBtn.addEventListener('click', () => {
      datasetSelect.value = repo_id;
      // Aktualizovat všechny ds-select-train tlačítka
      container.querySelectorAll('.ds-select-train').forEach(btn => {
        btn.classList.remove('active');
        btn.textContent = 'Pro trénink';
      });
      trainBtn.classList.add('active');
      trainBtn.textContent = '✓ Pro trénink';
      updateModalCmdPreview();
      // Update exists status badge
      const existsSpan = document.getElementById('modal-dataset-select-exists');
      if (existsSpan) {
        existsSpan.textContent = exists ? `✓ ${epText}` : '○ zatím prázdné';
        existsSpan.style.color = exists ? 'var(--green)' : 'var(--muted)';
      }
    });
    row.appendChild(trainBtn);

    container.appendChild(row);

    // --- Dataset select (sekce 2) ---
    const opt = document.createElement('option');
    opt.value = repo_id;
    opt.textContent = exists ? `${repo_id} (${epText})` : `${repo_id} ○`;
    datasetSelect.appendChild(opt);

    // --- Split source select ---
    const splitOpt = document.createElement('option');
    splitOpt.value = repo_id;
    splitOpt.textContent = exists ? `${repo_id} (${epText})` : `${repo_id} (prázdné)`;
    splitSourceSelect.appendChild(splitOpt);
  });

  // Nastavit výchozí výběr pro trénink a split source
  const preferredExists = datasets.find(d => d.repo_id === preferredTrainId);
  if (preferredExists) {
    datasetSelect.value = preferredTrainId;
    splitSourceSelect.value = `local/${cfg.task_slug}`; // základ pro split = celá nahrávka
  }

  // Aktualizovat exists badge pro výchozí výběr
  const existsSpan = document.getElementById('modal-dataset-select-exists');
  if (existsSpan) {
    const selected = datasets.find(d => d.repo_id === datasetSelect.value);
    if (selected) {
      existsSpan.textContent = selected.exists
        ? `✓ ${selected.episodes != null ? selected.episodes + ' epizod' : 'existuje na disku'}`
        : '○ zatím prázdné';
      existsSpan.style.color = selected.exists ? 'var(--green)' : 'var(--muted)';
    }
  }
}

async function openModelModal(stepSlug) {
  activeModalTarget = stepSlug;
  const modal = document.getElementById('model-modal');
  const title = document.getElementById('modal-title');

  if (stepSlug) {
    title.textContent = `📄 Datasety a modely pro krok: ${stepSlug}`;
  } else {
    title.textContent = `📄 Baseline datasety a modely: ${cfg.task_slug || 'task'}`;
  }

  await refreshModelStatus();

  const statusObj = modelStatus
    ? (stepSlug ? (modelStatus.steps && modelStatus.steps[stepSlug]) : modelStatus.baseline)
    : null;

  // datasets je nyní pole objektů { repo_id, exists, pinned } z API
  // Fallback pro případ že server ještě vrací staré stringy
  let datasets = (statusObj && statusObj.datasets) || [];
  if (datasets.length === 0) {
    // Minimální fallback — zobrazit alespoň odvozené jméno
    datasets = [{ repo_id: stepSlug ? `local/${cfg.task_slug}_${stepSlug}` : `local/${cfg.task_slug}`, exists: false, pinned: false }];
  } else if (typeof datasets[0] === 'string') {
    // Starý formát (plain stringy) — wrap do objektů
    datasets = datasets.map(r => ({ repo_id: r, exists: false, pinned: false }));
  }

  const checkpoints = (statusObj && statusObj.checkpoints) || [];

  // Sekce 1: dataset list + split source
  renderModalDatasets(datasets, stepSlug);
  updateSplitCmdPreview();

  // Sekce 2: fine-tune select + parametry
  const finetuneBaseSelect = document.getElementById('modal-finetune-base-select');
  finetuneBaseSelect.innerHTML = '';
  if (checkpoints && checkpoints.length > 0) {
    checkpoints.forEach(ckpt => {
      const opt = document.createElement('option');
      opt.value = ckpt.path;
      opt.textContent = `${ckpt.name} (${ckpt.steps || 0} kroků)`;
      finetuneBaseSelect.appendChild(opt);
    });
  } else {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'Žádné checkpointy pro doučení';
    finetuneBaseSelect.appendChild(opt);
  }

  let targetSteps = cfg.train_steps || 20000;
  if (stepSlug) {
    const sObj = (cfg.steps || []).find(s => s.slug === stepSlug);
    if (sObj && sObj.train_steps) targetSteps = sObj.train_steps;
  }

  document.getElementById('train-mode-new').checked = true;
  document.getElementById('finetune-box').style.display = 'none';
  document.getElementById('modal-policy-type').value = cfg.policy_type || 'act';
  document.getElementById('modal-train-steps').value = targetSteps;
  document.getElementById('modal-batch-size').value = cfg.batch_size || 8;
  document.getElementById('modal-save-freq').value = cfg.save_freq || 5000;
  document.getElementById('modal-device').value = cfg.device || 'cuda';

  updateModalCmdPreview();
  renderModalCheckpoints(checkpoints, stepSlug);

  modal.style.display = 'flex';
}

function updateSplitCmdPreview() {
  const py = cfg.python || 'python';
  const splitSourceSelect = document.getElementById('modal-split-source-ds');
  const sourceDs = (splitSourceSelect && splitSourceSelect.value) ? splitSourceSelect.value : `local/${cfg.task_slug || 'task'}`;
  const stepsSlugs = (cfg.steps || []).map(s => s.slug).filter(Boolean);
  const stepsArg = stepsSlugs.length > 0 ? stepsSlugs.join(',') : 'step1,step2';

  // Jednořádkově — "^" pokračování funguje jen v cmd.exe, ne v PowerShellu
  // (tam je to zpětné apostrofy) ani v bashi (tam zpětné lomítko). Zbytek
  // stránky ze stejného důvodu taky generuje jednořádkové příkazy.
  const cmd = `${py} split_dataset.py --repo-id ${sourceDs} --steps ${stepsArg}`;
  document.getElementById('modal-split-cmd-preview').textContent = cmd;
}

function updateModalCmdPreview() {
  const modeFinetune = document.getElementById('train-mode-finetune');
  const isFinetune = modeFinetune && modeFinetune.checked;

  const dsSelect = document.getElementById('modal-dataset-select');
  const ds = (dsSelect && dsSelect.value) ? dsSelect.value : 'local/dataset';
  const pol = document.getElementById('modal-policy-type').value || 'act';
  const steps = document.getElementById('modal-train-steps').value || 20000;
  const batch = document.getElementById('modal-batch-size').value || 8;
  // save_freq vyšší než cílové kroky by neuložilo žádný checkpoint — stejná
  // pojistka jako trainFlags() pro příkazy na hlavní stránce (render()).
  const saveFreq = Math.min(Number(document.getElementById('modal-save-freq').value) || 5000, Number(steps) || 20000);
  const device = document.getElementById('modal-device').value || 'cuda';
  const py = cfg.python || 'python';
  const root = cfg.output_root || 'outputs/training';

  // Odvozeno z VYBRANÉHO datasetu (ds), ne z toho, pro který krok byl modál
  // otevřený (activeModalTarget) — jinak by přepnutí "Tréninkového datasetu"
  // v sekci 2 na jiný krok dál generovalo output_dir podle původního kroku a
  // trénink by tichě přepisoval (nebo, jako tady, narazil na FileExistsError)
  // cizí checkpoint.
  let outDirName;
  if (ds.startsWith('local/')) {
    outDirName = `${ds.slice('local/'.length)}_${pol}`;
  } else if (activeModalTarget) {
    outDirName = `${cfg.task_slug || 'task'}_${activeModalTarget}_${pol}`;
  } else {
    outDirName = `${cfg.task_slug || 'task'}_${pol}`;
  }
  if (isFinetune) {
    outDirName += '_ft';
  }
  const outDir = `${root}/${outDirName}`;

  // -m lerobot.scripts.lerobot_train (ne cesta k souboru — ta na disku
  // neexistuje a i kdyby ano, závisela by na tom, odkud se příkaz spouští),
  // a stejné jméno přepínačů jako trainFlags() na hlavní stránce: --steps,
  // --batch_size, --save_freq, --policy.device jsou to, co lerobot_train
  // doopravdy čte (TrainPipelineConfig) — --training.* a holé --device
  // nejsou platné přepínače vůbec.
  const parts = [`${py} -m lerobot.scripts.lerobot_train`, arg('dataset.repo_id', ds)];
  if (isFinetune) {
    const finetuneBaseSelect = document.getElementById('modal-finetune-base-select');
    const basePath = (finetuneBaseSelect && finetuneBaseSelect.value) ? finetuneBaseSelect.value : `${outDir}/checkpoints/last/pretrained_model`;
    parts.push(arg('policy.path', basePath));
  } else {
    parts.push(arg('policy.type', pol));
  }
  parts.push(
    arg('steps', steps), arg('batch_size', batch), arg('save_freq', saveFreq),
    arg('policy.device', device), '--wandb.enable=false',
    arg('output_dir', outDir), '--policy.push_to_hub=false');

  document.getElementById('modal-cmd-preview').textContent = parts.join(' ');
}

function renderModalCheckpoints(checkpoints, stepSlug) {
  const container = document.getElementById('modal-ckpt-list');
  container.innerHTML = '';

  if (!checkpoints || checkpoints.length === 0) {
    container.innerHTML = '<div style="font-size:13px; color:var(--muted); font-style:italic;">Zatím nebyly nalezeny žádné natrénované checkpointy na disku. Natrénuj model pomocí příkazu výše.</div>';
    return;
  }

  let targetSteps = cfg.train_steps || 20000;
  if (stepSlug) {
    const sObj = (cfg.steps || []).find(s => s.slug === stepSlug);
    if (sObj && sObj.train_steps) targetSteps = sObj.train_steps;
  }

  checkpoints.forEach((ckpt) => {
    const card = document.createElement('div');
    const isSufficient = ckpt.trained && (ckpt.steps || 0) >= targetSteps;
    const badgeClass = isSufficient ? 'ok' : 'warn';
    const badgeChar = isSufficient ? '✓' : '✗';

    card.className = `ckpt-card ${ckpt.active ? 'selected' : ''}`;

    const stepsText = ckpt.steps != null ? `${ckpt.steps} / cíl ${targetSteps} kroků` : 'netrénováno';
    const activeChecked = ckpt.active ? 'checked' : '';
    const titleText = isSufficient
      ? `${ckpt.path}\nnatrénováno: ${ckpt.steps} kroků / cíl ${targetSteps}`
      : `${ckpt.path}\nnatrénováno jen ${ckpt.steps || 0} kroků / cíl ${targetSteps} — dotrénovat`;

    card.innerHTML = `
      <div class="radio-col">
        <input type="radio" name="active_ckpt_radio" value="${escapeAttr(ckpt.path)}" ${activeChecked}>
      </div>
      <div class="info-col">
        <div class="title">${escapeAttr(ckpt.name)} ${ckpt.active ? '<span style="color:var(--accent); font-size:11px; margin-left:6px;">[AKTIVNÍ PRO ORCHESTRACI]</span>' : ''}</div>
        <div class="meta">
          <span><b>Dataset:</b> ${escapeAttr(ckpt.dataset)}</span>
          <span><b>Architektura:</b> ${escapeAttr(ckpt.policy_type)}</span>
          <span><b>Stav tréninku:</b> ${stepsText}</span>
        </div>
      </div>
      <div class="badge-col">
        <span class="model-badge ${badgeClass}" title="${escapeAttr(titleText)}">${badgeChar}</span>
      </div>
    `;

    const radio = card.querySelector('input[type="radio"]');
    const selectHandler = async () => {
      radio.checked = true;
      document.querySelectorAll('#modal-ckpt-list .ckpt-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');

      if (stepSlug) {
        const stepObj = (cfg.steps || []).find(s => s.slug === stepSlug);
        if (stepObj) stepObj.policy_path = ckpt.path;
      } else {
        cfg.baseline_policy_path = ckpt.path;
      }
      await saveConfig(cfg);
      renderSteps();
    };

    card.addEventListener('click', (e) => {
      if (e.target !== radio) selectHandler();
    });
    radio.addEventListener('change', selectHandler);

    container.appendChild(card);
  });
}
