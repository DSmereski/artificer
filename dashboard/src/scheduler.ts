/**
 * scheduler.ts — Poll scheduler with pause/visibility awareness.
 *
 * Manages multiple named poll jobs at different intervals.
 * Suspends all polls when Lively pauses the wallpaper (via pause.ts) or
 * when the document becomes hidden. Resumes + immediate refetch on un-pause.
 *
 * Usage:
 *   const sched = createScheduler();
 *   sched.register('board', 10_000, fetchBoard);
 *   sched.register('scout',  3_000, fetchScout);
 *   sched.start();
 */

import { isPaused, onPauseStateChange } from './pause.js';

type PollFn = () => Promise<void>;

interface Job {
  name: string;
  intervalMs: number;
  fn: PollFn;
  timer: ReturnType<typeof setTimeout> | null;
  lastRun: number;
}

export interface Scheduler {
  register(name: string, intervalMs: number, fn: PollFn): void;
  /**
   * Update the interval for an existing named job (governor drives cadence).
   * If the job doesn't exist, silently ignored.
   */
  setInterval(name: string, intervalMs: number): void;
  start(): void;
  stop(): void;
  runAll(): Promise<void>;
}

export function createScheduler(): Scheduler {
  const jobs = new Map<string, Job>();
  let running = false;

  function scheduleJob(job: Job): void {
    if (!running) return;
    if (job.timer !== null) clearTimeout(job.timer);
    job.timer = setTimeout(async () => {
      if (!running || isPaused() || document.hidden) {
        scheduleJob(job); // retry later
        return;
      }
      try {
        await job.fn();
      } catch (err) {
        console.warn(`[scheduler] ${job.name} poll error`, err);
      }
      job.lastRun = Date.now();
      scheduleJob(job); // reschedule
    }, job.intervalMs);
  }

  function register(name: string, intervalMs: number, fn: PollFn): void {
    jobs.set(name, { name, intervalMs, fn, timer: null, lastRun: 0 });
  }

  function setInterval(name: string, intervalMs: number): void {
    const job = jobs.get(name);
    if (!job) return;
    if (job.intervalMs === intervalMs) return; // no change

    job.intervalMs = intervalMs;

    // Re-arm the timer with the new interval if running
    if (running && job.timer !== null) {
      clearTimeout(job.timer);
      job.timer = null;
      scheduleJob(job);
    }
  }

  function start(): void {
    if (running) return;
    running = true;
    for (const job of jobs.values()) {
      scheduleJob(job);
    }
  }

  function stop(): void {
    running = false;
    for (const job of jobs.values()) {
      if (job.timer !== null) {
        clearTimeout(job.timer);
        job.timer = null;
      }
    }
  }

  async function runAll(): Promise<void> {
    const promises = Array.from(jobs.values()).map(async (job) => {
      try {
        await job.fn();
        job.lastRun = Date.now();
      } catch (err) {
        console.warn(`[scheduler] ${job.name} initial run error`, err);
      }
    });
    await Promise.all(promises);
  }

  // Wire up pause/visibility integration
  onPauseStateChange((paused) => {
    if (paused) {
      // Cancel timers but leave running=true; jobs re-check isPaused() on fire
      for (const job of jobs.values()) {
        if (job.timer !== null) {
          clearTimeout(job.timer);
          job.timer = null;
        }
      }
    } else {
      // Resume: re-arm all jobs (they'll fire after their interval)
      if (running) {
        for (const job of jobs.values()) {
          scheduleJob(job);
        }
      }
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && running && !isPaused()) {
      // Page became visible — re-arm all jobs
      for (const job of jobs.values()) {
        if (job.timer === null) {
          scheduleJob(job);
        }
      }
    }
  });

  return { register, setInterval, start, stop, runAll };
}
