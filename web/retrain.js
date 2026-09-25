/* Retrénink stránka — provizorní, samostatná (nesdílí stav se setup.js).
 *
 * DŮLEŽITÉ: local/<repo> je jeden živý, postupně nahrávaný dataset — "_20ep",
 * "_60ep", "_120ep" checkpointy NEJSOU natrénované na třech různých kopiích,
 * ale na třech postupných velikostech TÉHOŽ adresáře v době, kdy měl zrovna
 * tolik epizod (nahráváno přes --resume). Ty starší velikosti už na disku
 * jako samostatná data neexistují — je tam jen aktuální stav (dnes 120).
 *
 * Takže se NEKOPÍRUJE a NEUSEKÁVÁ nic ručně do jiných složek — LeRobot umí
 * omezit trénink na konkrétní epizody TÉHOŽ datasetu přímo přes
 * `--dataset.episodes='[0,1,...,N-1]'` (DatasetConfig.episodes, viz
 * lerobot/configs/default.py a použití v lerobot_imgtransform_viz.py).
 * Pro každou dřívější velikost (zjištěnou z názvů existujících checkpointů,
 * `_<N>ep_`) i pro aktuální plný počet se tak vygeneruje samostatný příkaz na
 * prvních N epizod stejného živého datasetu — nic se nekopíruje, nic
 * nepřepisuje, žádné riziko poškození zdrojových dat.
 *
 * "Hotovo" se NEZNAČÍ ručně — appka to pozná sama z toho, jestli na disku
 * (viz /api/models → checkpoints[].name/.trained) už existuje checkpoint se
 * stejným názvem, jaký by tenhle příkaz vytvořil. Model buď vznikl, nebo ne;
 * ruční checkbox by mohl zůstat zaškrtnutý i po smazání checkpointu, nebo
 * naopak nezaškrtnutý po tom, co ho natrénoval někdo jiný / jiná relace. */

let cfg = null;
let modelStatus = null;

// Kolik "epoch" (= steps*batch_size/frames) měly historické checkpointy
// napříč VŠEMI 4 modely a jejich _20ep/_60ep(_61ep)/_120ep velikostmi —
// zjištěno 2026-09-19 z /api/models. 11 z 12 hodnot leží v 32.8–34.6,
// medián bez jasně odlehlého bodu (diplomka_1_carry_cube_120ep_act, 31.3 —
// ten checkpoint měl na svou velikost podtrénováno) je 33.4. Nový výchozí
// počet kroků se z tohohle dopočítává místo kopírování jednoho čísla na
// všechny velikosti, což dřív dávalo špatný výsledek právě pro tenhle případ.
const TARGET_EPOCHS = 33.4;

async function getJSON(url) {
  const r = await fetch(url);
  const j = await r.json();
  if (j && j.ok === false) throw new Error(j.error || 'chyba serveru');
  return j;
}

