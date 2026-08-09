/* Sdílená konfigurace obou stránek.
 *
 * Uloží se do localStorage (aby stránka se setupem fungovala i jako holé HTML
 * bez serveru) a zároveň se pošle na backend do config.json, odkud ji čte
 * orchestrátor. Jedno místo pravdy: co vyplníš v setupu, s tím poběží běh. */

const STORAGE_KEY = 'diplomka.config';

const DEFAULTS = {
  python: 'C:/Users/green/miniconda3/envs/lerobot/python.exe',
  device: 'cuda',

  robot_type: 'so101_follower',
  robot_port: 'COM3',
  robot_id: 'my_follower_arm',
  teleop_type: 'so101_leader',
  teleop_port: 'COM4',
  teleop_id: 'my_leader_arm',

  camera_name: 'top',
  camera_index: '0',
  camera_width: 640,
  camera_height: 480,
  camera_fps: 30,

  camera2_name: '',
  camera2_index: '1',
  camera2_width: 640,
  camera2_height: 480,
  camera2_fps: 30,

  fps: 30,
  data_strategy: 'split',
  task_slug: 'pick_and_place',
  task_description: 'vlož kostku do krabice',
  scene_description: 'Rameno SO-101 na stole, před ním kostka a krabice, kamera shora.',
  steps: [
    { slug: 'grab_cube', description: 'gripper drží kostku', grasp: true },
    { slug: 'move_to_box', description: 'kostka je nad krabicí', grasp: false },
    { slug: 'release', description: 'kostka leží v krabici, gripper je otevřený', grasp: false },
  ],

  episodes: 30,
  episode_time_s: 20,
  reset_time_s: 8,

  policy_type: 'act',
  train_steps: 20000,
  batch_size: 8,
  save_freq: 5000,
  output_root: 'outputs/training',

  lm_url: 'http://localhost:1234/v1',
  llm_model: 'local-llm',
  vlm_model: 'local-vlm',

  latch_timeout_s: 60,
  step_timeout_s: 8,
  max_replans: 3,

  // Plánovač: posílat mu snímky scény a nechat ho nejdřív popsat, co vidí.
  planner_vision: true,
  planner_reasoning: true,

  // Ukončovací protokoly kroku. Správné hodnoty závisí na úloze i hardwaru
  // (jiný gripper, jiný předmět, úloha bez úchopu), proto jsou obě metody
  // vypínatelné a jejich prahy konfigurovatelné.
  protocol_a_enabled: true,
  protocol_a_threshold_rad: 0.005,
  protocol_a_patience: 5,
  protocol_b_enabled: true,
  protocol_b_limit_ma: 250,
  gripper_state_in_context: true,
};

/** Načte konfiguraci: nejdřív backend (autorita), jinak localStorage. */
async function loadConfig() {
  let cfg = { ...DEFAULTS };
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (stored) cfg = { ...cfg, ...stored };
  } catch (_) { /* poškozený localStorage nevadí */ }
  try {
    const resp = await fetch('/api/config');
    if (resp.ok) cfg = { ...cfg, ...(await resp.json()) };
  } catch (_) { /* stránka běží bez serveru */ }
  return cfg;
}

/** Uloží konfiguraci do localStorage i na backend (pokud běží). */
async function saveConfig(cfg) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
  try {
    await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    return true;
  } catch (_) {
    return false;
  }
}

/* ── Odvozené cesty a identifikátory ─────────────────────────────────────
 * Stejná pravidla používá i orchestrator.py — kdyby se rozešla, běh by
 * hledal jiné modely, než jaké setup natrénoval. */

const derive = {
  /** Dataset celé úlohy — z něj se trénuje monolitický baseline. */
  baselineRepo: (c) => `local/${c.task_slug}`,
  /** Dílčí dataset jednoho kroku (vznikne rozdělením nahrávky).
   *  Jen jedno lomítko — Hugging Face hub validuje repo_id jako
   *  `namespace/název`, takže `local/uloha/krok` by neprošlo. */
  stepRepo: (c, slug) => `local/${c.task_slug}_${slug}`,
  /** Výstupní adresář tréninku baseline modelu. */
  baselineOut: (c) => `${c.output_root}/${c.task_slug}_${c.policy_type}`,
  /** Kde na disku doopravdy leží dataset — LeRobotDataset.resume() to
   *  potřebuje explicitně, jinak by mu psaní do sdílené cache mohlo
   *  poškodit revizní snapshoty ostatních datasetů. */
  datasetRoot: (c) => `$HOME/.cache/huggingface/lerobot/${derive.baselineRepo(c)}`,
  /** Výstupní adresář tréninku modelu jednoho kroku. */
  stepOut: (c, slug) => `${c.output_root}/${c.task_slug}_${slug}_${c.policy_type}`,
  /** Draccus zápis kamer pro lerobot CLI — jedna nebo dvě, podle vyplnění. */
  camerasArg: (c) => {
    const one = (name, index, width, height, fps) => (name
      ? `${name}: {type: opencv, index_or_path: ${index}, width: ${width}, height: ${height}, fps: ${fps}}`
      : '');
    const entries = [
      one(c.camera_name, c.camera_index, c.camera_width, c.camera_height, c.camera_fps),
      one(c.camera2_name, c.camera2_index, c.camera2_width, c.camera2_height, c.camera2_fps),
    ].filter(Boolean);
    return entries.length ? `{ ${entries.join(', ')}}` : '';
  },
  steps: (c) => (c.steps || []).filter((s) => s.slug && s.slug.trim()),
};
