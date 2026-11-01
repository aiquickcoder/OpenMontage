"""The AI behind the judge, the writer, the checker and the watermark eye.

Provider (owner 2026-11-01: «весь ИИ переключаем … на Codex GPT по подписке, трафик — через VPN»):
- codex (default): our codex-gateway on this server (codex-gateway.service, POST http://172.22.0.1:8282/v1/responses,
  OpenAI Responses API, streamed as SSE). The gateway adds the ChatGPT subscription's login and goes out through the
  VPN proxy itself; we send `Authorization: Bearer $CODEX_GW_SECRET` — the units read /etc/codex-gateway.env, the secret
  is never copied or printed. Billed by the subscription, not per token: llm_usage keeps the tokens, cost 0, and
  codex_calls_per_day caps the calls so a loop gone wrong cannot eat the subscription's limits.
- openrouter: the old way (chat/completions, key OPENROUTER_API_KEY, through the egress proxy) — only when
  llm_provider is set to it, or as a fallback when openrouter_fallback is switched on (off by default).
Models are settings (model_judge / model_write / model_check / model_vision / model_fallback), on the web page.
Answers are JSON: the prompt asks for it and `json_of` takes the outermost value out of the reply.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from addons.channels import config

CODEX_URL = config.env("CHANNELS_CODEX_URL", "http://172.22.0.1:8282/v1/responses")
CODEX_KEY_ENV = "CODEX_GW_SECRET"


class BudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))


def spent_today(db) -> float:
    r = db.one("SELECT COALESCE(SUM(cost), 0) AS c FROM llm_usage WHERE day=?", (_day(),))
    return float(r["c"] if r else 0)


def calls_today(db) -> int:
    r = db.one("SELECT COALESCE(SUM(calls), 0) AS c FROM llm_usage WHERE day=? AND model NOT LIKE '%/%'", (_day(),))
    return int(r["c"] if r else 0)


def _opener(proxy: str = ""):
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))  # the gateway is local: no proxy


def image_part(path: Path, max_side: int = 1024) -> dict:
    """An image for a vision prompt, downscaled JPEG as a data URL."""
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        raise LLMError(f"cannot read {path}")
    return image_part_array(img, max_side)


def image_part_array(img, max_side: int = 1024) -> dict:
    import cv2

    h, w = img.shape[:2]
    k = min(1.0, max_side / max(h, w))
    if k < 1:
        img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return {"type": "image", "url": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()}


def _parts(user: Any, provider: str) -> Any:
    if isinstance(user, str):
        return [{"type": "input_text", "text": user}] if provider == "codex" else user
    out = []
    for p in user:
        if p.get("type") == "image":
            out.append({"type": "input_image", "image_url": p["url"]} if provider == "codex" else
                       {"type": "image_url", "image_url": {"url": p["url"]}})
        elif p.get("type") in ("text", "input_text"):
            out.append({"type": "input_text" if provider == "codex" else "text", "text": p["text"]})
    return out


def chat(db, purpose: str, model: str, system: str, user: Any, *, max_tokens: int = 1500, temperature: float = 0.3,
         timeout: float = 300, tries: int = 3, fallback: bool = True) -> str:
    """One completion. `user` is a string or a list of parts ({"type": "text"} / image_part()). When the model keeps
    failing the request goes once to model_fallback (same provider), and — only if openrouter_fallback is on — to
    OpenRouter's model_openrouter."""
    provider = config.setting(db, "llm_provider") or "codex"
    try:
        return _call(db, provider, purpose, model, system, user, max_tokens, temperature, timeout, tries)
    except BudgetExceeded:
        raise
    except LLMError:
        if not fallback:
            raise
        alt = config.setting(db, "model_fallback")
        if alt and alt != model:
            try:
                return _call(db, provider, purpose, alt, system, user, max_tokens, temperature, timeout, 1)
            except LLMError:
                pass
        if provider == "codex" and config.setting(db, "openrouter_fallback"):
            return _call(db, "openrouter", purpose, config.setting(db, "model_openrouter"), system, user, max_tokens,
                         temperature, timeout, 1)
        raise


def _call(db, provider, purpose, model, system, user, max_tokens, temperature, timeout, tries) -> str:
    if provider == "codex":
        cap = int(config.setting(db, "codex_calls_per_day") or 0)
        if cap and calls_today(db) >= cap:
            raise BudgetExceeded(f"дневной лимит вызовов подписки ({cap}) исчерпан")
        effort = config.setting(db, f"effort_{purpose}") or config.setting(db, "reasoning_effort") or ""
        if model == config.setting(db, "model_fallback") and effort == "high":
            effort = "medium"  # the fallback is there to answer, and fast
        return _codex(db, purpose, model, system, user, timeout, tries, effort)
    budget = float(config.setting(db, "daily_budget_usd"))
    if spent_today(db) >= budget:
        raise BudgetExceeded(f"дневной бюджет OpenRouter ${budget:g} исчерпан")
    return _openrouter(db, purpose, model, system, user, max_tokens, temperature, timeout, tries)


