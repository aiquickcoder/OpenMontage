"""Studio advertising: advertisers, versioned inserts, campaigns, briefs, client report, factory hooks."""

import io
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from factory.db import BriefStore
from studio import ads
from studio.app import create_app
from tests.studio.test_studio_api import H, _login, _settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4 + b"\r\n--x\r\n"  # binary incl. CRLF/boundary-like bytes


@pytest.fixture
def env(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    app.include_router(ads.router)
    client = _login(TestClient(app))
    return settings, client


def _advertiser(client, **kw):
    body = {"brand": "Ромашка", "legal_name": "ООО «Ромашка»", "inn": "7700000000", "cta": "Закажи сейчас",
            "link": "https://romashka.ru", "promo_code": "MULT10", "must_say": ["доставка 30 минут"],
            "must_not_say": ["лучший в мире"], **kw}
    r = client.post("/api/advertisers", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _insert(client, template="Герой пользуется {product} от {brand}. CTA: {cta}. Нельзя: {must_not}"):
    r = client.post("/api/inserts", json={"name": "Нативно", "placement": "hero_uses_product",
                                          "max_screen_share": 0.2, "template": template}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _campaign(client, adv, ins, **kw):
    body = {"name": "Осень", "advertiser_id": adv["id"], "insert_id": ins["id"], "erid": "2VtzqX",
            "product": "пицца «Маргарита»", "period_start": "2026-10-01", "period_end": "2026-10-31",
            "planned_videos": 5, "accounts": ["ig_kot"], **kw}
    r = client.post("/api/campaigns", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def test_requires_login(tmp_path):
    app = create_app(_settings(tmp_path))
    app.include_router(ads.router)
    assert TestClient(app).get("/api/advertisers").status_code == 401


def test_advertiser_crud_and_archive(env):
    _, client = env
    adv = _advertiser(client)
    assert adv["must_say"] == ["доставка 30 минут"] and adv["logo"] is None and not adv["archived"]
    assert client.post("/api/advertisers", json={"brand": "X", "inn": "123"}, headers=H).status_code == 422
    r = client.patch(f"/api/advertisers/{adv['id']}", json={"notes": "звонить утром", "must_say": [" a ", ""]}, headers=H)
    assert r.json()["notes"] == "звонить утром" and r.json()["must_say"] == ["a"] and r.json()["brand"] == "Ромашка"
    assert client.delete(f"/api/advertisers/{adv['id']}", headers=H).status_code == 200
    assert client.get("/api/advertisers").json() == []
    assert client.get("/api/advertisers?archived=true").json()[0]["archived"] is True
    assert client.get("/api/advertisers/nope").status_code == 404


def test_upload_validation_and_files(env):
    settings, client = env
    adv = _advertiser(client)
    url = f"/api/advertisers/{adv['id']}/files"
    r = client.post(url, files={"file": ("logo.png", PNG, "image/png")}, data={"kind": "logo"}, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["advertiser"]["logo"] == "logo.png"
    r = client.post(url + "?kind=product", files={"file": ("Фото 1.jpg", JPG, "image/jpeg")}, headers=H)
    assert r.status_code == 201
    rel = r.json()["path"]
    assert rel.startswith("products/") and rel.endswith(".jpg")
    saved = settings.factory.data_dir / "advertisers" / adv["id"] / rel
    assert saved.read_bytes() == JPG  # binary round-trip through the stdlib multipart parser
    # a new logo replaces the old one, any extension
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16
    client.post(url, files={"file": ("l.webp", webp, "image/webp")}, data={"kind": "logo"}, headers=H)
    assert client.get(f"/api/advertisers/{adv['id']}").json()["logo"] == "logo.webp"
    # rejects
    assert client.post(url, files={"file": ("x.gif", b"GIF89a....", "image/gif")}, data={"kind": "logo"},
                       headers=H).status_code == 415
    assert client.post(url, files={"file": ("x.png", PNG, "image/png")}, data={"kind": "banner"},
                       headers=H).status_code == 422
    assert client.post(url, json={"kind": "logo"}, headers=H).status_code == 415
    big = PNG + b"\x00" * (ads.MAX_UPLOAD + 1)
    assert client.post(url, files={"file": ("b.png", big, "image/png")}, data={"kind": "logo"},
                       headers=H).status_code == 413
    # serve + escape guard + delete
    assert client.get(f"/api/advertisers/{adv['id']}/file/{rel}").content == JPG
    assert client.get(f"/api/advertisers/{adv['id']}/file/../../studio.db").status_code in (403, 404)
    r = client.delete(f"/api/advertisers/{adv['id']}/file/{rel}", headers=H)
    assert r.json()["products"] == []


def test_placeholder_validation(env):
    _, client = env
    for bad in ("Купи {brnd}", "{brand!r}", "{brand:>10}", "{0}", "{brand.upper}", "открытая { скобка"):
        r = client.post("/api/inserts", json={"name": "x", "placement": "plot", "max_screen_share": 0.1,
                                              "template": bad}, headers=H)
        assert r.status_code == 422, bad
    ins = _insert(client, "Литерал {{не подстановка}} и {brand}")
    assert ins["placeholders"] == ["brand"]
    assert client.patch(f"/api/inserts/{ins['id']}", json={"template": "{oops}"}, headers=H).status_code == 422
    assert client.post("/api/inserts", json={"name": "x", "placement": "plot", "max_screen_share": 0.6,
                                             "template": "{brand}"}, headers=H).status_code == 422
    assert client.post("/api/inserts", json={"name": "x", "placement": "billboard", "max_screen_share": 0.1,
                                             "template": "{brand}"}, headers=H).status_code == 422


def test_versioning_pinned_by_campaign(env):
    _, client = env
    adv, ins = _advertiser(client), _insert(client, "v1 {brand}")
    camp = _campaign(client, adv, ins)
    assert camp["insert_version"] == 1
    r = client.patch(f"/api/inserts/{ins['id']}", json={"template": "v2 {brand} {promo_code}"}, headers=H)
    assert r.json()["version"] == 2
    # archiving alone does not create a version; an identical save neither
    client.patch(f"/api/inserts/{ins['id']}", json={"template": "v2 {brand} {promo_code}"}, headers=H)
    client.delete(f"/api/inserts/{ins['id']}", headers=H)
    versions = client.get(f"/api/inserts/{ins['id']}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert client.get(f"/api/inserts/{ins['id']}?version=1").json()["template"] == "v1 {brand}"
    assert client.get(f"/api/campaigns/{camp['id']}").json()["insert_version"] == 1
    r = client.post(f"/api/inserts/{ins['id']}/preview", json={"advertiser_id": adv["id"], "version": 1}, headers=H)
    assert r.json()["text"] == "v1 Ромашка"
    # repin explicitly; a new campaign pins the current version
    r = client.patch(f"/api/campaigns/{camp['id']}", json={"insert_version": 2}, headers=H)
    assert r.json()["insert_version"] == 2
    assert client.patch(f"/api/campaigns/{camp['id']}", json={"insert_version": 9}, headers=H).status_code == 422
    assert _campaign(client, adv, ins)["insert_version"] == 2


def test_preview_render(env):
    _, client = env
    adv, ins = _advertiser(client), _insert(client)
    camp = _campaign(client, adv, ins)
    r = client.post(f"/api/inserts/{ins['id']}/preview", json={"advertiser_id": adv["id"]}, headers=H)
    assert r.json()["text"] == "Герой пользуется Ромашка от Ромашка. CTA: Закажи сейчас. Нельзя: лучший в мире"
    r = client.post(f"/api/inserts/{ins['id']}/preview", json={"advertiser_id": adv["id"], "campaign_id": camp["id"]},
                    headers=H)
    assert "пицца «Маргарита»" in r.json()["text"]
    assert r.json()["marking"] == "Реклама. ООО «Ромашка», ИНН 7700000000. erid: 2VtzqX"
    r = client.post(f"/api/inserts/{ins['id']}/preview",
                    json={"advertiser_id": adv["id"], "template": "{link} {promo_code} {must} {legal_name}"}, headers=H)
    assert r.json()["text"] == "https://romashka.ru MULT10 доставка 30 минут ООО «Ромашка»"
    assert client.post(f"/api/inserts/{ins['id']}/preview", json={"advertiser_id": adv["id"], "template": "{x}"},
                       headers=H).status_code == 422


def test_campaign_validation(env):
    _, client = env
    adv, ins = _advertiser(client), _insert(client)
    bad = {"name": "x", "advertiser_id": "adv_nope", "insert_id": ins["id"]}
    assert client.post("/api/campaigns", json=bad, headers=H).status_code == 422
    bad = {"name": "x", "advertiser_id": adv["id"], "insert_id": ins["id"],
           "period_start": "2026-10-10", "period_end": "2026-10-01"}
    assert client.post("/api/campaigns", json=bad, headers=H).status_code == 422
    camp = _campaign(client, adv, ins, erid="", erid_not_required=True, status="active")
    rows = client.get("/api/campaigns").json()
    assert rows[0]["advertiser_brand"] == "Ромашка" and rows[0]["erid_not_required"] is True
    assert client.delete(f"/api/campaigns/{camp['id']}", headers=H).status_code == 409
    client.patch(f"/api/campaigns/{camp['id']}", json={"status": "draft", "period_end": None}, headers=H)
    assert client.get(f"/api/campaigns/{camp['id']}").json()["period_end"] is None
    assert client.delete(f"/api/campaigns/{camp['id']}", headers=H).status_code == 200


def test_campaign_briefs_created_with_options(env):
    settings, client = env
    adv, ins = _advertiser(client), _insert(client)
    camp = _campaign(client, adv, ins, erid="")
    r = client.post(f"/api/campaigns/{camp['id']}/briefs", json={"text": "Кот-повар печёт пиццу", "count": 3},
                    headers=H)
    assert r.status_code == 201, r.text
    ids = r.json()["ids"]
    assert len(set(ids)) == 3
    assert any("ERID" in w for w in r.json()["warnings"]) and any("логотип" in w for w in r.json()["warnings"])
    brief = BriefStore(settings.factory.db_path).get(ids[0])
    assert brief["options"] == {"campaign": camp["id"], "advertiser": adv["id"], "insert": ins["id"],
                                "insert_version": 1, "product": "пицца «Маргарита»"}
    assert client.post(f"/api/campaigns/{camp['id']}/briefs", json={"text": "short"}, headers=H).status_code == 422
    client.patch(f"/api/campaigns/{camp['id']}", json={"status": "done"}, headers=H)
    assert client.post(f"/api/campaigns/{camp['id']}/briefs", json={"text": "Кот-повар печёт пиццу"},
                       headers=H).status_code == 409


def _farm_jobs(settings, rows):
    conn = sqlite3.connect(settings.db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS farm_jobs (id INTEGER PRIMARY KEY, kind, account_id, device, "
                 "release_id, status, payload TEXT, result TEXT, run_after REAL, created_at REAL, "
                 "started_at REAL, finished_at REAL, updated_at REAL)")
    for r in rows:
        conn.execute("INSERT INTO farm_jobs (kind, account_id, device, release_id, status, payload, result, "
                     "run_after, created_at, finished_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (r["kind"], r["account"], "pixel", r["release"], r["status"], "{}",
                      json.dumps(r.get("result")), 0, 0, r.get("finished"), time.time()))
    conn.commit()
    conn.close()


def test_report_json_and_csv(env):
    settings, client = env
    adv, ins = _advertiser(client), _insert(client)
    camp = _campaign(client, adv, ins)
    ids = client.post(f"/api/campaigns/{camp['id']}/briefs", json={"text": "Кот-повар печёт пиццу", "count": 2},
                      headers=H).json()["ids"]
    BriefStore(settings.factory.db_path).add("чужой бриф без кампании")
    # without farm_jobs the report still works
    rep = client.get(f"/api/campaigns/{camp['id']}/report").json()
    assert [r["brief_id"] for r in rep["rows"]] == ids and rep["rows"][0]["publications"] == []
    # approved release + caption + farm publications
    rel = settings.factory.releases_dir
    (rel / ids[0]).mkdir(parents=True)
    (rel / "index.json").write_text(json.dumps({"releases": [{"id": ids[0], "approved_at": "2026-10-05T10:00:00+00:00"}]}))
    (rel / ids[0] / "checkpoint_publish.json").write_text(json.dumps(
        {"artifacts": {"publish_log": {"entries": [{"metadata_used": {"description": "Кот; пицца"}}]}}}))
    _farm_jobs(settings, [
        {"kind": "publish", "account": "ig_kot", "release": ids[0], "status": "done", "finished": 1760000000.0,
         "result": {"url": "https://instagram.com/p/1"}},
        {"kind": "publish", "account": "tt_kot", "release": ids[0], "status": "failed", "result": None},
        {"kind": "publish", "account": "yt_kot", "release": ids[0], "status": "failed",
         "result": {"url": "javascript:fetch('/api/x')"}},
        {"kind": "warmup", "account": "ig_kot", "release": ids[0], "status": "done"},
    ])
    rep = client.get(f"/api/campaigns/{camp['id']}/report").json()
    assert rep["advertiser"]["inn"] == "7700000000" and rep["campaign"]["erid"] == "2VtzqX"
    row = rep["rows"][0]
    assert row["approved_at"].startswith("2026-10-05") and row["caption"] == "Кот; пицца"
    assert [(p["account"], p["status"], p["post_url"]) for p in row["publications"]] == [
        ("ig_kot", "done", "https://instagram.com/p/1"), ("tt_kot", "failed", None), ("yt_kot", "failed", None)]
    assert rep["totals"] == {"briefs": 2, "approved": 1, "publications": 1}

    r = client.get(f"/api/campaigns/{camp['id']}/report.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    raw = r.content.decode("utf-8")
    assert raw.startswith("﻿Кампания;Осень")
    assert "ИНН;7700000000" in raw and "ERID;2VtzqX" in raw
    assert '"Кот; пицца";ig_kot;pixel;done;2025-10-09T08:53:20+00:00;https://instagram.com/p/1' in raw
    assert f"{ids[1]};queued;" in raw  # brief without publications still gets a line


def test_report_csv_neutralizes_formulas():
    rep = {"campaign": {"name": "@SUM(1)", "erid": "", "erid_not_required": False, "period_start": None,
                        "period_end": None, "planned_videos": 1},
           "advertiser": {"legal_name": "=1+2", "brand": "", "inn": "+7"},
           "totals": {"approved": 0, "publications": 1},
           "rows": [{"brief_id": "b1", "state": "approved", "created_at": "-1", "approved_at": None,
                     "caption": "=HYPERLINK(\"x\")",
                     "publications": [{"account": "\tacc", "device": "\rdev", "status": "done",
                                       "finished_at": None, "post_url": "@x"}]}]}
    import csv as _csv

    rows = list(_csv.reader(io.StringIO(ads.report_csv(rep).lstrip("\ufeff"), newline=""), delimiter=";"))
    assert rows[0] == ["Кампания", "'@SUM(1)"] and rows[1] == ["Рекламодатель", "'=1+2"] and rows[2] == ["ИНН", "'+7"]
    assert rows[-1] == ["b1", "approved", "'-1", "", "'=HYPERLINK(\"x\")", "'\tacc", "'\rdev", "done", "", "'@x"]


def test_marking_for(env):
    settings, client = env
    adv, ins = _advertiser(client), _insert(client)
    camp = _campaign(client, adv, ins)
    assert ads.marking_for(settings, {}) is None
    assert ads.marking_for(settings, {"comment_bait": "none"}) is None
    assert ads.marking_for(settings, {"campaign": camp["id"]}) == "Реклама. ООО «Ромашка», ИНН 7700000000. erid: 2VtzqX"
    assert ads.marking_for(settings, {"campaign": "cmp_missing"}) is None
    bare = _advertiser(client, brand="Бренд", legal_name="", inn="")
    assert ads.marking_for(settings, {"advertiser": bare["id"], "insert": ins["id"]}) == "Реклама. Бренд"
    assert ads.marking_line({}, "") == "Реклама"


def test_prompt_lines(env):
    settings, client = env
    adv, ins = _advertiser(client), _insert(client)
    client.post(f"/api/advertisers/{adv['id']}/files", files={"file": ("l.png", PNG, "image/png")},
                data={"kind": "logo"}, headers=H)
    client.post(f"/api/advertisers/{adv['id']}/files?kind=product", files={"file": ("p.jpg", JPG, "image/jpeg")},
                headers=H)
    camp = _campaign(client, adv, ins)
    assert ads.prompt_lines(settings, {"product": "x"}) == []
    lines = ads.prompt_lines(settings, {"campaign": camp["id"]})
    text = "\n".join(lines)
    assert "addons/ad-insert/README.md" in text and "hero_uses_product" in text and "20%" in text
    assert "Герой пользуется пицца «Маргарита» от Ромашка" in text
    path = settings.factory.data_dir / "advertisers" / adv["id"] / "advertiser.json"
    assert str(path.resolve()) in text
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["erid"] == "2VtzqX" and data["forbidden_claims"] == ["лучший в мире"]
    assert data["logo_path"].endswith("logo.png") and len(data["product_photos"]) == 1
    assert all(p.startswith("/") for p in [data["logo_path"], *data["product_photos"]])
    # pinned version wins over later edits
    client.patch(f"/api/inserts/{ins['id']}", json={"template": "НОВОЕ {brand}"}, headers=H)
    assert "НОВОЕ" not in "\n".join(ads.prompt_lines(settings, {"campaign": camp["id"]}))
    # missing campaign -> explicit stop, no invented data
    assert "FAILED" in ads.prompt_lines(settings, {"campaign": "cmp_gone"})[0]
    # erid pending note
    camp2 = _campaign(client, adv, ins, erid="")
    assert any("ERID is not issued" in ln for ln in ads.prompt_lines(settings, {"campaign": camp2["id"]}))
    # advertiser.json is never served through the file endpoint
    assert client.get(f"/api/advertisers/{adv['id']}/file/advertiser.json").status_code == 404