function arg(name, value) {
  const token = `--${name}=${value}`;
  return /[\s{}"]/.test(String(value)) ? `"${token}"` : token;
}

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

/* Jeden chunk_size pro VŠECHNY modely a VŠECHNY velikosti — největší
 * kandidát, u kterého i nejkratší epizoda NEJKRATŠÍHO z celých datasetů
 * (dnes carry_cube, 79 snímků) zůstane v padding zóně (viz
 * dataset_reader.py._get_query_indices) pod `threshold`. Konzervativní: kus
 * datasetu (prvních N epizod) může mít i vyšší minimum než celek, nikdy
 * nižší, takže tohle číslo sedí i na každý menší tier. */
function chooseGlobalChunkSize(lengthArrays, threshold = 0.20) {
  const globalMin = Math.min(...lengthArrays.map((L) => Math.min(...L)));
  const candidates = [10, 15, 20, 30, 50, 100];
  let best = null;
  for (const c of candidates) {
    if (Math.min(c, globalMin) / globalMin <= threshold) best = c;
  }
  return { chunkSize: best ?? candidates[0], globalMin, forced: best === null };
}

function paddingFrac(chunkSize, len) {
  return Math.min(chunkSize, len) / len;
}

function fmtPct(x) { return `${(x * 100).toFixed(1)} %`; }

function episodesArg(n) {
  return `[${Array.from({ length: n }, (_, i) => i).join(',')}]`;
}

/* Kolik kroků odpovídá TARGET_EPOCHS průchodům přes `frames` snímků při
 * daném batch_size, zaokrouhleno na stovky (historické hodnoty taky nejsou
 * kulaté na tisíce). */
function computeStepsDefault(frames, batchSize) {
  const raw = TARGET_EPOCHS * frames / batchSize;
  return Math.max(100, Math.round(raw / 100) * 100);
}

function trainCommand({ py, repo, outDir, steps, batchSize, saveFreq, device, policyType,
                        jobName, chunkSize, episodesN, totalEps }) {
  const saveFreqClamped = Math.min(saveFreq, steps);
  const parts = [
    `${py} -m lerobot.scripts.lerobot_train`,
    arg('policy.type', policyType), arg('dataset.repo_id', repo),
  ];
  // Plny pocet epizod = cely dataset, filtr je zbytecny navic. Mensi tier =
  // prvnich N epizod TEHOZ zivého datasetu, zadna kopie na disku.
  if (episodesN < totalEps) {
    parts.push(arg('dataset.episodes', episodesArg(episodesN)));
  }
  parts.push(
    arg('steps', steps), arg('batch_size', batchSize),
    arg('save_freq', saveFreqClamped), arg('job_name', jobName),
    arg('policy.device', device), '--wandb.enable=false',
    arg('output_dir', outDir), '--policy.push_to_hub=false',
    arg('policy.chunk_size', chunkSize), arg('policy.n_action_steps', chunkSize),
  );
  return parts.join(' ');
}

/* Sjednoť blízké velikosti napříč VŠEMI cíli do jedné kanonické hodnoty.
 * Historicky se stalo, že checkpoint dostal do názvu špatný počet epizod
 * kvůli chybě při nahrávání — baseline má na disku `diplomka_1_61ep_act`,
 * přestože reálně šlo o stejných 60 epizod jako u ostatních 3 modelů
 * (`_60ep_` u catch_cube/carry_cube/homing). Bez tohohle by nová stránka tu
 * starou chybu doslova zopakovala v novém příkazu (--dataset.episodes na 61
 * položek místo 60). Kanonická hodnota v klastru čísel do sebe vzdálených
 * nejvýš `tolerance` = nejkulatější (dělitelná 10) nebo nejčastější napříč
 * všemi 4 modely. */
function canonicalizeTiers(allDetected, tolerance = 3) {
  const counts = new Map();
  for (const n of allDetected) counts.set(n, (counts.get(n) || 0) + 1);
  const uniq = [...counts.keys()].sort((a, b) => a - b);
  const clusters = [];
  for (const n of uniq) {
    const last = clusters[clusters.length - 1];
    if (last && n - last[last.length - 1] <= tolerance) last.push(n);
    else clusters.push([n]);
  }
  const map = new Map(); // detekovana hodnota -> kanonicka hodnota
  for (const cluster of clusters) {
    const canonical = cluster.slice().sort((a, b) => {
      const roundA = a % 10 === 0, roundB = b % 10 === 0;
      if (roundA !== roundB) return roundA ? -1 : 1; // kulate cislo vyhrava
      return (counts.get(b) - counts.get(a)) || (a - b); // pak castejsi, pak mensi
    })[0];
    for (const n of cluster) map.set(n, canonical);
  }
  return map;
}

/* Velikostni tiery zjistene z nazvu existujicich checkpointu (_<N>ep_),
 * proheznute pres kanonickou mapu (viz canonicalizeTiers), plus vzdy aktualni
 * plny pocet epizod. Tier vetsi nez aktualni pocet (nemelo by nastat, ale
 * kdyby dataset mezitim někdo zmensil) se zahodi — nejde reprodukovat jako
 * prefix. Vraci {n, correctedFrom} — correctedFrom je puvodni (spatne)
 * cislo, pokud kanonizace neco zmenila, jinak null. */
function detectTiers(checkpoints, currentEps, canonicalMap) {
  const correctedFrom = new Map(); // kanonicka hodnota -> puvodni (spatne) cislo
  const tiers = new Set([currentEps]);
  for (const c of (checkpoints || [])) {
    const m = /_(\d+)ep_/.exec(c.name);
    if (!m) continue;
    const raw = parseInt(m[1], 10);
    const canonical = canonicalMap.has(raw) ? canonicalMap.get(raw) : raw;
    tiers.add(canonical);
    if (canonical !== raw) correctedFrom.set(canonical, raw);
  }
  return [...tiers]
    .filter((n) => n > 0 && n <= currentEps)
    .sort((a, b) => a - b)
    .map((n) => ({ n, correctedFrom: correctedFrom.get(n) ?? null }));
}

/* mm:ss/h:mm/d h formatovani odhadovane doby treninku. */
function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  const s = Math.round(seconds);
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const mins = Math.floor((s % 3600) / 60);
  if (days > 0) return `${days} d ${hours} h`;
  if (hours > 0) return `${hours} h ${mins} min`;
  if (mins > 0) return `${mins} min`;
  return `${s} s`;
}

