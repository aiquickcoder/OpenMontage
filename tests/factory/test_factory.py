"""Factory: queue states, brief parsing, claude command, release staging/approval, worker + bot flow."""

import json
import subprocess
from pathlib import Path

import pytest

from factory import release, runner, service
from factory.config import Settings
from factory.db import BriefStore, InvalidTransition, make_id


def _settings(tmp_path: Path, **kw) -> Settings:
    base = dict(repo=tmp_path, data_dir=tmp_path / "data", releases_dir=tmp_path / "data" / "releases",
                claude_bin="claude", claude_proxy="http://127.0.0.1:8118", claude_max_turns=50,
                run_timeout_s=60, tg_token="", tg_chat="42", tg_proxy="", release_base_url="https://x/releases")
    base.update(kw)
    return Settings(**base)


class FakeTg:
    chat = "42"
    enabled = True

    def __init__(self):
        self.sent, self.videos, self.calls = [], [], []

    def send(self, text, buttons=None):
        self.sent.append(text)

    def send_video(self, path, caption, buttons=None):
        self.videos.append((Path(path), caption, buttons))

    def call(self, method, **params):
        self.calls.append((method, params))


def _ffprobe_ok(width=1080, height=1920, duration=42.0):
    def run(cmd, **kw):
        out = json.dumps({"streams": [{"width": width, "height": height}], "format": {"duration": str(duration)}})
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    return run


def _project(tmp_path: Path, name="p1") -> Path:
    p = tmp_path / "projects" / name
    (p / "renders").mkdir(parents=True)
    (p / "renders" / "final.mp4").write_bytes(b"video-bytes")
    (p / "checkpoint_publish.json").write_text(json.dumps({"artifacts": {"publish_log": {"entries": [
        {"platform": "instagram", "export_path": "renders/final.mp4",
         "metadata_used": {"description": "Лис и МУЛЬТ", "hashtags": ["мульт", "лис"]}}]}}}), encoding="utf-8")
    return p


# -- db --------------------------------------------------------------------
def test_queue_lifecycle(tmp_path):
    store = BriefStore(tmp_path / "f.db")
    a = store.add("первый бриф про лиса", {"comment_bait": "meta_joke"}, brief_id="a")
    store.add("второй бриф", brief_id="b")
    claimed = store.claim_next()
    assert claimed["id"] == a and claimed["state"] == "running"
    assert claimed["options"] == {"comment_bait": "meta_joke"}
    store.move(a, "awaiting_approval", project_dir="/p")
    with pytest.raises(InvalidTransition):
        store.move(a, "running")
    store.move(a, "approved")
    assert store.get(a)["project_dir"] == "/p"


def test_recover_running_marks_failed(tmp_path):
    store = BriefStore(tmp_path / "f.db")
    store.add("бриф для восстановления", brief_id="x")
    store.claim_next()
    assert store.recover_running() == ["x"]
    assert store.get("x")["state"] == "failed"


def test_make_id_translit():
    from datetime import datetime, timezone

    assert make_id("Лис и сыр!", datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)) == "20261010-120000-lis-i-syr"


# -- brief parsing -----------------------------------------------------------
def test_parse_brief_options():
    text, opts = service.parse_brief("bait: hero_breaks_wall\nproduct: сок «Яблочко»\nЛисёнок ищет сок в лесу")
    assert text == "Лисёнок ищет сок в лесу"
    assert opts == {"comment_bait": "hero_breaks_wall", "product": "сок «Яблочко»"}


def test_parse_brief_rejects_bad_bait():
    with pytest.raises(ValueError):
        service.parse_brief("bait: loud\nЛисёнок ищет сок в лесу")


# -- runner ----------------------------------------------------------------
def test_command_and_env(tmp_path):
    s = _settings(tmp_path, agent="claude")
    cmd = runner.build_command(s, "PROMPT")
    assert cmd[:3] == ["claude", "-p", "PROMPT"] and "--allowedTools" in cmd and "50" in cmd
    env = runner.build_env(s, {"PATH": "/bin"})
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:8118"
    assert ".google.com" in env["NO_PROXY"] and env["FLOW_DRIVER"] == "agent" and env["FLOW_BRIDGE"] == "1"


