"""Parsing helpers of the Flow agent-composer driver (no browser)."""

from tools.flow import agent_driver as ad


def _frame(rpc: str, payload: str) -> str:
    inner = f'[["wrb.fr","{rpc}","{payload}",null,null,null,"generic"]]'
    return f")]}}'\n\n{len(inner)}\n{inner}\n"


def test_gate_patch_keeps_length():
    body = _frame("KV2T2d", "[4]")
    patched = ad.GATE_RESULT.sub(r'\1"[ ]"', body)
    assert '"KV2T2d","[ ]"' in patched
    assert len(patched) == len(body)


def test_gate_patch_ignores_other_rpcs():
    body = _frame("NfrxTb", "[4]")
    assert ad.GATE_RESULT.sub(r'\1"[ ]"', body) == body


def test_media_since_extracts_signed_urls():
    drv = ad.FlowAgentDriver.__new__(ad.FlowAgentDriver)
    drv._bodies = [
        ("old", 'https://flow-content.google/image/11111111-1111-1111-1111-111111111111?Expires\\u003d1\\u0026Signature\\u003dold'),
        ("new", '\\"https://flow-content.google/video/3c41c804-ad9e-4dfd-8af1-3407a07fe088?Expires\\u003d1791674439'
                '\\u0026KeyName\\u003dlabs-flow-prod-cdn-key\\u0026Signature\\u003dbXY2rOb0\\",'),
    ]
    media = drv._media_since(1)
    assert list(media) == ["3c41c804-ad9e-4dfd-8af1-3407a07fe088"]
    m = media["3c41c804-ad9e-4dfd-8af1-3407a07fe088"]
    assert m.kind == "video"
    assert m.url.endswith("Signature=bXY2rOb0")
    assert "&KeyName=" in m.url


def test_last_credits():
    drv = ad.FlowAgentDriver.__new__(ad.FlowAgentDriver)
    drv._bodies = [("u", _frame("nzlxg", "[50,3,8,1,null,50,[1791724968,711746000]]"))]
    assert drv._last_credits() == 50


def test_credits_spent():
    r = ad.AgentResult(project_url="x", credits_before=50, credits_after=45)
    assert r.credits_spent == 5
    assert ad.AgentResult(project_url="x").credits_spent is None


def test_profile_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_PROFILE_DIR", str(tmp_path))
    assert ad.profile_dir("ultra") == tmp_path


def test_models_mentioned():
    assert ad.models_mentioned("calling generate_video with Veo 3.1 - Lite [Lower Priority]") == ["veo-lite-lp"]
    assert ad.models_mentioned('{"model": "Veo 3.1 - Fast"}') == ["veo-fast"]
    assert ad.models_mentioned("using Nano Banana Pro") == ["nano-pro"]
    assert ad.models_mentioned("hello") == []


def test_refusal_code():
    assert ad.refusal_code('[7,null,[["x",["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]]') == "PUBLIC_ERROR_UNUSUAL_ACTIVITY"
    assert ad.refusal_code("ok") is None


# -- agent backend ---------------------------------------------------------
from tools.flow import agent_backend as ab  # noqa: E402


def test_video_brief_i2v(tmp_path):
    frame = tmp_path / "k.png"
    frame.write_bytes(b"x")
    brief, att = ab.video_brief(
        {"prompt": "Лис машет лапой: «Пиши МУЛЬТ!»", "operation": "image_to_video", "image_path": str(frame)},
        "veo-lite", 8)
    assert att == [str(frame)]
    assert "Veo 3.1 - Lite." in brief and "8 seconds" in brief and "FIRST frame" in brief
    assert "«Пиши МУЛЬТ!»" in brief


def test_video_brief_r2v():
    brief, att = ab.video_brief({"prompt": "p", "operation": "reference_to_video",
                                 "reference_image_paths": ["a.png", "b.png"]}, "omni-flash", 10)
    assert att == ["a.png", "b.png"] and "2 attached images" in brief and "Omni 1.1 Flash" in brief


class _FakeDriver:
    spent = 10
    media_kind = "video"

    def __init__(self, profile):
        self.attached = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def credits(self):
        return 50

    def open_project(self, url=None):
        return url or "https://flow.google.com/project/11111111-2222-3333-4444-555555555555"

    def ask(self, brief, *, expect, count, timeout, attachments):
        media = [ad.Media(kind=self.media_kind, media_id=f"m{i}", url="u") for i in range(count)]
        return ad.AgentResult(project_url="p", media=media, credits_before=50,
                              credits_after=50 - self.spent, models_mentioned=["veo-lite"])

    def download(self, media, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"{media.media_id}.mp4"
        p.write_bytes(b"video")
        return p


def test_generate_places_output_and_reports_credits(tmp_path):
    out = tmp_path / "clips" / "shot1.mp4"
    gen = ab.generate("video", "brief", [], count=1, output_path=str(out), flow_project=None,
                      timeout=10, driver_cls=_FakeDriver)
    assert gen.paths == [str(out)] and out.read_bytes() == b"video"
    assert gen.credits_spent == 10
    assert gen.flow_project == "11111111-2222-3333-4444-555555555555"
    assert not (out.parent / ".flow_agent_tmp").exists()


def test_generate_missing_attachment_is_not_charged(tmp_path):
    import pytest

    with pytest.raises(ab.AgentGenerationError) as err:
        ab.generate("video", "b", [str(tmp_path / "nope.png")], count=1, output_path=None,
                    flow_project=None, timeout=1, driver_cls=_FakeDriver)
    assert err.value.charged is False


def test_veo_tool_agent_branch_books_measured_credits(tmp_path, monkeypatch):
    from tools.video.flow_veo_video import FlowVeoVideo

    monkeypatch.setenv("FLOW_DRIVER", "agent")
    monkeypatch.setenv("FLOW_PLAN", "free")
    monkeypatch.setattr(ab, "is_available", lambda: True)
    real_generate = ab.generate
    monkeypatch.setattr(ab, "generate", lambda *a, **k: real_generate(*a, **{**k, "driver_cls": _FakeDriver}))
    _FakeDriver.spent = 7
    res = FlowVeoVideo().execute({"prompt": "fox", "model": "veo-lite", "project_dir": str(tmp_path),
                                  "output_path": str(tmp_path / "s.mp4")})
    assert res.success, res.error
    assert res.data["driver"] == "agent" and res.data["credits_spent"] == 7
    assert res.data["ledger"]["committed"] == 7
    assert res.data["model_mismatch_suspected"] is False
