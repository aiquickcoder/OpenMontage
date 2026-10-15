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
    assert list(media) == [("video", "3c41c804-ad9e-4dfd-8af1-3407a07fe088")]
    m = media[("video", "3c41c804-ad9e-4dfd-8af1-3407a07fe088")]
    assert m.kind == "video"
    assert m.url.endswith("Signature=bXY2rOb0")
    assert "&KeyName=" in m.url


def test_media_since_double_escaped_keeps_signed_url():
    # as seen on the server: StreamChat nests JSON, so '=' arrives as \\u003d
    drv = ad.FlowAgentDriver.__new__(ad.FlowAgentDriver)
    drv._bodies = [
        ("a", 'll,null,1,\\"https://flow-content.google/image/34b84c67-4990-44a9-a525-99c46d4ea231?Expires\\\\u003d1791721469'
              '\\\\u0026KeyName\\\\u003dlabs-flow-prod\\\\u0026Signature\\\\u003dsig\\"'),
        ("b", 'https://flow-content.google/image/34b84c67-4990-44a9-a525-99c46d4ea231?Expires\\'),
    ]
    m = drv._media_since(0)[("image", "34b84c67-4990-44a9-a525-99c46d4ea231")]
    assert m.url.endswith("?Expires=1791721469&KeyName=labs-flow-prod&Signature=sig")


def test_video_sharing_its_frame_id_is_a_new_result():
    # a finished queued clip reuses its first frame's media id (seen 2026-10-12)
    drv = ad.FlowAgentDriver.__new__(ad.FlowAgentDriver)
    mid = "588c3653-aca8-4f65-b379-99aa3328ba8b"
    drv._bodies = [
        ("upload", f"https://flow-content.google/image/{mid}?Expires=1&Signature=long-image-signature-xxxx"),
        ("done", f"https://flow-content.google/video/{mid}?Expires=1&Signature=v"),
    ]
    known = {("image", mid)}
    vids = drv._new_media(0, known, "video")
    assert [(m.kind, m.media_id) for m in vids] == [("video", mid)]


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
    assert "Veo 3.1 - Lite." in brief and "8 seconds" in brief and "FIRST frame" in brief and "file k.png" in brief
    assert "«Пиши МУЛЬТ!»" in brief


def test_video_brief_r2v():
    brief, att = ab.video_brief({"prompt": "p", "operation": "reference_to_video",
                                 "reference_image_paths": ["a.png", "b.png"]}, "omni-flash", 10)
    assert att == ["a.png", "b.png"] and "2 images I just uploaded (a.png, b.png)" in brief and "Omni 1.1 Flash" in brief


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

    declined = None

    def ask(self, brief, *, expect, count, timeout, attachments, confirm_up_to=None):
        _FakeDriver.last_confirm_up_to = confirm_up_to
        if self.declined is not None:
            return ad.AgentResult(project_url="p", credits_before=50, credits_after=50,
                                  declined_cost=self.declined)
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


def test_confirmation_request():
    asked = ("Generate exactly 1 video and nothing else.\n\nWould you like me to kick off this "
             "1 video generation, costing 15 credits?\n\nthumb_up")
    assert ad.confirmation_request(asked) == 15
    assert ad.confirmation_request("Shall I go ahead?") == 0
    assert ad.confirmation_request("Here's your cheerful cat chef! Let me know if you'd like more.") is None


def test_generate_declined_quote_is_error_not_charged(tmp_path):
    import pytest

    _FakeDriver.declined = 40
    try:
        with pytest.raises(ab.AgentGenerationError) as err:
            ab.generate("video", "b", [], count=1, output_path=None, flow_project=None,
                        timeout=1, confirm_up_to=20, driver_cls=_FakeDriver)
    finally:
        _FakeDriver.declined = None
    assert err.value.charged is False and "quoted 40" in str(err.value)


def test_veo_tool_passes_confirm_cap(tmp_path, monkeypatch):
    from tools.video.flow_veo_video import FlowVeoVideo

    monkeypatch.setenv("FLOW_DRIVER", "agent")
    monkeypatch.setenv("FLOW_PLAN", "free")
    monkeypatch.setattr(ab, "is_available", lambda: True)
    real_generate = ab.generate
    monkeypatch.setattr(ab, "generate", lambda *a, **k: real_generate(*a, **{**k, "driver_cls": _FakeDriver}))
    res = FlowVeoVideo().execute({"prompt": "fox", "model": "veo-lite", "project_dir": str(tmp_path),
                                  "output_path": str(tmp_path / "s.mp4")})
    assert res.success, res.error
    assert _FakeDriver.last_confirm_up_to == 20  # veo-lite on free = 10 → cap max(20, 20)


def test_dry_run_input_never_generates(tmp_path, monkeypatch):
    from tools.graphics.flow_image import FlowImage
    from tools.video.flow_veo_video import FlowVeoVideo

    monkeypatch.setenv("FLOW_DRIVER", "agent")
    monkeypatch.setattr(ab, "is_available", lambda: True)

    def boom(*a, **k):
        raise AssertionError("generate must not run on dry_run")

    monkeypatch.setattr(ab, "generate", boom)
    for tool in (FlowVeoVideo(), FlowImage()):
        res = tool.execute({"prompt": "test", "dry_run": True, "project_dir": str(tmp_path)})
        assert res.success and res.data["dry_run"] is True


def test_one_line_never_contains_enter():
    brief, _ = ab.image_brief({"prompt": "kitchen\nwith oven"}, "nano-pro", "9:16", 1)
    flat = ad.one_line(brief)
    assert "\n" not in flat and "\r" not in flat
    assert flat.startswith("Generate exactly 1 image") and flat.endswith("kitchen with oven")


def test_generate_quota_reached_says_stop(tmp_path):
    import pytest

    class Quota(_FakeDriver):
        def ask(self, brief, *, expect, count, timeout, attachments, confirm_up_to=None):
            return ad.AgentResult(project_url="p", credits_before=50, credits_after=50,
                                  refused="PUBLIC_ERROR_USER_QUOTA_REACHED", confirmed_costs=[10])

    with pytest.raises(ab.AgentGenerationError) as err:
        ab.generate("video", "b", [], count=1, output_path=None, flow_project=None, timeout=1, driver_cls=Quota)
    assert "AUTOPILOT_RESULT: BUDGET" in str(err.value) and err.value.charged is False


def test_proxy_settings_from_env(monkeypatch):
    from tools.flow import agent_driver
    monkeypatch.delenv("FLOW_PROXY", raising=False)
    assert agent_driver.proxy_settings() is None
    monkeypatch.setenv("FLOW_PROXY", "socks5://127.0.0.1:20900   # SE exit")
    assert agent_driver.proxy_settings() == {"server": "socks5://127.0.0.1:20900"}


def test_flow_plan_ignores_inline_comment(monkeypatch):
    from tools.flow import credit_ledger
    monkeypatch.setenv("FLOW_PLAN", "free                      # no paid plan")
    assert credit_ledger.flow_plan() == "free"


def test_failed_generation_books_measured_spend(tmp_path, monkeypatch):
    from tools.flow import credit_ledger
    ledger = credit_ledger.CreditLedger(tmp_path, budget=40)
    entry = ledger.reserve(tool="flow_veo_video", model="omni-flash", credits=15)
    ledger.settle(entry, charged=True, credits=7)
    assert ledger.summary()["committed"] == 7


class _QueuedDriver(_FakeDriver):
    """Flow charged the clip but it is still in the free queue when ask() times out."""

    def ask(self, brief, *, expect, count, timeout, attachments, confirm_up_to=None):
        return ad.AgentResult(project_url="p", credits_before=43, credits_after=33,
                              confirmed_costs=[10], models_mentioned=["veo-lite"])

    def collect(self, project_url, *, expect, count, timeout):
        _QueuedDriver.collected_from = project_url
        return [ad.Media(kind="video", media_id="late", url="u")]


def test_generate_charged_without_media_is_pending(tmp_path):
    import pytest

    with pytest.raises(ab.AgentGenerationError) as err:
        ab.generate("video", "b", [], count=1, output_path=None, flow_project=None,
                    timeout=1, driver_cls=_QueuedDriver)
    assert err.value.charged and err.value.data["pending"]
    assert "collect_from=" in str(err.value) and "Do NOT generate it again" in str(err.value)


