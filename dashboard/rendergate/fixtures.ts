/**
 * rendergate/fixtures.ts — deterministic seeded gateway state for the render
 * gate. Hand-built so the dashboard mounts a KNOWN set of panels with data
 * (a building task, a blocked/review task, GPU load, git/docker/suno feeds)
 * with NO live gateway. Every value is fixed: the gate must be reproducible.
 *
 * Tier intent: two mid-range cards under moderate AI load + a higher-end card
 * with NO game → non-gaming tier (so kpi + crew-board-full are always
 * relevant), and an in_progress task → BUILDING activity (the hero shows a
 * live lane).
 */

export const boardStats = {
  by_status: { proposed: 1, backlog: 3, ready: 2, in_progress: 2, qa: 1, review: 1, done: 14 },
  by_assignee: { 'qwen3.6:27b': 2, claude: 1 },
  tokens: { hive: 184_250, claude: 39_800 },
  avg_tokens_per_task: { 'qwen3.6:27b': 42_000 },
  avg_attempts: 2.4,
  smoke: { pass: 12, fail: 1 },
  cost_usd: 3.42,
  lessons: 7,
  parse_fail: { rate: 0.02, fails: 1, turns: 50 },
  top_projects: [
    { slug: 'example-rts', done: 8, active: 1 },
    { slug: 'example-cardgame', done: 4, active: 1 },
  ],
};

export const boardState = {
  paused: false,
  pending_approvals: [],
  projects: [
    { slug: 'example-rts', status: 'active' },
    { slug: 'example-cardgame', status: 'active' },
    { slug: 'example-hub', status: 'active' },
  ],
  tasks: [
    {
      slug: 'T-0401', title: 'Wire knowledge-file unlink endpoint', status: 'in_progress',
      project_slug: 'example-hub', assignee: 'qwen3.6:27b', last_action: 'write_file gateway/routes/knowledge.py',
      agent_turns: 11, hive_tokens: 38_400, claude_tokens: 0, updated_at: '2026-06-16T18:42:00Z', kind: 'code',
    },
    {
      slug: 'T-0402', title: 'Add ladder tier-6 balance pass', status: 'in_progress',
      project_slug: 'example-rts', assignee: 'qwen3.6:27b', last_action: 'run_cmd pytest -q',
      agent_turns: 6, hive_tokens: 21_900, claude_tokens: 0, updated_at: '2026-06-16T18:44:00Z', kind: 'code',
    },
    {
      slug: 'T-0399', title: 'Review: scoring table', status: 'review',
      project_slug: 'example-cardgame', assignee: 'claude', last_action: 'await human review',
      agent_turns: 18, hive_tokens: 51_000, claude_tokens: 12_400, updated_at: '2026-06-16T17:10:00Z', kind: 'code',
    },
    {
      slug: 'T-0398', title: 'QA: AI bluff thresholds', status: 'qa',
      project_slug: 'example-cardgame', assignee: 'qwen3.6:27b', last_action: 'smoke pass 8/8',
      agent_turns: 9, hive_tokens: 17_300, claude_tokens: 0, updated_at: '2026-06-16T16:55:00Z', kind: 'code',
    },
    {
      slug: 'T-0395', title: 'Ready: economy crew buffs', status: 'ready',
      project_slug: 'example-strategy', assignee: '', last_action: '', agent_turns: 0, kind: 'code',
    },
    {
      slug: 'T-0390', title: 'Done: themed scrollbars on the crew board', status: 'done',
      project_slug: 'example-hub', assignee: 'qwen3.6:27b', last_action: 'merged', agent_turns: 4,
      hive_tokens: 9_100, updated_at: '2026-06-15T22:00:00Z', kind: 'code',
    },
  ],
};

export const tokensByDay = Array.from({ length: 30 }, (_, i) => {
  const day = String(i + 1).padStart(2, '0');
  const hive = 40_000 + ((i * 7919) % 90_000);
  const claude = 5_000 + ((i * 4111) % 28_000);
  return { date: `2026-05-${day}`, hive, claude, total: hive + claude };
});

export const sessionToken = { token: 'rendergate-mock-board-token' };