/* Existuje uz na disku checkpoint se stejnym jmenem, jake by tenhle prikaz
 * vytvoril? To je jediny zdroj pravdy pro "hotovo" — viz komentar nahore. */
function diskStatus(existingCheckpoints, outDir) {
  const base = outDir.split('/').pop();
  const found = (existingCheckpoints || []).find((c) => c.name === base);
  if (!found) return { state: 'missing' };
  return { state: found.trained ? 'trained' : 'partial', steps: found.steps };
}

function statusBadge(status) {
  if (status.state === 'trained') {
    return `<span class="model-badge ok" style="display:inline-block; padding:2px 10px;">✓ na disku, natrénováno${status.steps ? ` (${status.steps} kr.)` : ''}</span>`;
  }
  if (status.state === 'partial') {
    return `<span class="model-badge warn" style="display:inline-block; padding:2px 10px;">na disku, ale nedokončeno${status.steps ? ` (${status.steps} kr.)` : ''}</span>`;
  }
  return `<span style="color:var(--muted); font-size:12.5px;">zatím neproběhlo</span>`;
}

function checkpointList(checkpoints) {
  if (!checkpoints || !checkpoints.length) {
    return '<p style="color:var(--muted); font-size:13px; margin:4px 0 0;">Zatím žádný natrénovaný checkpoint pro tenhle cíl.</p>';
  }
  const rows = checkpoints.map((c) => {
    const state = c.trained ? `hotovo${c.steps ? `, ${c.steps} kr.` : ''}` : 'nedokončeno';
    return `<li><code class="inline">${c.name}</code> <span style="color:var(--muted); font-size:12px;">— ${state}</span>${c.active ? ' <b style="color:var(--accent); font-size:12px;">(aktivní)</b>' : ''}</li>`;
  }).join('');
  return `<p style="color:var(--muted); font-size:13px; margin:10px 0 2px;">
      <b>STARÉ modely z dřívějška</b> (tahle stránka je nemaže ani nepřepisuje — jen pro přehled, ať je vidět, že nový příkaz níže míří jinam):
    </p>
    <ul style="margin:2px 0 0; padding-left:20px; font-size:13px; color:var(--muted);">${rows}</ul>`;
}

