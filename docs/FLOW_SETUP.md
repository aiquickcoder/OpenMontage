# Google Flow on a subscription (gflow-cli)

This fork generates Google images and video through **Google Flow** using a
**Google AI Pro / Ultra subscription**, instead of the pay-per-call Gemini /
Vertex API. Automation is done by [gflow-cli](https://github.com/ffroliva/gflow-cli)
(MIT, pinned to `0.83.1`), which drives a real Chrome profile logged into Flow.

| Tool | Capability | What it does |
|---|---|---|
| `flow_image` | image_generation | Nano Banana Pro / Nano Banana 2 / Imagen 4, text-to-image and image-to-image with up to 10 references |
| `flow_veo_video` | video_generation | Veo 3.1 lite / fast / quality (+ omni-flash): text-, image-, first/last-frame- and reference-to-video, native audio and dialogue |
| `flow_video_upscale` | video_upscale | 1080p export of a Flow clip by `media_id` |

All three have `provider = "google_flow"`. Route selectors with
`allowed_providers: ["google_flow"]` so API providers are never picked silently.

## One-time setup

```bash
brew install uv                                   # if missing
uv tool install gflow-cli==0.83.1
uv tool run --from gflow-cli playwright install chromium
gflow auth login --browser chrome                 # sign in by hand in the window that opens
gflow auth status
gflow credits user                                # shows the monthly balance
```

- Flow is not available from every region. Chrome must reach `labs.google`
  through your VPN — a system-wide VPN (TUN mode) is the most reliable; a
  per-app proxy that Chrome does not use will fail with exit 39.
- gflow needs a **visible** Chrome window. It does not work headless, on a
  server or in a container; reCAPTCHA rejects headless browsers.
- Some accounts were moved to `flow.google.com`; there a few options are not
  supported yet (gflow exits 36 before spending anything).

## Credits and budget

Google AI Pro includes **1000 Flow credits per month** (no rollover).
Approximate cost per clip (8 s, 720p, with audio):

| Model | Credits | Use for |
|---|---|---|
| `veo-lite` | 10 | blocking / timing tests |
| `veo-fast` | 20 | default for most shots |
| `veo-quality` | 100 | hero and product shots only (no reference-to-video) |

Image generation uses a daily quota, not credits — iterate on character sheets
and storyboard frames freely, then animate only approved frames.

Every paid call is booked in `projects/<id>/flow_credits.json`. A call that
would push the project over `FLOW_CREDIT_BUDGET_PER_PROJECT` (default 500) is
refused **before** Flow is touched. A reservation is refunded only when gflow
proves the failure happened before submit; anything else counts as spent.
`count > 1` multiplies the cost.

## Anti-bot and failure handling

- Calls are serialized (one browser profile) and spaced by
  `FLOW_MIN_INTERVAL_SEC` (default 60 s) plus jitter.
- Paid calls are **never retried automatically**.

| Exit | Meaning | What to do |
|---|---|---|
| 3 / 8 | session expired / missing | `gflow auth login --browser chrome` |
| 4 | rate limit / quota | wait, retry later |
| 5 | content policy | rephrase the prompt |
| 10 | WAF 403 "unusual activity" | stop for 30–60 min, raise `FLOW_MIN_INTERVAL_SEC` to 90–120 |
| 23 | Flow UI changed | check gflow releases, `gflow update`, re-pin the version here |
| 37 | out of credits | wait for the monthly reset or top up |
| 39 | Flow unavailable | VPN / region |

A clip that was generated but failed to download keeps its `media_id` in the
tool result and the ledger — recover it with `gflow data list videos` instead
of generating again.

## Terms of service

gflow is unofficial and reverse-engineered. Automating Flow may conflict with
Google's terms; use a modest pace and accept the risk to the account. The
supported, paid alternative is the existing `veo_video` / `google_imagen` API
tools (set `GOOGLE_API_KEY`).

## Upgrading gflow

Bump `PINNED_VERSION` in `tools/flow/gflow_client.py`, run
`uv tool install gflow-cli==<new>`, then `pytest tests/tools/test_flow_tools.py`
and one free `flow_image` smoke call before any paid run.
