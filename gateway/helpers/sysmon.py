"""Sysmon helper — fetches the live snapshot from scout-daemon RPC,
then asks an LLM to interpret it in plain language for the user.

M6.2: full body. Replaces the M2.2 skeleton.
"""

from __future__ import annotations

import json
import logging

from gateway.helpers.base import (
    BaseHelper, HelperResult, HelperTask, ResultBuilder,
    SchemaValidationError, load_prompt, parse_with_schema,
)
from gateway.helpers.shapes import SysmonPlan
from gateway import sysmon_client

log = logging.getLogger("gateway.helpers.sysmon")


class SysmonHelper(BaseHelper):
    role = "sysmon"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", SysmonPlan)
        super().__init__(**kwargs)

    async def invoke(self, task: HelperTask) -> HelperResult:
        rb = ResultBuilder(role=self.role, model_id=self.model_id,
                           parent_id=task.parent_id)

        # 1. Fetch the live snapshot from the daemon.
        snapshot = task.inputs.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = await sysmon_client.fetch_snapshot()
        if snapshot is None:
            return rb.fail(
                "scout-daemon RPC unreachable at 127.0.0.1:8767",
            ).build()

        # 2. Compose user message: snapshot + user's question.
        user_msg = task.inputs.get("user_msg") or task.goal
        try:
            system = load_prompt(self.prompt_name)
        except FileNotFoundError as e:
            return rb.fail(str(e)).build()

        user = json.dumps({
            "goal": task.goal,
            "inputs": {"snapshot": snapshot, "user_msg": user_msg},
        }, indent=2, default=str)

        # 3. Call the LLM.
        import asyncio
        try:
            text, t_in, t_out = await asyncio.wait_for(
                self.invoker.chat(
                    model=self.ollama_name,
                    system=system, user=user,
                    params=self.params, use_cpu=task.use_cpu,
                ),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError:
            return rb.fail(f"sysmon timed out after {self.timeout_s}s").build()
        except Exception as e:  # noqa: BLE001
            log.exception("sysmon unexpected")
            return rb.fail(f"unexpected: {type(e).__name__}: {e}").build()
        rb.add_tokens(t_in, t_out)
        rb.raw_text = text

        # 4. Parse output (or fallback to a deterministic summary if
        #    the LLM emits non-JSON — sysmon is critical enough that
        #    we want to always return something useful).
        try:
            parsed = parse_with_schema(text, SysmonPlan)
            rb.output = parsed.model_dump()
        except SchemaValidationError:
            # Fallback: build a SysmonPlan directly from the snapshot.
            rb.output = self._fallback_plan(snapshot, user_msg)
            rb.confidence = "low"

        # Make sure the snapshot fields are present even if the LLM
        # forgot them — the synthesizer should always have real numbers.
        for key in ("gpu_temps", "gpu_vram_used_pct", "disk_free_gb"):
            if key in snapshot and not rb.output.get(key):
                rb.output[key] = snapshot[key]
        if rb.output.get("game_running") is None and snapshot.get("game_running"):
            rb.output["game_running"] = snapshot["game_running"]
        # game_gpu is a deterministic NVML reading — always trust the
        # snapshot over whatever the LLM emitted (or didn't).
        if snapshot.get("game_gpu") is not None:
            rb.output["game_gpu"] = snapshot["game_gpu"]

        # Build a one-line output_summary the synthesizer reads. With
        # game_gpu in here, Hive no longer guesses which GPU a game
        # is on from heat readings.
        if not rb.output.get("summary"):
            rb.output["summary"] = self._compose_summary(rb.output)

        return rb.build()

    @staticmethod
    def _compose_summary(out: dict) -> str:
        bits = []
        temps = out.get("gpu_temps") or {}
        if isinstance(temps, dict) and temps:
            try:
                vals = [int(v) for v in temps.values()]
                bits.append(f"GPUs at {sorted(vals)} C")
            except (TypeError, ValueError):
                pass
        game = out.get("game_running")
        gpu = out.get("game_gpu")
        if game and gpu is not None:
            bits.append(f"{game} running on GPU {gpu}")
        elif game:
            bits.append(f"{game} running")
        return "; ".join(bits) or "no readings"

    @staticmethod
    def _fallback_plan(snapshot: dict, user_msg: str) -> dict:
        gpu_temps = snapshot.get("gpu_temps") or {}
        if isinstance(gpu_temps, dict) and gpu_temps:
            try:
                vals = [int(v) for v in gpu_temps.values()]
                hottest = max(vals)
                summary = f"GPUs at {sorted(vals)} C; hottest {hottest} C"
            except (TypeError, ValueError):
                summary = "GPU temps available"
        else:
            summary = "no GPU data"
        return {
            "summary": summary,
            "gpu_temps": gpu_temps,
            "gpu_vram_used_pct": snapshot.get("gpu_vram_used_pct") or {},
            "disk_free_gb": snapshot.get("disk_free_gb") or {},
            "game_running": snapshot.get("game_running"),
            "game_gpu": snapshot.get("game_gpu"),
            "alerts": snapshot.get("alerts") or [],
            "plan": ["fetched snapshot", "fallback summary (LLM JSON failed)"],
        }
