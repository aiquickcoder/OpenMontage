"""Drive Google Flow's agent-only composer directly with Playwright.

Some accounts get Flow's "agent-only composer": generation is requested by chatting
with Flow's creation agent, and gflow 0.83.1 cannot drive it (gflow issue #799).
This driver types the request into that composer and harvests the finished media
from Flow's own RPC responses:

- signed CDN URLs ``https://flow-content.google/{image|video}/<media-id>?Expires=…``
  appear in media RPC payloads once a generation lands;
- the agent's reasoning and tool calls stream through
  ``FlowCreationAgentService/StreamChat`` (kept for the run log);
- ``VideoFxService.GetCredits`` (rpc ``nzlxg``) reports the credit balance.

FLOW_BRIDGE=1 also neutralises Flow's client-side region gate (see gflow_bridge.py).
Unofficial: Flow's UI and private RPCs can change without notice.

CLI:
    python -m tools.flow.agent_driver --profile ultra --out DIR "prompt" [--project URL] [--timeout 900]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from tools.flow.gflow_client import _profile_lock

FLOW_HOME = "https://flow.google.com/"
GATE_URL = re.compile(r".*/batchexecute\?rpcids=KV2T2d.*")
GATE_RESULT = re.compile(r'(\["wrb\.fr","KV2T2d",)"\[\d\]"')
MEDIA_URL = re.compile(r"https://flow-content\.google/(image|video)/([0-9a-f-]{36})\?[^\"\\\s]+")
CREDITS = re.compile(r'\["wrb\.fr","nzlxg","\[(\d+),(\d+)')
COMPOSER = "textarea:visible, [contenteditable='true']:visible"


def profile_dir(profile: str) -> Path:
    override = os.environ.get("FLOW_PROFILE_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "gflow-cli"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "gflow-cli"
    return base / f"profile_{profile}"


def bridge_enabled() -> bool:
    return os.environ.get("FLOW_BRIDGE") == "1"


# Model names as Flow's agent spells them in its replies and tool calls.
MODEL_PATTERNS = {
    "veo-lite-lp": r"lite\s*\[?\s*lower\s*priority|lite[_-]?lp|lower[_ ]priority",
    "veo-lite": r"veo[\s_-]*3\.?1?[\s_-]*-?\s*lite|veo[_-]?lite",
    "veo-fast": r"veo[\s_-]*3\.?1?[\s_-]*-?\s*fast|veo[_-]?fast",
    "veo-quality": r"veo[\s_-]*3\.?1?[\s_-]*-?\s*quality|veo[_-]?quality",
    "omni-flash": r"omni[\s_-]*(1\.1)?[\s_-]*flash",
    "nano-pro": r"nano[\s_-]*banana[\s_-]*pro|nano[_-]?pro",
    "nano2": r"nano[\s_-]*banana[\s_-]*2|nano[_-]?2",
    "image4": r"imagen[\s_-]*4",
}


def models_mentioned(stream: str) -> list[str]:
    """Model aliases named in the agent stream (reasoning + tool calls), in alias order."""
    text = stream.replace("\\n", " ").lower()
    found = []
    for alias, pat in MODEL_PATTERNS.items():
        if re.search(pat, text):
            found.append(alias)
            if alias == "veo-lite-lp":
                # "Veo 3.1 - Lite [Lower Priority]" must not also count as plain Lite
                text = re.sub(r"veo[\s_-]*3\.?1?[\s_-]*-?\s*lite\s*\[?\s*lower\s*priority\]?", " ", text)
    return found


CONFIRM_ASK = re.compile(
    r"would you like me to|shall i (?:go ahead|proceed|start)|do you want me to|"
    r"should i (?:go ahead|proceed|start)|ready to (?:kick off|start|generate)", re.I)
QUOTED_COST = re.compile(r"cost(?:ing|s)?\s*(?:about|around|~)?\s*(\d+)\s*credits?", re.I)
CONFIRM_REPLY = "Yes, go ahead."
# Flow's go-ahead is a radiogroup inside <flow-permission-message>, not buttons:
# <div role="radio" aria-label="Approve" aria-checked="false">. Never "Always approve".
APPROVE_OPTION = ('flow-permission-message [role="radio"][aria-label="Approve"]'
                  '[aria-checked="false"]:not([aria-disabled="true"])')
OVERLAY_ACCEPT = re.compile(r"^\s*(i agree|agree|accept|i understand|got it|continue|ok|done)\s*$", re.I)


def confirmation_request(reply: str) -> Optional[int]:
    """Agent paused to ask for a go-ahead. Returns the quoted cost (0 if none), else None."""
    tail = reply[-800:]
    if not CONFIRM_ASK.search(tail):
        return None
    costs = [int(c) for c in QUOTED_COST.findall(tail)]
    return max(costs) if costs else 0


def one_line(text: str) -> str:
    """Flatten a multi-line brief: lines become sentences separated by a space."""
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def unescape_urls(body: str) -> str:
    """Undo JSON escaping of '=' / '&' at any nesting depth (StreamChat double-escapes: \\\\u003d)."""
    return re.sub(r"\\+u0026", "&", re.sub(r"\\+u003d", "=", body))


def refusal_code(stream: str) -> Optional[str]:
    m = re.search(r"PUBLIC_ERROR_[A-Z_]+", stream)
    return m.group(0) if m else None


@dataclass
class Media:
    kind: str
    media_id: str
    url: str
    path: Optional[str] = None


@dataclass
class AgentResult:
    project_url: str
    media: list[Media] = field(default_factory=list)
    credits_before: Optional[int] = None
    credits_after: Optional[int] = None
    reply_text: str = ""
    stream_log: str = ""
    models_mentioned: list[str] = field(default_factory=list)
    refused: Optional[str] = None
    confirmed_costs: list[int] = field(default_factory=list)
    declined_cost: Optional[int] = None

    @property
    def credits_spent(self) -> Optional[int]:
        if self.credits_before is None or self.credits_after is None:
            return None
        return self.credits_before - self.credits_after


class FlowAgentDriver:
    """One browser session on a gflow profile. Use as a context manager."""

    def __init__(self, profile: str = "default", *, headless: bool = False, bridge: Optional[bool] = None):
        self.profile_path = profile_dir(profile)
        self.headless = headless
        self.bridge = bridge_enabled() if bridge is None else bridge
        self._bodies: list[tuple[str, str]] = []
        self.overlay_log: list[str] = []

    # -- lifecycle -------------------------------------------------------
    def __enter__(self) -> "FlowAgentDriver":
        from playwright.sync_api import sync_playwright

        self._lock = _profile_lock()
        self._lock.__enter__()
        self._pw = sync_playwright().start()
        # Same launch shape as gflow's own generation context (gflow_cli/api/client.py):
        # without it reCAPTCHA Enterprise scores the session low and the agent
        # answers PUBLIC_ERROR_UNUSUAL_ACTIVITY.
        self.ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_path), channel="chrome", headless=self.headless,
            viewport={"width": 1366, "height": 860},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--password-store=basic", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        self.ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        if self.bridge:
            self.ctx.route(GATE_URL, self._patch_gate)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.on("response", self._record)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self.ctx.close()
            self._pw.stop()
        finally:
            self._lock.__exit__(*exc)

    @staticmethod
    def _patch_gate(route: Any) -> None:
        resp = route.fetch()
        # "[ ]" has the same byte length as "[4]", so batchexecute length prefixes stay valid
        route.fulfill(response=resp, body=GATE_RESULT.sub(r'\1"[ ]"', resp.text()))

    def _record(self, resp: Any) -> None:
        url = resp.url
        if "batchexecute" not in url and "StreamChat" not in url:
            return
        try:
            self._bodies.append((url, resp.text()))
        except Exception:
            pass

    # -- helpers ---------------------------------------------------------
    def _media_since(self, mark: int) -> dict[str, Media]:
        found: dict[str, Media] = {}
        for _, body in self._bodies[mark:]:
            for m in MEDIA_URL.finditer(unescape_urls(body)):
                prev = found.get(m.group(2))
                # the same id can appear truncated elsewhere; keep the fullest (signed) URL
                if prev is None or len(m.group(0)) > len(prev.url):
                    found[m.group(2)] = Media(kind=m.group(1), media_id=m.group(2), url=m.group(0))
        return found

    def _new_media(self, mark: int, known: set[str], expect: str) -> list[Media]:
        return [m for mid, m in self._media_since(mark).items()
                if mid not in known and expect in ("any", m.kind)]

    def _last_credits(self) -> Optional[int]:
        for _, body in reversed(self._bodies):
            m = CREDITS.search(body)
            if m:
                return int(m.group(1))
        return None

    def _check_gate(self) -> None:
        if "/unavailable" in self.page.url:
            raise RuntimeError("Flow served /unavailable — set FLOW_BRIDGE=1 or use a non-RU IP")

    def credits(self) -> Optional[int]:
        self.page.goto(FLOW_HOME, wait_until="domcontentloaded", timeout=90_000)
        self.page.wait_for_timeout(8_000)
        self._check_gate()
        return self._last_credits()

    def open_project(self, project_url: Optional[str] = None) -> str:
        if project_url:
            self.page.goto(project_url, wait_until="domcontentloaded", timeout=90_000)
        else:
            if "flow.google.com" not in self.page.url or "/project/" in self.page.url:
                self.page.goto(FLOW_HOME, wait_until="domcontentloaded", timeout=90_000)
                self.page.wait_for_timeout(6_000)
            self._check_gate()
            self.page.get_by_role("button", name=re.compile("New project", re.I)).first.click()
            self.page.wait_for_url(re.compile(r".*/project/[0-9a-f-]{36}.*"), timeout=60_000)
        self.page.wait_for_timeout(8_000)
        self._check_gate()
        self.page.keyboard.press("Escape")
        return self.page.url

    def attach(self, paths: list[str], *, per_file_wait_ms: int = 6_000) -> None:
        """Upload local images into the composer (keyframes, references)."""
        if not paths:
            return
        files = [str(Path(p).resolve()) for p in paths]
        inputs = self.page.locator("input[type=file]")
        if inputs.count() == 0:
            # the file input is created lazily behind the composer's "add" button
            self.page.get_by_role("button", name=re.compile(r"^add$|add media|upload", re.I)).last.click()
            self.page.wait_for_timeout(1_500)
            upload = self.page.get_by_text(re.compile(r"upload", re.I))
            if upload.count():
                with self.page.expect_file_chooser(timeout=15_000) as chooser:
                    upload.first.click()
                chooser.value.set_files(files)
                self.page.wait_for_timeout(per_file_wait_ms * len(files))
                self._clear_overlays()
                return
        inputs.last.set_input_files(files)
        self.page.wait_for_timeout(per_file_wait_ms * len(files))
        self._clear_overlays()

    def _clear_overlays(self, rounds: int = 4) -> None:
        """Close dialogs left over the composer after an upload (first-upload consent,
        the add-media menu). Consent-style buttons are accepted, anything else is escaped."""
        for _ in range(rounds):
            backdrop = self.page.locator(".cdk-overlay-backdrop-showing")
            if backdrop.count() == 0:
                return
            pane = self.page.locator(".cdk-overlay-pane").last
            text = (pane.inner_text(timeout=5_000) if pane.count() else "")[:300]
            self.overlay_log.append(text)
            accept = pane.get_by_role("button", name=OVERLAY_ACCEPT) if pane.count() else None
            if accept is not None and accept.count():
                accept.first.click(timeout=10_000)
            else:
                self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(1_500)
        if self.page.locator(".cdk-overlay-backdrop-showing").count():
            raise RuntimeError(f"a Flow dialog stays open over the composer: {self.overlay_log[-1]!r}")

    def _send(self, text: str) -> None:
        box = self.page.locator(COMPOSER).last
        box.click()
        # typed "\n" is an Enter keypress → the composer would submit only the first line
        # timeout=0: Playwright's default 30 s would abort briefs longer than ~1500 chars
        box.type(one_line(text), delay=8, timeout=0)
        self.page.wait_for_timeout(600)
        self.page.keyboard.press("Enter")

    def _visible_approvals(self) -> int:
        buttons = self.page.locator(APPROVE_OPTION)
        return sum(1 for i in range(buttons.count()) if buttons.nth(i).is_visible())

    def _approve_button(self) -> Any:
        """Flow's inline go-ahead (Approve / Always approve / Reject); a typed "yes" is
        taken as a new chat message. Returns the visible, unchecked Approve option, or None."""
        buttons = self.page.locator(APPROVE_OPTION)
        for i in range(buttons.count() - 1, -1, -1):
            if buttons.nth(i).is_visible():
                return buttons.nth(i)
        return None

    def _reply_text(self) -> str:
        # the visible chat ends with the agent's latest message, followed by the composer chrome
        text = self.page.evaluate("document.body.innerText")
        cut = text.rfind("What do you want to create?")
        return (text[:cut] if cut > 0 else text)[-1500:]

    # -- main entry ------------------------------------------------------
    def ask(self, prompt: str, *, expect: str = "any", count: int = 1,
            timeout: float = 900, settle: float = 15,
            attachments: Optional[list[str]] = None,
            confirm_up_to: Optional[int] = None, max_confirms: int = 2) -> AgentResult:
        """Send one request to the agent and wait for `count` new media of kind `expect`.

        Media already seen before submission (project history, uploaded attachments)
        are never reported as results. When the agent pauses to ask for a go-ahead
        ("…costing 15 credits?") it is confirmed if the quoted cost is <= `confirm_up_to`
        (None = any cost), at most `max_confirms` times; otherwise the request ends.
        """
        result = AgentResult(project_url=self.page.url, credits_before=self._last_credits())
        self.attach(list(attachments or []))
        known = set(self._media_since(0))
        mark = len(self._bodies)
        approve_seen = self._visible_approvals()  # stale buttons from earlier turns are ignored
        self._send(prompt)

        deadline = time.monotonic() + timeout
        turn_mark, turn_start = mark, time.monotonic()
        ready_at: Optional[float] = None
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(5_000)
            stream = "".join(b for u, b in self._bodies[mark:] if "StreamChat" in u)
            if "PUBLIC_ERROR_" in stream:
                break  # agent refused (anti-abuse / policy); waiting longer won't help
            # The go-ahead request is itself a pending generate_* call, so look for its
            # inline Approve button rather than for the absence of a tool call.
            approve = self._approve_button() if self._visible_approvals() > approve_seen else None
            if approve is not None:
                cost = confirmation_request(self._reply_text()) or 0
                if (confirm_up_to is not None and cost > confirm_up_to) or len(result.confirmed_costs) >= max_confirms:
                    result.declined_cost = cost
                    break
                result.confirmed_costs.append(cost)
                approve.click(timeout=15_000)
                self.page.wait_for_timeout(3_000)
                approve_seen = self._visible_approvals()  # if the clicked button lingers, never re-click
                turn_mark, turn_start = len(self._bodies), time.monotonic()
                continue
            turn = "".join(b for u, b in self._bodies[turn_mark:] if "StreamChat" in u)
            last_stream = max((i for i, (u, _) in enumerate(self._bodies) if "StreamChat" in u), default=-1)
            if (last_stream >= turn_mark and "generate_" not in turn
                    and time.monotonic() - turn_start > 90):
                # agent answered without calling a generation tool: a text go-ahead or a question
                cost = confirmation_request(self._reply_text())
                if cost is None:
                    break
                if (confirm_up_to is not None and cost > confirm_up_to) or len(result.confirmed_costs) >= max_confirms:
                    result.declined_cost = cost
                    break
                result.confirmed_costs.append(cost)
                turn_mark, turn_start = len(self._bodies), time.monotonic()
                self._send(CONFIRM_REPLY)
                continue
            media = self._new_media(mark, known, expect)
            if len(media) >= count:
                ready_at = ready_at or time.monotonic()
                if time.monotonic() - ready_at >= settle:
                    break
        result.media = self._new_media(mark, known, expect)
        result.stream_log = "\n".join(b for u, b in self._bodies[mark:] if "StreamChat" in u)
        result.models_mentioned = models_mentioned(result.stream_log)
        result.refused = refusal_code(result.stream_log)
        result.reply_text = self._reply_text()
        try:
            self.page.reload(wait_until="domcontentloaded")
            self.page.wait_for_timeout(8_000)
        except Exception:
            pass
        result.credits_after = self._last_credits()
        return result

    def download(self, media: Media, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        resp = self.ctx.request.get(media.url, timeout=300_000)
        if not resp.ok:
            raise RuntimeError(f"download {media.media_id}: HTTP {resp.status}")
        ext = {"image": ".png", "video": ".mp4"}[media.kind]
        ctype = resp.headers.get("content-type", "")
        if "jpeg" in ctype:
            ext = ".jpg"
        path = out_dir / f"{media.media_id}{ext}"
        path.write_bytes(resp.body())
        media.path = str(path)
        return path


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prompt")
    ap.add_argument("--profile", default=os.environ.get("FLOW_PROFILE", "default"))
    ap.add_argument("--project", help="existing Flow project URL (default: new project)")
    ap.add_argument("--expect", choices=["any", "image", "video"], default="any")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args(argv)

    with FlowAgentDriver(args.profile, headless=args.headless) as drv:
        drv.credits()
        drv.open_project(args.project)
        res = drv.ask(args.prompt, expect=args.expect, count=args.count, timeout=args.timeout)
        for m in res.media:
            drv.download(m, args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "agent_stream.log").write_text(res.stream_log, encoding="utf-8")
    payload = {**asdict(res), "credits_spent": res.credits_spent}
    payload.pop("stream_log")
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0 if res.media else 1


if __name__ == "__main__":
    sys.exit(main())
