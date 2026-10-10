"""FLOW_DRIVER=agent backend for the Flow tools.

Accounts on Flow's agent-only composer cannot be driven by gflow, so flow_image /
flow_veo_video translate their inputs into an explicit brief for Flow's creation
agent and run it through FlowAgentDriver. Results keep the gflow-backend shape
(local paths, media ids, actual credits) so pipeline stages don't care which
driver produced them.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from tools.flow import agent_driver

VIDEO_MODEL_LABELS = {
    "veo-lite-lp": "Veo 3.1 - Lite [Lower Priority]",
    "veo-lite": "Veo 3.1 - Lite",
    "veo-fast": "Veo 3.1 - Fast",
    "veo-quality": "Veo 3.1 - Quality",
    "omni-flash": "Omni 1.1 Flash",
}
IMAGE_MODEL_LABELS = {
    "nano-pro": "Nano Banana Pro",
    "nano2": "Nano Banana 2",
    "nano2-lite": "Nano Banana 2 Lite",
    "image4": "Imagen 4",
    "imagen4": "Imagen 4",
}


class AgentGenerationError(RuntimeError):
    def __init__(self, message: str, *, charged: bool, data: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.charged = charged
        self.data = data or {}


@dataclass
class AgentGeneration:
    paths: list[str]
    media_ids: list[str]
    flow_project: Optional[str]
    credits_spent: Optional[int]
    models_mentioned: list[str] = field(default_factory=list)
    reply: str = ""


def is_selected() -> bool:
    return os.environ.get("FLOW_DRIVER", "gflow").strip().lower() == "agent"


def profile_name() -> str:
    return os.environ.get("FLOW_PROFILE", "default")


def is_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return agent_driver.profile_dir(profile_name()).is_dir()


def video_brief(inputs: dict[str, Any], model: str, duration: Optional[int]) -> tuple[str, list[str]]:
    operation = inputs.get("operation") or "text_to_video"
    count = int(inputs.get("count") or 1)
    aspect = inputs.get("aspect_ratio") or "9:16"
    first = inputs.get("image_path") or inputs.get("first_frame_path")
    last = inputs.get("last_frame_path")
    refs = list(inputs.get("reference_image_paths") or [])

    attachments: list[str] = []
    lines = [
        f"Generate exactly {count} video{'s' if count > 1 else ''} and nothing else "
        "(no images, no extra variations, do not ask follow-up questions).",
        f"Model: {VIDEO_MODEL_LABELS.get(model, model)}. Aspect ratio {aspect}."
        + (f" Duration {duration} seconds." if duration else ""),
    ]
    if operation in ("image_to_video", "first_last_frame_to_video"):
        attachments.append(first)
        lines.append("Use attached image 1 as the exact FIRST frame (image-to-video); keep its "
                     "characters, composition and style unchanged.")
        if last:
            attachments.append(last)
            lines.append("Use attached image 2 as the exact LAST frame.")
    elif operation == "reference_to_video":
        attachments += refs
        lines.append(f"Use the {len(refs)} attached images as ingredients/references "
                     "for characters and objects (reference-to-video); keep them on-model.")
    lines.append("Shot description (follow literally, keep any quoted dialogue word for word, "
                 "native audio on):")
    lines.append(inputs["prompt"])
    return "\n".join(lines), [a for a in attachments if a]


def image_brief(inputs: dict[str, Any], model: str, aspect: str, count: int) -> tuple[str, list[str]]:
    refs = [r for r in (inputs.get("image_paths") or []) if r]
    lines = [
        f"Generate exactly {count} image{'s' if count > 1 else ''} and nothing else "
        "(no video, do not ask follow-up questions).",
        f"Model: {IMAGE_MODEL_LABELS.get(model, model)}. Aspect ratio {aspect}.",
    ]
    if refs:
        lines.append(f"Use the {len(refs)} attached images as references (image-to-image); keep "
                     "characters, products and style on-model.")
    lines.append("Image description (follow literally):")
    lines.append(inputs["prompt"])
    return "\n".join(lines), refs


def _project_url(flow_project: Optional[str]) -> Optional[str]:
    if not flow_project:
        return None
    if flow_project.startswith("http"):
        return flow_project
    return f"https://flow.google.com/project/{flow_project}"


def _place(downloaded: list[Path], output_path: Optional[str]) -> list[str]:
    if not output_path:
        return [str(p) for p in downloaded]
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    placed = []
    for i, src in enumerate(downloaded):
        dest = out if i == 0 else out.with_name(f"{out.stem}_{i + 1}{out.suffix}")
        shutil.move(str(src), dest)
        placed.append(str(dest))
    return placed


def generate(kind: str, brief: str, attachments: list[str], *, count: int,
             output_path: Optional[str], flow_project: Optional[str],
             timeout: float, driver_cls: Any = None) -> AgentGeneration:
    """Run one agent request. Raises AgentGenerationError; `charged` is True once submitted."""
    for path in attachments:
        if not Path(path).is_file():
            raise AgentGenerationError(f"Input image not found: {path}", charged=False)
    driver_cls = driver_cls or agent_driver.FlowAgentDriver
    staging = Path(output_path).parent if output_path else Path.cwd()
    staging = staging / ".flow_agent_tmp"
    try:
        with driver_cls(profile_name()) as drv:
            drv.credits()
            project_url = drv.open_project(_project_url(flow_project))
            res = drv.ask(brief, expect=kind, count=count, timeout=timeout, attachments=attachments)
            data = {"project_url": project_url, "models_mentioned": res.models_mentioned,
                    "credits_before": res.credits_before, "credits_after": res.credits_after,
                    "reply": res.reply_text[-600:]}
            if res.refused:
                raise AgentGenerationError(f"Flow agent refused: {res.refused}",
                                           charged=bool(res.credits_spent), data=data)
            if not res.media:
                raise AgentGenerationError(
                    "Flow agent finished without producing media (it may have asked a question "
                    "or picked an unavailable model) — see data.reply", charged=bool(res.credits_spent), data=data)
            downloaded = [drv.download(m, staging) for m in res.media[:count]]
    except AgentGenerationError:
        raise
    except Exception as exc:  # browser/UI failure: submission state unknown → assume charged
        raise AgentGenerationError(f"Flow agent driver failed: {exc}", charged=True) from exc

    paths = _place(downloaded, output_path)
    if output_path:
        shutil.rmtree(staging, ignore_errors=True)
    project_id = project_url.rstrip("/").split("/project/")[-1].split("?")[0] if "/project/" in project_url else None
    return AgentGeneration(
        paths=paths,
        media_ids=[m.media_id for m in res.media[:count]],
        flow_project=project_id,
        credits_spent=res.credits_spent,
        models_mentioned=res.models_mentioned,
        reply=res.reply_text[-600:],
    )