def test_codex_runs_through_the_gateway_in_its_sandbox(tmp_path):
    """Owner 2026-11-01: Claude → Codex GPT (gpt-6.1-sol, effort high), through the VPN via the local codex-gateway."""
    from factory import agent_cli
    s = _settings(tmp_path)
    assert s.agent == "codex"
    cmd = runner.build_command(s, "PROMPT", tmp_path / "last.txt")
    assert cmd[:2] == ["codex", "exec"] and cmd[-1] == "PROMPT"
    assert "workspace-write" in cmd and "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert "gpt-6.1-sol" in cmd and 'model_reasoning_effort="high"' in cmd and str(tmp_path / "last.txt") in cmd
    assert any("172.22.0.1:8282" in c and 'env_key="CODEX_GW_SECRET"' in c for c in cmd)
    env = runner.build_env(s, {"PATH": "/bin"})
    assert "172.22.0.1" in env["NO_PROXY"]  # the gateway is local even when the tools get the VPN proxy
    # Codex echoes the prompt (which spells AUTOPILOT_RESULT out) into stdout: only its last message counts
    (tmp_path / "last.txt").write_text("AUTOPILOT_RESULT: READY projects/b1 done", encoding="utf-8")
    assert agent_cli.result_text("user\nEnd with AUTOPILOT_RESULT: READY projects/<id>", tmp_path / "last.txt").startswith("AUTOPILOT_RESULT: READY projects/b1")
    assert agent_cli.result_text("anything", tmp_path / "missing.txt") == ""
    web = agent_cli.codex_command("P", tmp_path, tmp_path / "l.txt", {}, search=True)
    assert web[:3] == ["codex", "--search", "exec"]  # «codex exec --search» is refused by the CLI


def test_prompt_mentions_addons():
    p = runner.build_prompt({"id": "b1", "text": "Бриф", "options": {"comment_bait": "meta_joke", "product": "сок"}})
    assert "addons/autopilot/README.md" in p and "projects/b1" in p and "meta_joke" in p and "сок" in p


def test_prompt_resumes_existing_project(tmp_path):
    proj = tmp_path / "b1"
    (proj / "b1").mkdir(parents=True)
    (proj / "checkpoint_idea.json").write_text("{}")
    (proj / "b1" / "checkpoint_scene_plan.json").write_text("{}")
    (proj / "b1" / "checkpoint_idea_20261011T0724255016960000.json").write_text("{}")
    done = runner.completed_stages(proj)
    assert done == ["idea", "scene_plan"]
    p = runner.build_prompt({"id": "b1", "text": "Бриф", "detail": "Veo timeout"}, done)
    assert "RESUME" in p and "idea, scene_plan" in p and "Veo timeout" in p
    assert "RESUME" not in runner.build_prompt({"id": "b1", "text": "Бриф"}, [])


def test_parse_result():
    out = "работаю...\nAUTOPILOT_RESULT: READY projects/b1\n"
    assert runner.parse_result(out, Path("d")) == ("READY", Path("projects/b1"), "")
    st, _, detail = runner.parse_result("AUTOPILOT_RESULT: FAILED projects/b1 Flow unavailable", Path("d"))
    assert st == "FAILED" and detail == "Flow unavailable"
    assert runner.parse_result("nothing", Path("d"))[0] == "FAILED"


def test_run_brief_writes_log(tmp_path):
    s = _settings(tmp_path, agent="claude")

    def fake(cmd, **kw):
        assert kw["cwd"] == tmp_path
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\nAUTOPILOT_RESULT: READY projects/b1", stderr="")

    out = runner.run_brief(s, {"id": "b1", "text": "t", "options": {}}, runner=fake)
    assert out.status == "READY" and out.project_dir == tmp_path / "projects" / "b1"
    assert "AUTOPILOT_RESULT" in (tmp_path / "projects" / "b1" / "factory_run.log").read_text()


def test_run_brief_with_codex_reads_its_last_message(tmp_path):
    s = _settings(tmp_path)

    def fake(cmd, **kw):  # Codex prints the prompt (with the result line spelled out) and writes its answer to -o
        Path(cmd[cmd.index("-o") + 1]).write_text("AUTOPILOT_RESULT: READY projects/b1 done", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="user\nEnd with AUTOPILOT_RESULT: FAILED <reason>", stderr="")

    out = runner.run_brief(s, {"id": "b1", "text": "t", "options": {}}, runner=fake)
    assert out.status == "READY" and out.project_dir == tmp_path / "projects" / "b1"


# -- release ---------------------------------------------------------------
def test_stage_and_approve(tmp_path):
    proj = _project(tmp_path)
    rel = tmp_path / "rel"
    staged = release.stage(proj, rel, "b1", runner=_ffprobe_ok())
    assert staged["caption"] == "Лис и МУЛЬТ" and staged["duration"] == 42.0
    pub = json.loads((rel / "pending" / "b1" / "checkpoint_publish.json").read_text())
    assert pub["artifacts"]["publish_log"]["entries"][0]["export_path"] == "final.mp4"
    rec = release.approve(rel, "b1")
    assert set(rec["files"]) == {"final.mp4", "checkpoint_publish.json"}
    assert json.loads((rel / "index.json").read_text())["releases"][0]["id"] == "b1"
    with pytest.raises(release.ReleaseError):
        release.approve(rel, "b1")


