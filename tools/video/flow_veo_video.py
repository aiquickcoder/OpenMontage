"""Generate video with Veo 3.1 inside Google Flow, billed to a Google AI Pro/Ultra
subscription (Flow credits) instead of the paid Gemini API.

Drives the ``gflow`` CLI (see tools/flow/gflow_client.py). Same operation
vocabulary as ``veo_video`` so selectors and pipeline skills can route to it
with ``allowed_providers: ["google_flow"]``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)
from tools.flow import gflow_client
from tools.flow.credit_ledger import (
    VIDEO_CREDITS,
    CreditLedger,
    FlowBudgetExceeded,
    video_cost,
)

OPERATION_TO_COMMAND = {
    "text_to_video": "t2v",
    "image_to_video": "i2v",
    "first_last_frame_to_video": "i2v",
    "reference_to_video": "r2v",
}
REFERENCE_CAPS = {"omni-flash": 7, "veo-lite": 3, "veo-fast": 3, "veo-lite-lp": 3, "veo-quality": 0}


def _parse_duration(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    text = str(value).lower().strip().rstrip("s")
    try:
        seconds = int(float(text))
    except ValueError:
        return None
    # Veo in Flow only offers 4/6/8 s (omni-flash also 10): snap up.
    for allowed in (4, 6, 8, 10):
        if seconds <= allowed:
            return allowed
    return 8


class FlowVeoVideo(BaseTool):
    name = "flow_veo_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "google_flow"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []  # checked dynamically: gflow binary on PATH or GFLOW_BIN
    install_instructions = (
        "Install gflow-cli and log into Google Flow with an AI Pro/Ultra account:\n"
        "  uv tool install gflow-cli==" + gflow_client.PINNED_VERSION + "\n"
        "  uv tool run --from gflow-cli playwright install chromium\n"
        "  gflow auth login --browser chrome\n"
        "See docs/FLOW_SETUP.md."
    )
    agent_skills = ["ai-video-gen"]

    capabilities = [
        "text_to_video",
        "image_to_video",
        "reference_to_video",
        "first_last_frame_to_video",
    ]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "reference_to_video": True,
        "first_last_frame_to_video": True,
        "native_audio": True,
        "dialogue_generation": True,
        "ambient_sound": True,
        "subscription_billing": True,
    }
    best_for = [
        "Veo 3.1 with native dialogue and sound on a Google AI Pro subscription",
        "keyframe-driven animation (image_to_video from an approved storyboard frame)",
        "3D animated / cartoon shots with spoken Russian or English dialogue",
    ]
    not_good_for = [
        "headless servers (needs a visible Chrome)",
        "high-volume batch runs (anti-bot pacing, monthly credit pool)",
        "clips longer than 8 s per shot",
    ]
    fallback_tools = ["veo_video", "gemini_omni_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "operation": {
                "type": "string",
                "enum": list(OPERATION_TO_COMMAND),
                "default": "text_to_video",
            },
            "model": {
                "type": "string",
                "enum": sorted(VIDEO_CREDITS),
                "default": "veo-fast",
                "description": "gflow model alias. Credits per clip: "
                + ", ".join(f"{k}={v}" for k, v in sorted(VIDEO_CREDITS.items())),
            },
            "duration": {
                "description": "Seconds (4, 6, 8; 10 on omni-flash). '8s' also accepted.",
                "type": ["integer", "string"],
                "default": 8,
            },
            "aspect_ratio": {"type": "string", "enum": ["9:16", "16:9"], "default": "9:16"},
            "count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
            "image_path": {"type": "string", "description": "Initial frame (image_to_video)"},
            "first_frame_path": {"type": "string", "description": "Alias of image_path"},
            "last_frame_path": {"type": "string", "description": "End frame (first_last_frame_to_video)"},
            "reference_image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ingredients for reference_to_video (max 3 on Veo, 7 on omni-flash)",
            },
            "flow_project": {
                "type": "string",
                "description": "Existing Flow project id to generate in (keeps a production's media together)",
            },
            "output_path": {"type": "string"},
            "project_dir": {"type": "string", "description": "projects/<id> — credits are booked to its ledger"},
            "scene_id": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=1500, vram_mb=0, disk_mb=300, network_required=True
    )
    # Never auto-retry: a post-submit failure may already have spent credits.
    retry_policy = RetryPolicy(max_retries=0)
    idempotency_key_fields = ["prompt", "operation", "model", "duration", "image_path"]
    side_effects = [
        "spends Google Flow subscription credits",
        "opens a visible Chrome window logged into Google Flow",
        "writes video file to output_path",
    ]
    user_visible_verification = [
        "Watch the clip: character on-model, hands/faces clean, product shape and colors correct",
        "Listen: dialogue language, pronunciation, voice matches the character bible",
    ]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if gflow_client.is_installed() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Subscription-billed: no per-call USD. Credits are reported by estimate_credits().
        return 0.0

    def estimate_credits(self, inputs: dict[str, Any]) -> int:
        return video_cost(inputs.get("model", "veo-fast"), inputs.get("count", 1))

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        per_clip = {"veo-quality": 240.0, "veo-fast": 120.0}.get(inputs.get("model", "veo-fast"), 90.0)
        return per_clip + gflow_client._min_interval()

    def dry_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        info = super().dry_run(inputs)
        project_dir = self._project_dir(inputs)
        try:
            args = self._build_args(inputs)
            problem = None
        except ValueError as exc:
            args, problem = None, str(exc)
        info.update({
            "estimated_credits": self.estimate_credits(inputs),
            "ledger": CreditLedger(project_dir).summary(),
            "gflow_args": args,
            "would_execute": problem is None and self.get_status() == ToolStatus.AVAILABLE,
            "problem": problem,
        })
        return info

    @staticmethod
    def _project_dir(inputs: dict[str, Any]) -> Optional[Path]:
        if inputs.get("project_dir"):
            return Path(inputs["project_dir"])
        from lib.events import infer_project_dir

        return infer_project_dir(inputs)

    def _build_args(self, inputs: dict[str, Any]) -> list[str]:
        operation = inputs.get("operation") or "text_to_video"
        if operation not in OPERATION_TO_COMMAND:
            raise ValueError(f"Unsupported operation {operation!r}")
        model = inputs.get("model") or "veo-fast"
        if model not in VIDEO_CREDITS:
            raise ValueError(f"Unknown model {model!r}; use one of {sorted(VIDEO_CREDITS)}")
        count = int(inputs.get("count") or 1)
        command = OPERATION_TO_COMMAND[operation]
        args = ["video", command]

        first = inputs.get("image_path") or inputs.get("first_frame_path")
        last = inputs.get("last_frame_path")
        refs = list(inputs.get("reference_image_paths") or [])

        if command == "i2v":
            if not first:
                raise ValueError(f"{operation} needs image_path (the initial frame)")
            args += ["--initial-frame", self._existing(first)]
            if last:
                args += ["--end-frame", self._existing(last)]
            elif operation == "first_last_frame_to_video":
                raise ValueError("first_last_frame_to_video needs last_frame_path")
        elif command == "r2v":
            cap = REFERENCE_CAPS[model]
            if not refs:
                raise ValueError("reference_to_video needs reference_image_paths")
            if len(refs) > cap:
                raise ValueError(f"{model} accepts at most {cap} reference images, got {len(refs)}")
            for ref in refs:
                args += ["--ref", self._existing(ref)]

        args += ["--model", model, "--aspect", inputs.get("aspect_ratio") or "9:16"]
        duration = _parse_duration(inputs.get("duration", 8))
        if duration == 10 and model != "omni-flash":
            duration = 8
        if duration:
            args += ["--duration", str(duration)]
        if count > 1:
            args += ["--count", str(count)]
        if inputs.get("flow_project"):
            args += ["--project", inputs["flow_project"]]

        output_path = inputs.get("output_path")
        if output_path and count == 1:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            args += ["-o", str(output_path)]
        elif output_path:
            out_dir = Path(output_path).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            args += ["--out-dir", str(out_dir)]
        args.append(inputs["prompt"])
        return args

    @staticmethod
    def _existing(path: str) -> str:
        # Flow media UUIDs pass through; local paths must exist before we spend credits.
        if len(path) == 36 and path.count("-") == 4 and not Path(path).exists():
            return path
        resolved = Path(path)
        if not resolved.is_file():
            raise ValueError(f"Input image not found: {path}")
        return str(resolved.resolve())

    @staticmethod
    def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("videos", "results"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
        return [payload] if payload else []

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(success=False, error="gflow not installed. " + self.install_instructions)
        try:
            args = self._build_args(inputs)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        model = inputs.get("model") or "veo-fast"
        credits = self.estimate_credits(inputs)
        ledger = CreditLedger(self._project_dir(inputs))
        try:
            entry_id = ledger.reserve(
                tool=self.name,
                model=model,
                credits=credits,
                note=f"{inputs.get('scene_id') or ''} {inputs['prompt'][:120]}",
            )
        except FlowBudgetExceeded as exc:
            return ToolResult(success=False, error=str(exc))

        try:
            run = gflow_client.run(args)
        except gflow_client.GflowError as exc:
            ledger.settle(entry_id, charged=exc.charged)
            return ToolResult(
                success=False,
                error=str(exc),
                data={
                    "exit_code": exc.exit_code,
                    "error_class": exc.error_class,
                    "credits_possibly_spent": credits if exc.charged else 0,
                    "ledger": ledger.summary(),
                },
                duration_seconds=round(time.time() - start, 2),
                model=model,
            )

        results = self._results(run.payload)
        succeeded = [r for r in results if r.get("succeeded", r.get("status") == "ok")]
        media_ids = [r["media_id"] for r in results if r.get("media_id")]
        paths = [r["local_path"] for r in succeeded if r.get("local_path")]
        ledger.settle(entry_id, charged=True, media_ids=media_ids)

        if not paths:
            reasons = [x for r in results for x in (r.get("failure_reasons") or [])]
            return ToolResult(
                success=False,
                error="Flow reported no downloadable video"
                + (f": {reasons}" if reasons else "")
                + ". If media_ids are present the clip may be recoverable with "
                "`gflow data list videos` / the gflow_download_media MCP tool.",
                data={"media_ids": media_ids, "payload": run.payload, "ledger": ledger.summary()},
                duration_seconds=round(time.time() - start, 2),
                model=model,
            )

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": model,
                "operation": inputs.get("operation") or "text_to_video",
                "prompt": inputs["prompt"],
                "output": paths[0],
                "output_path": paths[0],
                "outputs": paths,
                "media_id": media_ids[0] if media_ids else None,
                "media_ids": media_ids,
                "flow_project": run.payload.get("project_id") or inputs.get("flow_project"),
                "credits_spent": credits,
                "ledger": ledger.summary(),
                "request": run.payload.get("request"),
            },
            artifacts=paths,
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )
