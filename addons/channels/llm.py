"""The AI behind the judge, the writer, the checker and the watermark eye: an OpenAI-compatible chat endpoint
(OpenRouter by default — config.LLM_URL, key from the env var named by config.LLM_KEY_ENV), through the egress proxy.
Models are settings (model_judge / model_write / model_check / model_vision), changed on the web page.

Every call is metered (llm_usage: tokens and OpenRouter's own cost) and refused past the day's budget
(daily_budget_usd), so a loop gone wrong costs a day's budget at most. Answers are JSON: the prompt asks for it and
`json_of` takes the first object or array out of the reply.
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


class BudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


def spent_today(db) -> float:
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
    r = db.one("SELECT COALESCE(SUM(cost), 0) AS c FROM llm_usage WHERE day=?", (day,))
    return float(r["c"] if r else 0)


def _opener():
    if config.LLM_PROXY:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"https": config.LLM_PROXY,
                                                                        "http": config.LLM_PROXY}))
    return urllib.request.build_opener()


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
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()}}


def chat(db, purpose: str, model: str, system: str, user: Any, *, max_tokens: int = 1500, temperature: float = 0.3,
         timeout: float = 120, tries: int = 3) -> str:
    """One completion. `user` is a string or a list of content parts (text + images)."""
    budget = float(config.setting(db, "daily_budget_usd"))
    if spent_today(db) >= budget:
        raise BudgetExceeded(f"дневной бюджет ИИ ${budget:g} исчерпан")
    key = os.environ.get(config.LLM_KEY_ENV, "")
    if not key:
        raise LLMError(f"{config.LLM_KEY_ENV} is not set")
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "usage": {"include": True},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(config.LLM_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        # Cloudflare in front of OpenRouter answers «error code: 1010» to urllib's default User-Agent now and then
        "User-Agent": "Mozilla/5.0 (compatible; zasnyali-channels/1.0)",
        "HTTP-Referer": "https://studio.luvael.ru", "X-Title": "ZASNYALI channels"})
    last: Exception | None = None
    for k in range(tries):
        try:
            with _opener().open(req, timeout=timeout) as r:
                data = json.loads(r.read())
            if data.get("error"):
                raise LLMError(str(data["error"])[:300])
            u = data.get("usage") or {}
            cost = float(u.get("cost") or 0.0)
            db.usage(model, purpose, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), cost)
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            text = msg.get("content") or ""
            if isinstance(text, list):
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            if not text.strip():
                raise LLMError("empty answer")
            return text
        except urllib.error.HTTPError as e:
            body = e.read()[:300]
            last = LLMError(f"HTTP {e.code}: {body!r}")
            if e.code in (400, 401, 402) or (e.code == 403 and b"1010" not in body):
                break
        except (urllib.error.URLError, TimeoutError, LLMError, ValueError, OSError) as e:
            last = e
        time.sleep(3 * (k + 1))
    raise LLMError(str(last))


def json_of(text: str) -> Any:
    """The first JSON object or array in a reply (models wrap it in ```json fences or prose)."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    for opener, closer in (("{", "}"), ("[", "]")):
        i = t.find(opener)
        while i != -1:
            depth, j, in_str, esc = 0, i, False, False
            while j < len(t):
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
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(t[i:j + 1])
                        except ValueError:
                            break
                j += 1
            i = t.find(opener, i + 1)
    raise LLMError(f"no JSON in the answer: {text[:200]!r}")


def chat_json(db, purpose: str, model: str, system: str, user: Any, **kw) -> Any:
    last: Exception | None = None
    for _ in range(2):
        try:
            return json_of(chat(db, purpose, model, system, user, **kw))
        except LLMError as e:
            if "no JSON" not in str(e):
                raise
            last = e
    raise LLMError(str(last))
