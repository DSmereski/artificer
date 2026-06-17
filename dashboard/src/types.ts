/**
 * types.ts — Shared domain types for Hive dashboard Phase B.
 *
 * Re-exports gateway types and adds scout / telemetry shapes.
 * Panels import from here; gateway.ts stays as the HTTP client.
 */

export type {
  BoardStats,
  BoardState,
  BoardTask,
  BoardProject,
  SmokeStats,
  TokenStats,
  SunoTrack,
} from './gateway.js';

// ─── Scout / telemetry types ─────────────────────────────────────────────────

export interface GpuInfo {
  index: number;
  name: string;
  temp_c: number;
  vram_used_mb: number;
  vram_total_mb: number;
  vram_used_pct: number;
  utilization_pct: number;
  game?: boolean;
}

export interface DiskInfo {
  drive: string;        // e.g. "C:\\"
  free_gb: number;
  total_gb: number;
  used_pct: number;
}

export interface BotInfo {
  name: string;
  is_running: boolean;
  pid?: number;
  uptime_seconds?: number;
}

export interface HostInfo {
  cpu?: { usage_pct?: number; cores_logical?: number; cores_physical?: number; freq_mhz?: number };
  ram?: { used_gb?: number; total_gb?: number; used_pct?: number };
  uptime_seconds?: number;
}

// ─── Docker types ─────────────────────────────────────────────────────────────

export interface DockerContainer {
  name: string;
  state: string;   // running | exited | paused | created | ...
  status: string;  // "Up 43 minutes" etc.
  image: string;
  health: string;  // healthy | unhealthy | starting | ""
}

export interface DockerStatus {
  available: boolean;
  reason?: string;
  running: number;
  total: number;
  containers: DockerContainer[];
}

// ─── Git activity ─────────────────────────────────────────────────────────────

export interface GitCommit {
  project: string;
  hash: string;
  subject: string;
  author: string;
  ts: number;        // author unix seconds
}

export interface GitActivity {
  commits: GitCommit[];
}

export interface ScoutStatus {
  gpus: GpuInfo[];
  disks: DiskInfo[];
  bots: BotInfo[];
  host: HostInfo;
}

export interface ScoutHistorySample {
  ts: number; // unix epoch seconds
  gpus: Array<{
    index: number;
    utilization_pct: number;
    temp_c: number;
    vram_used_pct: number;
  }>;
  host?: {
    cpu_pct?: number;
    ram_used_pct?: number;
  };
}

export interface TurnMetric {
  ts: number;
  hive_tokens: number;
  claude_tokens: number;
  latency_ms?: number;
}

// ─── Rolling buffer sample (built client-side from polled snapshots) ─────────

export interface BoardStatsSample {
  ts: number;                // unix epoch ms (Date.now())
  hive_tokens: number;
  claude_tokens: number;
  cost_usd: number;
  done_count: number;
  smoke_pass_pct: number;   // 0-100
  parse_fail_rate: number;  // 0-1
}

// ─── Escalation types ────────────────────────────────────────────────────────

export interface Escalation {
  slug: string;
  title?: string;
  created_at?: string;
  reason?: string;
}

export interface EscalationList {
  open_count: number;
  escalations: Escalation[];
}

// ─── Calendar / agenda types ─────────────────────────────────────────────────

export interface CalendarJob {
  id: string;
  title: string;
  next_run?: string;       // ISO 8601
  recurrence?: string;
  status?: string;
}
