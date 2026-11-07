"""Resuming an approved news render must not resend an acknowledged copy."""
import json
from types import SimpleNamespace

import pytest

from addons.news import produce
from factory import config, tg


def setup_delivery(tmp_path, monkeypatch):
    (tmp_path / "renders").mkdir()
    (tmp_path / "renders/final.mp4").write_bytes(b"approved render")
    monkeypatch.setattr(config, "load", lambda: SimpleNamespace(tg_token="test", tg_chat="owner", tg_proxy=""))
    monkeypatch.setenv("NEWS_TG_CHATS", "group,group")
    calls = []

    class Telegram:
        def __init__(self, token, chat, proxy):
            self.chat = chat

        def send_document(self, render, caption, name):
            calls.append(self.chat)
            return {"message_id": len(calls), "date": 123, "document": {"file_id": "file"}}

    monkeypatch.setattr(tg, "Telegram", Telegram)
    return calls


def test_resume_skips_acknowledged_chats_and_does_not_run_build(tmp_path, monkeypatch):
    calls = setup_delivery(tmp_path, monkeypatch)
    monkeypatch.setattr(produce.subprocess, "run", lambda *a, **k: pytest.fail("must not build"))
    produce.deliver_existing(tmp_path, {}, None, True, False)
    produce.deliver_existing(tmp_path, {}, None, True, False)
    assert calls == ["owner", "group"]
    assert (tmp_path / "renders/final.mp4").read_bytes() == b"approved render"
    receipt = json.loads((tmp_path / "delivery.json").read_text())
    assert all(x["status"] == "sent" for x in receipt["telegram"].values())


def test_resume_does_not_retry_an_ambiguous_telegram_request(tmp_path, monkeypatch):
    calls = setup_delivery(tmp_path, monkeypatch)
    (tmp_path / "delivery.json").write_text(json.dumps({"telegram": {"owner": {"status": "sending"}}}))
    with pytest.raises(RuntimeError, match="interrupted"):
        produce.deliver_existing(tmp_path, {}, None, True, False)
    assert calls == []


def test_resume_rejects_changed_render(tmp_path, monkeypatch):
    calls = setup_delivery(tmp_path, monkeypatch)
    produce.deliver_existing(tmp_path, {}, None, True, False)
    (tmp_path / "renders/final.mp4").write_bytes(b"different edit")
    with pytest.raises(RuntimeError, match="differs"):
        produce.deliver_existing(tmp_path, {}, None, True, False)
    assert calls == ["owner", "group"]


def test_resume_verifies_existing_kinescope_before_removing_video(tmp_path, monkeypatch):
    from addons.news import kinescope_upload

    calls = setup_delivery(tmp_path, monkeypatch)
    (tmp_path / "kinescope.json").write_text(json.dumps({"id": "existing", "play_link": "play"}))
    monkeypatch.setenv("KINESCOPE_API_TOKEN", "test")
    monkeypatch.setattr(produce.subprocess, "run", lambda *a, **k: pytest.fail("must not upload twice"))
    monkeypatch.setattr(kinescope_upload, "download_link", lambda *a: ("processing", ""))
    with pytest.raises(RuntimeError, match="not ready"):
        produce.deliver_existing(tmp_path, {}, None, True, True)
    assert (tmp_path / "renders/final.mp4").is_file()
    monkeypatch.setattr(kinescope_upload, "download_link", lambda *a: ("done", "download"))
    produce.deliver_existing(tmp_path, {}, None, True, True)
    assert not (tmp_path / "renders/final.mp4").exists()
    produce.deliver_existing(tmp_path, {}, None, True, True)
    assert calls == ["owner", "group"]