def test_stage_finds_nested_pipeline_dir(tmp_path):
    # agent wrote checkpoints to projects/<id>/<id>/ while renders stayed at projects/<id>/
    outer = tmp_path / "projects" / "b1"
    inner = _project(outer, name="b1").parent.parent / "projects" / "b1"
    nested = outer / "b1"
    nested.mkdir(parents=True)
    (inner / "checkpoint_publish.json").rename(nested / "checkpoint_publish.json")
    (outer / "renders").mkdir()
    (inner / "renders" / "final.mp4").rename(outer / "renders" / "final.mp4")
    staged = release.stage(outer, tmp_path / "rel", "b1", runner=_ffprobe_ok())
    assert staged["caption"] == "Лис и МУЛЬТ"


def test_stage_rejects_horizontal(tmp_path):
    with pytest.raises(release.ReleaseError, match="vertical"):
        release.stage(_project(tmp_path), tmp_path / "rel", "b1", runner=_ffprobe_ok(1920, 1080))


def test_stage_rejects_missing_publish(tmp_path):
    proj = _project(tmp_path)
    (proj / "checkpoint_publish.json").unlink()
    with pytest.raises(release.ReleaseError, match="missing"):
        release.stage(proj, tmp_path / "rel", "b1", runner=_ffprobe_ok())


def test_release_folder_is_readable_by_phone_farm(tmp_path):
    farm = Path.home() / "Desktop" / "movies" / "phone-farm"
    if not (farm / "farm" / "openmontage.py").exists():
        pytest.skip("phone-farm repo not present")
    import importlib.util

    spec = importlib.util.spec_from_file_location("farm_openmontage", farm / "farm" / "openmontage.py")
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["farm_openmontage"] = mod  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    rel = tmp_path / "rel"
    release.stage(_project(tmp_path), rel, "b1", runner=_ffprobe_ok())
    release.approve(rel, "b1")
    got = mod.releases(rel / "b1")
    assert got["instagram"].video == rel / "b1" / "final.mp4"
    assert got["instagram"].caption.startswith("Лис и МУЛЬТ")


