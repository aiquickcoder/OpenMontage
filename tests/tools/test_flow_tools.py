"""Flow (gflow-cli) providers, driven by a fake gflow binary — no browser, no credits."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from tools.flow import gflow_client
from tools.flow.credit_ledger import CreditLedger
from tools.graphics.flow_image import FlowImage
from tools.video.flow_veo_video import FlowVeoVideo

FAKE_GFLOW = r'''#!{python}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["FAKE_GFLOW_LOG"]).open("a").write(json.dumps(args) + "\n")
mode = os.environ.get("FAKE_GFLOW_MODE", "ok")
if mode == "waf":
    print(json.dumps({{"status": "fail", "error": {{"class": "WafRejectionError", "exit_code": 10,
        "retryable": True, "title": "403"}}}}))
    sys.exit(10)
if mode == "drift":
    print(json.dumps({{"status": "fail", "error": {{"class": "UiSelectorDriftError", "exit_code": 23,
        "retryable": False, "title": "drift"}}}}))
    sys.exit(23)
out = Path(args[args.index("-o") + 1]) if "-o" in args else Path(os.environ["FAKE_GFLOW_OUT"]) / "x.bin"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(b"data")
if args[0] == "video":
    print(json.dumps({{"status": "ok", "command": "video " + args[1], "media_id": "m-1",
        "generation_status": "MEDIA_GENERATION_STATUS_SUCCESSFUL", "succeeded": True,
        "local_path": str(out), "failure_reasons": [], "error_message": None,
        "request": {{"model": "veo_3_1_fast", "mode": args[1], "aspect": "9:16", "duration": 8, "count": 1, "seed": None}}}}))
else:
    print(json.dumps({{"status": "ok", "command": "image " + args[1], "project_id": "p-1", "model": "GEM_PIX_2",
        "count": 1, "images": [{{"media_name": "img-1", "seed": 7, "local_path": str(out)}}]}}))
'''


@pytest.fixture
def fake_gflow(tmp_path, monkeypatch):
    script = tmp_path / "gflow"
    script.write_text(FAKE_GFLOW.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("GFLOW_BIN", str(script))
    monkeypatch.setenv("FAKE_GFLOW_LOG", str(log))
    monkeypatch.setenv("FAKE_GFLOW_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FLOW_MIN_INTERVAL_SEC", "0")
    monkeypatch.setattr(gflow_client, "STATE_DIR", tmp_path / "state")

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return calls


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "frame.png"
    path.write_bytes(b"png")
    return path


def test_i2v_builds_command_and_books_credits(fake_gflow, frame, tmp_path):
    project = tmp_path / "proj"
    out = project / "assets" / "video" / "s1.mp4"
    result = FlowVeoVideo().execute({
        "prompt": "Pixar-style fox waves, говорит по-русски: «Привет!»",
        "operation": "image_to_video",
        "image_path": str(frame),
        "model": "veo-fast",
        "duration": "8s",
        "output_path": str(out),
        "project_dir": str(project),
    })
    assert result.success, result.error
    assert out.exists()
    assert result.data["media_id"] == "m-1"
    args = fake_gflow()[0]
    assert args[:2] == ["video", "i2v"]
    assert args[args.index("--initial-frame") + 1] == str(frame.resolve())
    assert args[args.index("--model") + 1] == "veo-fast"
    assert args[args.index("--duration") + 1] == "8"
    assert args[args.index("--aspect") + 1] == "9:16"
    assert "--json" in args
    assert CreditLedger(project).summary()["committed"] == 20


def test_budget_cap_blocks_before_calling_flow(fake_gflow, frame, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_CREDIT_BUDGET_PER_PROJECT", "50")
    result = FlowVeoVideo().execute({
        "prompt": "x", "operation": "image_to_video", "image_path": str(frame),
        "model": "veo-quality", "project_dir": str(tmp_path / "proj"),
    })
    assert not result.success
    assert "budget exceeded" in result.error
    assert fake_gflow() == []


def test_pre_submit_failure_is_refunded(fake_gflow, frame, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_MODE", "waf")
    project = tmp_path / "proj"
    result = FlowVeoVideo().execute({
        "prompt": "x", "operation": "image_to_video", "image_path": str(frame),
        "model": "veo-fast", "project_dir": str(project),
    })
    assert not result.success
    assert result.data["exit_code"] == 10
    assert "Hint" in result.error
    assert CreditLedger(project).summary()["committed"] == 0


def test_post_submit_failure_stays_charged(fake_gflow, frame, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GFLOW_MODE", "drift")
    project = tmp_path / "proj"
    result = FlowVeoVideo().execute({
        "prompt": "x", "operation": "image_to_video", "image_path": str(frame),
        "model": "veo-fast", "project_dir": str(project),
    })
    assert not result.success
    assert CreditLedger(project).summary()["committed"] == 20


def test_input_validation_never_reaches_flow(fake_gflow, frame, tmp_path):
    tool = FlowVeoVideo()
    missing = tool.execute({"prompt": "x", "operation": "image_to_video", "image_path": str(tmp_path / "nope.png")})
    too_many = tool.execute({
        "prompt": "x", "operation": "reference_to_video", "model": "veo-fast",
        "reference_image_paths": [str(frame)] * 4,
    })
    quality_r2v = tool.execute({
        "prompt": "x", "operation": "reference_to_video", "model": "veo-quality",
        "reference_image_paths": [str(frame)],
    })
    assert not missing.success and not too_many.success and not quality_r2v.success
    assert fake_gflow() == []


def test_flow_image_i2i_with_references(fake_gflow, frame, tmp_path):
    out = tmp_path / "kf" / "s1.png"
    result = FlowImage().execute({
        "prompt": "storyboard keyframe",
        "image_paths": [str(frame), str(frame)],
        "width": 1080, "height": 1920,
        "output_path": str(out),
    })
    assert result.success, result.error
    args = fake_gflow()[0]
    assert args[:2] == ["image", "i2i"]
    assert args.count("--ref") == 2
    assert args[args.index("--model") + 1] == "nano-pro"
    assert args[args.index("--aspect") + 1] == "9:16"
    assert result.data["output_path"] == str(out)


def test_registry_and_selectors_see_google_flow(fake_gflow):
    from tools.tool_registry import registry

    registry.discover()
    assert "flow_veo_video" in {t.name for t in registry.get_by_capability("video_generation")}
    assert "flow_image" in {t.name for t in registry.get_by_capability("image_generation")}
    assert {t.name for t in registry.get_by_provider("google_flow")} >= {
        "flow_veo_video", "flow_image", "flow_video_upscale"}
