/* Stránka Setup — formulář a z něj generované příkazy.
 *
 * Jediná logika, kterou stránka má: vezmi konfiguraci, poskládej z ní přesné
 * příkazové řádky a dej k nim tlačítko „kopírovat". Nic nespouští. */

let cfg = null;

/* ── Formulář ──────────────────────────────────────────────────────────── */

function fillForm() {
  document.querySelectorAll('[data-key]').forEach((el) => {
    const value = cfg[el.dataset.key];
    if (value !== undefined) el.value = value;
  });
  document.getElementById('boundaries').placeholder =
    Array.from({ length: Math.max(derive.steps(cfg).length - 1, 1) },
      (_, i) => (4 + i * 5).toFixed(1)).join(',');
  renderSteps();
  render();
}

function readForm() {
  document.querySelectorAll('[data-key]').forEach((el) => {
    cfg[el.dataset.key] = el.type === 'number' ? Number(el.value) : el.value;
  });
}

function renderSteps() {
  const host = document.getElementById('steps-editor');
  host.innerHTML = '';
  (cfg.steps || []).forEach((step, i) => {
    const row = document.createElement('div');
    row.className = 'step-row';
    row.innerHTML = `
      <div class="idx">${i + 1}.</div>
      <input class="slug" type="text" placeholder="slug_kroku" value="${escapeAttr(step.slug || '')}">
      <input class="desc" type="text" placeholder="co má být po kroku vidět" value="${escapeAttr(step.description || '')}">
      <label class="checkline" title="Krok končí sevřením objektu (protokol B), ne dosednutím kloubů">
        <input class="grasp" type="checkbox" ${step.grasp ? 'checked' : ''}> úchop
      </label>
      <button type="button" title="Odebrat krok">✕</button>`;
    row.querySelector('.slug').addEventListener('input', (e) => {
      cfg.steps[i].slug = e.target.value.trim(); render();
    });
    row.querySelector('.desc').addEventListener('input', (e) => {
      cfg.steps[i].description = e.target.value; render();
    });
    row.querySelector('.grasp').addEventListener('change', (e) => {
      cfg.steps[i].grasp = e.target.checked;
    });
    row.querySelector('button').addEventListener('click', () => {
      cfg.steps.splice(i, 1); renderSteps(); render();
    });
    host.appendChild(row);
  });
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
    ['Dodatečné epizody do stejného datasetu', 'stejný příkaz s --resume; num_episodes = kolik PŘIBUDE',
      `${record.join(' ')} --resume=true`],
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

  // 6) trénink
  const trainFlags = (repo, job, out) => [`${py} -m lerobot.scripts.lerobot_train`,
    arg('policy.type', c.policy_type), arg('dataset.repo_id', repo),
    arg('steps', c.train_steps), arg('batch_size', c.batch_size),
    arg('save_freq', c.save_freq), arg('job_name', job),
    arg('policy.device', c.device), '--wandb.enable=false',
    arg('output_dir', out), '--policy.push_to_hub=false'].join(' ');

  fill('cmds-train-baseline', [
    [`Baseline ${c.policy_type.toUpperCase()}`, `celý dataset → ${derive.baselineOut(c)}`,
      trainFlags(derive.baselineRepo(c), c.task_slug, derive.baselineOut(c))],
  ]);

  fill('cmds-train-steps', steps.map((s, i) => [
    `Krok ${i + 1}: ${s.slug}`,
    `${derive.stepRepo(c, s.slug)} → ${derive.stepOut(c, s.slug)}`,
    trainFlags(derive.stepRepo(c, s.slug), `${c.task_slug}_${s.slug}`, derive.stepOut(c, s.slug)),
  ]));

  // 7) baseline běh
  const baseline = [`${py} inference_daemon.py`,
    arg('robot.type', c.robot_type), arg('robot.port', c.robot_port), arg('robot.id', c.robot_id),
    arg('policy.path', derive.baselineOut(c)),
    arg('device', c.device), arg('fps', c.fps),
    '--no-triggers', arg('max-seconds', 60)];
  if (c.camera_name) {
    baseline.push(arg('camera.name', c.camera_name), arg('camera.index', c.camera_index),
      arg('camera.width', c.camera_width), arg('camera.height', c.camera_height),
      arg('camera.fps', c.camera_fps));
  }
  // Oficiální cesta LeRobotu — stejný model, jiný běhový stack. Hodí se jako
  // kontrola, že checkpoint sám o sobě funguje.
  const rollout = [`${py} -m lerobot.scripts.lerobot_rollout`,
    '--strategy.type=base',
    arg('policy.path', `${derive.baselineOut(c)}/checkpoints/last/pretrained_model`),
    arg('robot.type', c.robot_type), arg('robot.port', c.robot_port), arg('robot.id', c.robot_id),
    arg('task', c.task_description), arg('duration', 60)];
  if (cameras) rollout.push(arg('robot.cameras', cameras));

  fill('cmds-baseline-run', [
    ['Baseline stejným daemonem jako orchestrace', 'jeden model, žádné plánování ani verifikace — tohle je měřená baseline',
      baseline.join(' ')],
    ['Kontrola přes lerobot-rollout', 'oficiální nástroj LeRobotu; ověří, že checkpoint sám o sobě jede',
      rollout.join(' ')],
  ]);

  // 8) server
  fill('cmds-serve', [
    ['Spuštění serveru', 'otevře se na http://localhost:8000', `${py} server.py`],
  ]);
}

/* ── Start ─────────────────────────────────────────────────────────────── */

(async function init() {
  cfg = await loadConfig();
  fillForm();

  document.querySelectorAll('[data-key]').forEach((el) => {
    el.addEventListener('input', () => { readForm(); render(); });
  });
  document.getElementById('boundaries').addEventListener('input', render);

  document.querySelectorAll('[data-strategy]').forEach((el) => {
    el.addEventListener('change', () => { cfg.data_strategy = el.value; render(); });
  });

  document.getElementById('add-step').addEventListener('click', () => {
    cfg.steps = cfg.steps || [];
    cfg.steps.push({ slug: '', description: '', grasp: false });
    renderSteps(); render();
  });

  document.getElementById('save').addEventListener('click', async () => {
    readForm();
    const ok = await saveConfig(cfg);
    const status = document.getElementById('save-status');
    status.textContent = ok
      ? 'Uloženo do prohlížeče i do config.json.'
      : 'Uloženo do prohlížeče. Server neběží, config.json se nezapsal.';
    setTimeout(() => { status.textContent = ''; }, 4000);
  });

  document.getElementById('reset').addEventListener('click', () => {
    cfg = JSON.parse(JSON.stringify(DEFAULTS));
    fillForm();
  });
})();