function tierCard(host, { repoId, slug, taskSlug, outputRoot, policyType, py, device,
                          batchSize, saveFreq, jobNameBase, totalEps, lengths, n,
                          chunkSizeDefault, existingCheckpoints, correctedFrom, rate }) {
  const wrap = document.createElement('div');
  wrap.style.cssText = 'border:1px dashed var(--border); border-radius:var(--radius); padding:12px 14px; margin:10px 0; background:var(--inset);';
  const subLengths = lengths.slice(0, n);
  const minLen = Math.min(...subLengths);
  const frames = subLengths.reduce((a, b) => a + b, 0);
  const stepsDefault = computeStepsDefault(frames, batchSize);
  const isFull = n === totalEps;
  const correctionNote = correctedFrom
    ? ` <span style="color:var(--yellow);" title="Starý checkpoint měl v názvu _${correctedFrom}ep_, ale to bylo chybné pojmenování při nahrávání — sjednoceno na ${n}, stejně jako u ostatních modelů.">(opraveno z chybného ${correctedFrom})</span>`
    : '';

  wrap.innerHTML = `
    <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:6px;">
      <div style="font-weight:600; font-size:13.5px;">
        ${n} epizod${correctionNote} ${isFull ? '(celý aktuální dataset)' : `(prvních ${n} z ${totalEps} — <code class="inline">--dataset.episodes</code>, žádná kopie)`}
      </div>
      <div class="status-badge"></div>
    </div>
    <div style="display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:8px;">
      <label class="field" style="max-width:140px;">chunk_size
        <input type="number" min="1" max="1000" class="chunk-input" value="${chunkSizeDefault}">
      </label>
      <label class="field" style="max-width:140px;">trénovací kroky
        <input type="number" min="1" max="2000000" class="steps-input" value="${stepsDefault}">
        <span class="hint">~${TARGET_EPOCHS} epoch × ${frames} snímků</span>
      </label>
      <label class="field" style="max-width:170px;">odhad doby učení
        <span class="duration-out" style="padding:8px 0; display:block; font-weight:600;"></span>
      </label>
      <span class="hint padfrac" style="align-self:flex-end; margin-bottom:8px;"></span>
    </div>
    <div class="cmd-host"></div>`;
  host.appendChild(wrap);

  const chunkInput = wrap.querySelector('.chunk-input');
  const stepsInput = wrap.querySelector('.steps-input');
  const padHint = wrap.querySelector('.padfrac');
  const durationOut = wrap.querySelector('.duration-out');
  const statusEl = wrap.querySelector('.status-badge');
  const cmdHost = wrap.querySelector('.cmd-host');

  function outDirFor(chunkSize) {
    const base = slug ? `${taskSlug}_${slug}` : taskSlug;
    return `${outputRoot}/${base}_${n}ep_${policyType}_cs${chunkSize}`;
  }

  function refresh() {
    const chunkSize = Math.max(1, parseInt(chunkInput.value, 10) || chunkSizeDefault);
    const steps = Math.max(1, parseInt(stepsInput.value, 10) || stepsDefault);
    const frac = paddingFrac(chunkSize, minLen);
    padHint.textContent = `nejhorší epizoda tohoto úseku: ${fmtPct(frac)} v paddingu`;
    padHint.style.color = frac > 0.25 ? 'var(--yellow)' : 'var(--muted)';

    if (rate && rate.rate_steps_per_s > 0) {
      const dur = formatDuration(steps / rate.rate_steps_per_s);
      durationOut.textContent = dur ? `~${dur}` : '—';
      durationOut.title = `Ze skutečné rychlosti tréninku ${rate.rate_steps_per_s.toFixed(2)} kr./s, naměřené na checkpointu ${rate.path.split('/').pop()} (${rate.steps} kr. za ${formatDuration(rate.seconds)}).`;
      durationOut.style.color = '';
    } else {
      durationOut.textContent = 'odhad není k dispozici';
      durationOut.title = 'Pro tenhle cíl zatím neexistují aspoň dva uložené checkpointy, ze kterých by šla spočítat skutečná rychlost.';
      durationOut.style.color = 'var(--muted)';
      durationOut.style.fontWeight = '400';
      durationOut.style.fontSize = '12px';
    }

    const outDir = outDirFor(chunkSize);
    const jobName = `${jobNameBase}_${n}ep`;
    const command = trainCommand({
      py, repo: repoId, outDir, steps, batchSize, saveFreq, device, policyType,
      jobName, chunkSize, episodesN: n, totalEps,
    });
    cmdHost.innerHTML = '';
    cmdHost.appendChild(cmdCard(
      outDir.split('/').pop(),
      `${n} epizod, ${steps} kroků, chunk_size=${chunkSize}`,
      command,
    ));

    statusEl.innerHTML = statusBadge(diskStatus(existingCheckpoints, outDir));
  }

  chunkInput.addEventListener('input', refresh);
  stepsInput.addEventListener('input', refresh);

  refresh();
}

function renderTarget(host, { title, slug, repoId }, dsInfo, chunkSuggestion, canonicalMap, rate) {
  const section = document.createElement('div');
  section.style.cssText = 'border:1px solid var(--border); border-radius:var(--radius); padding:16px; margin-bottom:18px; background:var(--panel-2);';
  host.appendChild(section);

  if (dsInfo.error) {
    section.innerHTML = `<h3 style="margin:0 0 4px; font-size:15px;">${title}</h3>
      <span style="color:var(--red);">Dataset nedostupný: ${dsInfo.error}</span>`;
    return;
  }

  const { measured_episodes: eps, measured_frames: frames, declared_episodes: declEps, lengths } = dsInfo;
  const mean = frames / eps;
  const minLen = Math.min(...lengths), maxLen = Math.max(...lengths);
  const mismatchNote = (declEps !== eps)
    ? `<div class="note warn">meta/info.json tvrdí <b>${declEps}</b> epizod, ale na disku jich skutečně je <b>${eps}</b> — používá se změřený počet (${eps}), ne ten z metadat.</div>`
    : '';

  const status = slug ? (modelStatus.steps[slug] || {}) : (modelStatus.baseline || {});
  const existingCheckpoints = status.checkpoints || [];
  const tiers = detectTiers(existingCheckpoints, eps, canonicalMap);

  section.innerHTML = `<h3 style="margin:0 0 4px; font-size:15px;">${title} <span style="color:var(--muted); font-weight:400; font-size:13px;">(${repoId})</span></h3>
    <div style="color:var(--muted); font-size:13px; margin-bottom:8px;">epizod celkem: <b>${eps}</b> &nbsp;·&nbsp; snímků/epizoda: min <b>${minLen}</b>, průměr <b>${mean.toFixed(0)}</b>, max <b>${maxLen}</b></div>`;
  section.insertAdjacentHTML('beforeend', mismatchNote);
  section.insertAdjacentHTML('beforeend', checkpointList(existingCheckpoints));
  section.insertAdjacentHTML('beforeend',
    `<p style="color:var(--muted); font-size:13px; margin:10px 0 0;"><b>Nové příkazy k retréninku</b> (chunk_size=${chunkSuggestion.chunkSize} pro všechny modely, viz vysvětlení nahoře):</p>`);

  const tiersHost = document.createElement('div');
  tiersHost.style.marginTop = '10px';
  section.appendChild(tiersHost);

  const jobNameBase = slug ? `${cfg.task_slug}_${slug}_retrain` : `${cfg.task_slug}_retrain`;

  for (const { n, correctedFrom } of tiers) {
    tierCard(tiersHost, {
      repoId, slug, taskSlug: cfg.task_slug, outputRoot: cfg.output_root,
      policyType: cfg.policy_type, py: cfg.python, device: cfg.device,
      batchSize: cfg.batch_size, saveFreq: cfg.save_freq, jobNameBase,
      totalEps: eps, lengths, n, chunkSizeDefault: chunkSuggestion.chunkSize,
      existingCheckpoints, correctedFrom, rate,
    });
  }
}