def test_veo_tool_collect_from_downloads_without_charging(tmp_path, monkeypatch):
    from tools.video.flow_veo_video import FlowVeoVideo

    monkeypatch.setenv("FLOW_DRIVER", "agent")
    monkeypatch.setattr(ab, "is_available", lambda: True)
    real_collect = ab.collect
    monkeypatch.setattr(ab, "collect", lambda *a, **k: real_collect(*a, **{**k, "driver_cls": _QueuedDriver}))
    url = "https://flow.google.com/project/8d923a65-a7fa-442b-a994-171163f3fed2"
    out = tmp_path / "s1.mp4"
    res = FlowVeoVideo().execute({"prompt": "fox", "model": "veo-lite", "project_dir": str(tmp_path),
                                  "output_path": str(out), "collect_from": url})
    assert res.success, res.error
    assert res.data["credits_spent"] == 0 and out.read_bytes() == b"video"
    assert _QueuedDriver.collected_from == url
    assert not (tmp_path / "flow_credits.json").exists() or '"reserved"' not in (tmp_path / "flow_credits.json").read_text()


def test_video_timeout_by_plan(monkeypatch):
    monkeypatch.delenv("FLOW_VIDEO_TIMEOUT_SEC", raising=False)
    monkeypatch.setenv("FLOW_PLAN", "free   # note")
    assert ab.video_timeout() == 5400
    monkeypatch.setenv("FLOW_PLAN", "ultra")
    assert ab.video_timeout() == 1800
    monkeypatch.setenv("FLOW_VIDEO_TIMEOUT_SEC", "7200")
    assert ab.video_timeout() == 7200


class _UploadPage:
    """A page whose uploads only show up in the recorded bodies after a few waits."""

    def __init__(self, drv, url, delay):
        self.drv, self.url, self.delay, self.waits = drv, url, delay, 0

    def wait_for_timeout(self, ms):
        self.waits += 1
        if self.waits == self.delay:
            self.drv._bodies.append(("batchexecute", self.url))


def test_attach_waits_for_late_uploads(monkeypatch):
    drv = ad.FlowAgentDriver.__new__(ad.FlowAgentDriver)
    drv._bodies = []
    up = "https://flow-content.google/image/11111111-2222-3333-4444-555555555555?Expires=1&Signature=s"
    drv.page = _UploadPage(drv, up, delay=4)
    monkeypatch.setattr(drv, "_clear_overlays", lambda *a, **k: None)
    drv._await_uploads(0, 1, per_file_wait_ms=1, settle_timeout_ms=60_000)
    known = set(drv._media_since(0))
    assert ("image", "11111111-2222-3333-4444-555555555555") in known  # the upload is never a "result"


def test_soft_error_does_not_mask_a_hard_one():
    assert ad.refusal_code("x PUBLIC_ERROR_SOMETHING_WENT_WRONG y") == "PUBLIC_ERROR_SOMETHING_WENT_WRONG"
    assert ad.refusal_code("PUBLIC_ERROR_SOMETHING_WENT_WRONG ... PUBLIC_ERROR_USER_QUOTA_REACHED") == "PUBLIC_ERROR_USER_QUOTA_REACHED"
    assert ad.refusal_code("all good") is None


class _RetriedDriver(_FakeDriver):
    def ask(self, brief, *, expect, count, timeout, attachments, confirm_up_to=None):
        media = [ad.Media(kind="image", media_id="m1", url="u")]
        return ad.AgentResult(project_url="p", media=media, credits_before=50, credits_after=50,
                              refused="PUBLIC_ERROR_SOMETHING_WENT_WRONG")


def test_generate_keeps_media_after_agent_retried_a_soft_error(tmp_path):
    gen = ab.generate("image", "b", [], count=1, output_path=str(tmp_path / "a.png"), flow_project=None,
                      timeout=10, driver_cls=_RetriedDriver)
    assert gen.media_ids == ["m1"]


def test_image_brief_names_uploaded_refs():
    brief, refs = ab.image_brief({"prompt": "hall", "image_paths": ["/x/lena.png", "/y/loc.png"]}, "nano-pro", "9:16", 1)
    assert refs == ["/x/lena.png", "/y/loc.png"]
    assert "2 images I just uploaded to this project (lena.png, loc.png)" in brief
    assert "attached" not in brief