export const scoutStatus = {
  host: 'localhost', ts: 1_750_000_000,
  cpu_pct: 34, ram_pct: 58, ram_used_gb: 37.1, ram_total_gb: 64,
  disk: [{ mount: 'C:', used_gb: 812, total_gb: 1862, pct: 44 }],
  bots: { gateway: true, scout: true, terry: true, vault: true, ollama: true },
  gpus: [
    { index: 0, name: 'NVIDIA GeForce RTX 4070', utilization_pct: 3, temp_c: 41,
      vram_used_mb: 900, vram_total_mb: 12_000, power_w: 38, fan_pct: 30, game: null },
    { index: 1, name: 'NVIDIA GeForce RTX 4060 Ti', utilization_pct: 62, temp_c: 64,
      vram_used_mb: 12_800, vram_total_mb: 16_000, power_w: 132, fan_pct: 55, game: null },
    { index: 2, name: 'NVIDIA GeForce RTX 4060 Ti', utilization_pct: 48, temp_c: 60,
      vram_used_mb: 10_100, vram_total_mb: 16_000, power_w: 118, fan_pct: 50, game: null },
  ],
};

export const scoutHistory = Array.from({ length: 40 }, (_, i) => ({
  ts: 1_750_000_000 - (40 - i) * 30,
  cpu_pct: 28 + ((i * 13) % 30),
  ram_pct: 52 + ((i * 7) % 14),
  gpus: [
    { index: 1, utilization_pct: 45 + ((i * 17) % 40), temp_c: 60 + ((i * 3) % 12) },
    { index: 2, utilization_pct: 35 + ((i * 11) % 38), temp_c: 57 + ((i * 2) % 10) },
  ],
}));

export const dockerStatus = {
  available: true,
  containers: [
    { name: 'example-project-db', image: 'postgres:16', state: 'running', status: 'Up 3 days', restarts: 0 },
    { name: 'example-app-db', image: 'postgres:16', state: 'running', status: 'Up 3 days', restarts: 1 },
    { name: 'redis', image: 'redis:7', state: 'running', status: 'Up 3 days', restarts: 0 },
  ],
};

export const gitActivity = {
  commits: [
    { project: 'example-hub', sha: 'a1b2c3d', subject: 'T-0359: add integration tests', ts: 1_750_000_000, ahead: 0, behind: 0 },
    { project: 'example-dashboard', sha: 'e4f5a6b', subject: 'feat: swap terminal/actions-log slots', ts: 1_749_990_000, ahead: 0, behind: 0 },
    { project: 'example-cardgame', sha: 'c7d8e9f', subject: 'feat: add new game mode', ts: 1_749_980_000, ahead: 0, behind: 0 },
  ],
};

export const escalations = { open: [], count: 0 };

export const upcomingJobs = [
  { id: 'j1', title: 'Nightly vault reindex', when: '2026-06-17T03:00:00Z', kind: 'cron' },
  { id: 'j2', title: 'Weekly session log', when: '2026-06-18T01:00:00Z', kind: 'session' },
];

export const sunoTracks = Array.from({ length: 24 }, (_, i) => ({
  id: `track-${1000 + i}`,
  title: i % 5 === 0 ? `Untitled · ${1000 + i}` : `Hive Anthem ${i + 1}`,
  artist_name: 'Operator',
  tags: 'synthwave, ambient',
  duration: 120 + i * 7,
  image_url: null,
  play_count: i * 3,
}));

/** Minimal stub for the embedded crew-board iframe (no live gateway). */
export const boardEmbedHtml = `<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:oklch(0.16 0.01 60);color:oklch(0.86 0.04 70);
font:13px/1.4 ui-monospace,monospace}.col{display:inline-block;vertical-align:top;
width:160px;padding:8px}.card{border:1px solid oklch(0.3 0.02 60);border-radius:3px;
padding:6px;margin:4px 0}</style></head><body>
<div class="col"><b>in_progress</b><div class="card">T-0401 knowledge unlink</div>
<div class="card">T-0402 tier-6 balance</div></div>
<div class="col"><b>review</b><div class="card">T-0399 payout table</div></div>
<div class="col"><b>done</b><div class="card">T-0390 scrollbars</div></div>
</body></html>`;