/* Checkpoint s nejvic kroky pro dany cil — nejdelsi beh dava nejspolehlivejsi
 * odhad rychlosti (viz checkpoint_rate() v server.py, mereno ze span mezi
 * prvnim a poslednim ulozenym checkpointem). */
function bestCheckpointFor(checkpoints) {
  const withSteps = (checkpoints || []).filter((c) => c.steps);
  if (!withSteps.length) return null;
  return withSteps.reduce((a, b) => (b.steps > a.steps ? b : a));
}

async function main() {
  const host = document.getElementById('models');
  host.innerHTML = 'načítám…';

  [cfg, modelStatus] = await Promise.all([getJSON('/api/config'), getJSON('/api/models')]);

  const targets = [
    { title: 'Baseline (celá úloha)', slug: null, repoId: `local/${cfg.task_slug}` },
    ...(cfg.steps || []).map((s) => ({
      title: `Krok: ${s.slug}`, slug: s.slug, repoId: `local/${cfg.task_slug}_${s.slug}`,
    })),
  ];

  const dsInfos = await Promise.all(targets.map((t) =>
    getJSON(`/api/dataset-episode-lengths?repo_id=${encodeURIComponent(t.repoId)}`)
      .catch((e) => ({ error: e.message }))));

  const chunkSuggestion = chooseGlobalChunkSize(dsInfos.filter((d) => !d.error).map((d) => d.lengths));

  // Kanonizace velikostí (viz canonicalizeTiers) potřebuje čísla ze VŠECH
  // 4 cílů najednou, ne jen z jednoho — jinak by baseline nemělo s čím svoje
  // osamocené "61" porovnat.
  const allDetected = [];
  for (const t of targets) {
    const status = t.slug ? (modelStatus.steps[t.slug] || {}) : (modelStatus.baseline || {});
    for (const c of (status.checkpoints || [])) {
      const m = /_(\d+)ep_/.exec(c.name);
      if (m) allDetected.push(parseInt(m[1], 10));
    }
  }
  const canonicalMap = canonicalizeTiers(allDetected);

  // Odhad rychlosti (kroky/s) z nejdéle trénovaného checkpointu každého cíle
  // — skutečné naměřené timestampy na tomhle stroji, ne odhad od oka.
  const rates = await Promise.all(targets.map((t) => {
    const status = t.slug ? (modelStatus.steps[t.slug] || {}) : (modelStatus.baseline || {});
    const best = bestCheckpointFor(status.checkpoints);
    if (!best) return Promise.resolve(null);
    return getJSON(`/api/checkpoint-rate?path=${encodeURIComponent(best.path)}`)
      .then((r) => r.rate).catch(() => null);
  }));

  host.innerHTML = '';
  if (chunkSuggestion.forced) {
    host.insertAdjacentHTML('beforeend',
      `<div class="note warn">Nejkratší dataset (min ${chunkSuggestion.globalMin} snímků) je tak krátký, že ani
       chunk_size=10 nedrží padding pod 20 % — použito minimum z nabídky, zvaž ho snížit ještě víc ručně
       u konkrétního modelu.</div>`);
  }

  targets.forEach((t, i) => renderTarget(host, t, dsInfos[i], chunkSuggestion, canonicalMap, rates[i]));
}

document.getElementById('reload-btn')?.addEventListener('click', () => main().catch(showError));

function showError(e) {
  document.getElementById('models').innerHTML =
    `<p style="color:var(--red);">Nepodařilo se načíst: ${e.message}</p>`;
}

main().catch(showError);