def _codex(db, purpose, model, system, user, timeout, tries, effort: str = "") -> str:
    key = os.environ.get(CODEX_KEY_ENV, "")
    if not key:
        raise LLMError(f"{CODEX_KEY_ENV} is not set (the unit needs EnvironmentFile=-/etc/codex-gateway.env)")
    body = {"model": model, "instructions": system, "input": [{"role": "user", "content": _parts(user, "codex")}],
            "stream": True}
    if effort:
        body["reasoning"] = {"effort": effort}
    req = urllib.request.Request(CODEX_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream"})
    last: Exception | None = None
    for k in range(tries):
        try:
            text, usage, failed = [], {}, ""
            with _opener().open(req, timeout=timeout) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    ev = json.loads(data)
                    kind = ev.get("type", "")
                    if kind == "response.output_text.delta":
                        text.append(ev.get("delta", ""))
                    elif kind == "response.completed":
                        usage = (ev.get("response") or {}).get("usage") or {}
                    elif kind in ("response.failed", "error", "response.incomplete"):
                        failed = json.dumps(ev.get("response", ev), ensure_ascii=False)[:300]
            db.usage(model, purpose, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), 0.0)
            out = "".join(text)
            if failed and not out.strip():
                raise LLMError(f"codex: {failed}")
            if not out.strip():
                raise LLMError("codex: empty answer")
            return out
        except urllib.error.HTTPError as e:
            body_err = e.read()[:300]
            last = LLMError(f"codex HTTP {e.code}: {body_err!r}")
            if e.code == 401:
                raise LLMError("codex-gateway: 401 — вход подписки протух, шлюз надо перелогинить") from None
            if e.code in (400, 403, 404):
                break
            if e.code == 429:
                time.sleep(30 * (k + 1))
                continue
        except (urllib.error.URLError, TimeoutError, LLMError, ValueError, OSError) as e:
            last = e
        time.sleep(3 * (k + 1))
    raise LLMError(str(last))


def _openrouter(db, purpose, model, system, user, max_tokens, temperature, timeout, tries) -> str:
    key = os.environ.get(config.LLM_KEY_ENV, "")
    if not key:
        raise LLMError(f"{config.LLM_KEY_ENV} is not set")
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "usage": {"include": True},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": _parts(user, "openrouter")}]}
    req = urllib.request.Request(config.LLM_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        # Cloudflare in front of OpenRouter answers «error code: 1010» to urllib's default User-Agent now and then
        "User-Agent": "Mozilla/5.0 (compatible; zasnyali-channels/1.0)",
        "HTTP-Referer": "https://studio.luvael.ru", "X-Title": "ZASNYALI channels"})
    last: Exception | None = None
    for k in range(tries):
        try:
            with _opener(config.LLM_PROXY).open(req, timeout=timeout) as r:
                data = json.loads(r.read())
            if data.get("error"):
                raise LLMError(str(data["error"])[:300])
            u = data.get("usage") or {}
            db.usage(model, purpose, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0),
                     float(u.get("cost") or 0.0))
            choice = (data.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            if isinstance(text, list):
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            if not text.strip():
                raise LLMError("empty answer")
            if choice.get("finish_reason") in ("error", "content_filter"):
                raise LLMError("the answer was cut (provider error or content filter)")
            return text
        except urllib.error.HTTPError as e:
            body_err = e.read()[:300]
            last = LLMError(f"HTTP {e.code}: {body_err!r}")
            if e.code in (400, 401, 402) or (e.code == 403 and b"1010" not in body_err):
                break
        except (urllib.error.URLError, TimeoutError, LLMError, ValueError, OSError) as e:
            last = e
        time.sleep(3 * (k + 1))
    raise LLMError(str(last))


def _balanced(t: str, i: int) -> str | None:
    """The bracketed JSON value starting at t[i], strings respected; None when it never closes."""
    opener = t[i]
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for j in range(i, len(t)):
        ch = t[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return t[i:j + 1] if ch == closer else None
    return None


def json_of(text: str) -> Any:
    """The JSON value in a reply (models wrap it in ```json fences or prose): the outermost one that comes first —
    an array of objects is the array, not its first object. A broken or cut array gives the objects that parse."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if not starts:
        raise LLMError(f"no JSON in the answer: {text[:200]!r}")
    i = min(starts)
    chunk = _balanced(t, i)
    if chunk is not None:
        try:
            return json.loads(chunk)
        except ValueError:
            pass
    if t[i] == "[":  # salvage the array's objects one by one
        items, k = [], i + 1
        while True:
            k = t.find("{", k)
            if k == -1:
                break
            obj = _balanced(t, k)
            if obj is None:
                break
            try:
                items.append(json.loads(obj))
            except ValueError:
                pass
            k += len(obj)
        if items:
            return items
    raise LLMError(f"no JSON in the answer: {text[:200]!r}")


def chat_json(db, purpose: str, model: str, system: str, user: Any, **kw) -> Any:
    """A JSON answer; a reply that is no JSON (cut off mid-way — some providers stop on crime stories) is asked
    once more from the fallback model."""
    try:
        return json_of(chat(db, purpose, model, system, user, **kw))
    except LLMError as e:
        if "no JSON" not in str(e):
            raise
        alt = config.setting(db, "model_fallback") or model
        return json_of(chat(db, purpose, alt, system, user, **kw))