# -- worker + bot ----------------------------------------------------------
def test_worker_ready_flow(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    store = BriefStore(s.db_path)
    store.add("Лисёнок ищет сок в лесу", brief_id="b1")
    proj = _project(tmp_path, "b1")
    monkeypatch.setattr(release, "check_video", lambda v, r=None: {"width": 1080, "height": 1920, "duration": 40.0})
    tg = FakeTg()
    done = service.process_one(s, store, tg, run=lambda st, b: runner.RunOutcome("READY", proj, "", 0))
    assert done == "b1" and store.get("b1")["state"] == "awaiting_approval"
    assert tg.videos and tg.videos[0][2][0][0]["callback_data"] == "approve:b1"

    service.handle_update(s, store, tg, {"callback_query": {"id": "q", "data": "approve:b1",
                                                            "message": {"chat": {"id": 42}}}})
    assert store.get("b1")["state"] == "approved"
    assert (s.releases_dir / "b1" / "final.mp4").exists()


def test_worker_budget_and_failure(tmp_path):
    s = _settings(tmp_path)
    store = BriefStore(s.db_path)
    store.add("бриф номер один", brief_id="b1")
    store.add("бриф номер два", brief_id="b2")
    tg = FakeTg()
    service.process_one(s, store, tg, run=lambda st, b: runner.RunOutcome("BUDGET", tmp_path, "no credits", 0))
    service.process_one(s, store, tg, run=lambda st, b: runner.RunOutcome("FAILED", tmp_path, "boom", 1))
    assert store.get("b1")["state"] == "budget" and store.get("b2")["state"] == "failed"


def test_bot_ignores_strangers_and_queues_brief(tmp_path):
    s = _settings(tmp_path)
    store = BriefStore(s.db_path)
    tg = FakeTg()
    service.handle_update(s, store, tg, {"message": {"chat": {"id": 7}, "text": "/brief чужой бриф про лиса"}})
    assert store.list() == []
    service.handle_update(s, store, tg, {"message": {"chat": {"id": 42},
                                                     "text": "/brief bait: meta_joke\nЛисёнок ищет сок"}})
    rows = store.list()
    assert len(rows) == 1 and rows[0]["options"] == {"comment_bait": "meta_joke"}


def test_bot_campaign_brief_checked_like_studio(tmp_path):
    from studio import ads

    s = _settings(tmp_path)
    store = BriefStore(s.db_path)
    tg = FakeTg()
    adstore = ads.store_for(runner.studio_settings(s))
    live = adstore.save_campaign(None, {"name": "Осень", "advertiser_id": "adv-1", "insert_id": "ins-1",
                                        "insert_version": 2, "product": "сок", "status": "active"})
    done = adstore.save_campaign(None, {"name": "Лето", "advertiser_id": "adv-1", "insert_id": "ins-1",
                                        "insert_version": 1, "status": "done"})

    def brief(cmp):
        service.handle_update(s, store, tg, {"message": {"chat": {"id": 42},
                                                         "text": f"/brief campaign: {cmp}\nЛисёнок ищет сок"}})

    brief("cmp-nope")
    brief(done)
    assert store.list() == [] and "unknown campaign" in tg.sent[-2] and "кампания завершена" in tg.sent[-1]
    brief(live)
    rows = store.list()
    assert len(rows) == 1 and rows[0]["options"] == {"campaign": live, "advertiser": "adv-1", "insert": "ins-1",
                                                     "insert_version": 2, "product": "сок"}


def test_music_option_reaches_prompt():
    text, opts = service.parse_brief("music: none\nКот печёт пиццу")
    assert opts == {"music": "none"}
    prompt = runner.build_prompt({"id": "b1", "text": text, "options": opts})
    assert "- music: none" in prompt


def test_send_video_passes_dimensions(tmp_path, monkeypatch):
    from factory import tg

    clip = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=108x192:d=2",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    assert tg.video_meta(clip) == {"width": 108, "height": 192, "duration": 2}
    sent = {}
    bot = tg.Telegram("t", "1")
    monkeypatch.setattr(bot, "call", lambda method, **kw: sent.update(kw))
    bot.send_video(clip, "c")
    assert sent["width"] == 108 and sent["height"] == 192 and sent["duration"] == 2
    assert tg.video_meta(tmp_path / "missing.mp4") == {}


def test_mural_format_routes_to_mural_addon():
    text, opts = service.parse_brief("format: mural\nЭпизод «Бали»")
    assert opts == {"format": "mural"}
    p = runner.build_prompt({"id": "b2", "text": text, "options": opts})
    assert "addons/mural/README.md" in p and "projects/b2" in p
    assert "cartoon-ad" not in p and "addons/autopilot" not in p


def test_a_film_over_the_bot_limit_goes_as_a_squeezed_file(tmp_path, monkeypatch):
    from factory import tg as tgmod
    big, small = tmp_path / "final.mp4", tmp_path / "final.tg.mp4"
    big.write_bytes(b"x" * 2000)
    small.write_bytes(b"x" * 500)
    monkeypatch.setattr(tgmod, "VIDEO_LIMIT_BYTES", 1000)
    monkeypatch.setattr(tgmod, "squeeze", lambda p: small)
    calls = []
    t = tgmod.Telegram("tok", "42")
    monkeypatch.setattr(t, "call", lambda method, **kw: calls.append((method, kw)))
    t.send_document(big, "cap", "news.mp4")
    (method, kw), = calls
    assert method == "sendDocument" and kw["files"]["document"][0] == "news.mp4"
    assert kw["caption"].startswith("cap") and "сжат" in kw["caption"]
    assert 2400 < tgmod.fit_kbps(145.8) < 2600 and tgmod.fit_kbps(38) > 9000  # 2.5 min film / 38 s film


def test_squeeze_keeps_the_picture_size(tmp_path):
    from factory import tg as tgmod
    src = tmp_path / "f.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=270x480:rate=25:duration=2",
                    "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)],
                   check=True)
    out = tgmod.squeeze(src)
    assert out.name == "f.tg.mp4" and tgmod.video_meta(out)["height"] == 480
    assert tgmod.squeeze(src) == out  # the second chat reuses the copy


def test_bot_token_never_reaches_the_log(monkeypatch):
    """requests puts /bot<token>/getUpdates into its error text; Telegram.call must hand it on masked."""
    import requests

    from factory.tg import Telegram, mask

    token = "123456789:AAH-secret_Token"
    tg = Telegram(token, "42")

    def fail(url, **kw):
        raise requests.exceptions.SSLError(f"Max retries exceeded with url: /bot{token}/getUpdates (SSL)")

    monkeypatch.setattr(tg.s, "post", fail)
    with pytest.raises(RuntimeError) as err:
        tg.call("getUpdates")
    text = str(err.value)
    assert token not in text and "AAH-secret" not in text and "bot***" in text
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert mask(f"url /bot{token}/x") == "url /bot***/x"
