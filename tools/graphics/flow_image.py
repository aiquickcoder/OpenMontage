"""Generate images with Nano Banana (Gemini image) / Imagen inside Google Flow,
on a Google AI Pro/Ultra subscription instead of the paid Gemini API.

Image generation in Flow uses a daily per-model quota rather than monthly
credits, so iterating on character sheets and storyboard keyframes here is
effectively free — the expensive step is animating them with flow_veo_video.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

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

MODEL_REFERENCE_CAPS = {"nano-pro": 10, "nano2": 10, "nano2-lite": 3, "image4": 3}
ASPECTS = ["9:16", "16:9", "1:1", "4:3", "3:4"]


def _nearest_aspect(width: int, height: int) -> str:
    target = width / height
    return min(ASPECTS, key=lambda a: abs(target - int(a.split(":")[0]) / int(a.split(":")[1])))


class FlowImage(BaseTool):
    name = "flow_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "google_flow"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []  # checked dynamically: gflow binary on PATH or GFLOW_BIN
    install_instructions = (
        "Install gflow-cli and log into Google Flow with an AI Pro/Ultra account:\n"
        "  uv tool install gflow-cli==" + gflow_client.PINNED_VERSION + "\n"
        "  gflow auth login --browser chrome\n"
        "See docs/FLOW_SETUP.md."
    )
    agent_skills = []

    capabilities = ["generate_image", "generate_illustration", "text_to_image", "image_to_image"]
    supports = {
        "negative_prompt": False,
        "seed": False,
        "custom_size": False,
        "aspect_ratio": True,
        "image_edit": True,
        "reference_images": True,
        "subscription_billing": True,
    }
    best_for = [
        "character turnaround and expression sheets for 3D cartoon characters",
        "storyboard keyframes that keep characters and products on-model via reference images",
        "Nano Banana Pro quality on a Google AI Pro subscription",
    ]
    not_good_for = [
        "exact pixel dimensions (aspect ratios only)",
        "headless servers (needs a visible Chrome)",
        "rendering exact logo/brand text (composite real logos in post)",
    ]
    fallback_tools = ["google_imagen"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {
                "type": "string",
                "enum": sorted(MODEL_REFERENCE_CAPS),
                "default": "nano-pro",
                "description": "nano-pro = Nano Banana Pro (best quality/consistency), "
                "nano2 = Nano Banana 2 (faster), image4 = Imagen 4",
            },
            "aspect_ratio": {"type": "string", "enum": ASPECTS, "default": "9:16"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "number_of_images": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Reference images (character sheets, product photos, previous keyframe). "
                "Switches to image-to-image. Up to 10 on nano-pro/nano2.",
            },
            "flow_project": {"type": "string"},
            "output_path": {"type": "string"},
            "project_dir": {"type": "string"},
            "scene_id": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=1500, vram_mb=0, disk_mb=100, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=0)
    idempotency_key_fields = ["prompt", "model", "aspect_ratio", "image_paths"]
    side_effects = [
        "uses the Google Flow daily image quota",
        "opens a visible Chrome window logged into Google Flow",
        "writes PNG files",
    ]
    user_visible_verification = ["Inspect images for on-model characters and correct product details"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if gflow_client.is_installed() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 45.0 + gflow_client._min_interval()

    def _build_args(self, inputs: dict[str, Any]) -> list[str]:
        model = inputs.get("model") or "nano-pro"
        if model not in MODEL_REFERENCE_CAPS:
            raise ValueError(f"Unknown model {model!r}; use one of {sorted(MODEL_REFERENCE_CAPS)}")
        refs = [r for r in (inputs.get("image_paths") or []) if r]
        if len(refs) > MODEL_REFERENCE_CAPS[model]:
            raise ValueError(f"{model} accepts at most {MODEL_REFERENCE_CAPS[model]} references, got {len(refs)}")

        aspect = inputs.get("aspect_ratio")
        if not aspect and inputs.get("width") and inputs.get("height"):
            aspect = _nearest_aspect(int(inputs["width"]), int(inputs["height"]))
        count = int(inputs.get("number_of_images") or 1)

        args = ["image", "i2i" if refs else "t2i"]
        for ref in refs:
            path = Path(ref)
            if not path.is_file():
                raise ValueError(f"Reference image not found: {ref}")
            args += ["--ref", str(path.resolve())]
        args += ["--model", model, "--aspect", aspect or "9:16", "-n", str(count)]
        if inputs.get("flow_project"):
            args += ["--project", inputs["flow_project"]]

        output_path = inputs.get("output_path")
        if output_path and count == 1:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            args += ["-o", str(output_path)]
        elif output_path:
            out_dir = Path(output_path).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            args += ["--out", str(out_dir)]
        args.append(inputs["prompt"])
        return args

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(success=False, error="gflow not installed. " + self.install_instructions)
        try:
            args = self._build_args(inputs)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        model = inputs.get("model") or "nano-pro"
        try:
            run = gflow_client.run(args, timeout=10 * 60)
        except gflow_client.GflowError as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                data={"exit_code": exc.exit_code, "error_class": exc.error_class},
                duration_seconds=round(time.time() - start, 2),
                model=model,
            )

        images = [i for i in run.payload.get("images") or [] if isinstance(i, dict)]
        paths = [i["local_path"] for i in images if i.get("local_path")]
        if not paths:
            return ToolResult(
                success=False,
                error="Flow returned no images",
                data={"payload": run.payload},
                duration_seconds=round(time.time() - start, 2),
                model=model,
            )
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": model,
                "prompt": inputs["prompt"],
                "output": paths[0],
                "output_path": paths[0],
                "outputs": paths,
                "media_ids": [i.get("media_name") for i in images],
                "seeds": [i.get("seed") for i in images],
                "flow_project": run.payload.get("project_id"),
                "reference_count": len(inputs.get("image_paths") or []),
            },
            artifacts=paths,
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            seed=images[0].get("seed"),
            model=model,
        )
