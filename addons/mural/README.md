# Add-on: Mural video (one continuous camera over a canvas of panels)

A short vertical video (9:16, 20–40 s) made from **one canvas of 6 panels**. A single virtual camera travels across the
canvas, pushing in and zooming into each panel in turn. There are no cuts: the travel between panels *is* the
transition. Captions and music sit on top. It costs **0 Flow credits**, because the panels are Nano Banana Pro images
and the rest is local.

The reference episode is «Племяшка и наушник», 2026-10-21, approved by the owner ("по камере все прямо неплохо").
Its full definition is in `story.example.json`.

Built on OpenMontage pieces, not a new engine:
- the panels are drawn through the Flow driver (`tools/graphics/flow_image.py`, `FLOW_DRIVER=agent`);
- `tools/enhancement/upscale.py` does the 2x Real-ESRGAN upscale;
- the camera is the HyperFrames blueprint `spatial-pan-stations` with the rules `viewport-change` and
  `multi-phase-camera` (`.agents/skills/hyperframes-animation/`);
- the HyperFrames runtime renders it.

## Pipeline (who runs what, where)

| # | Step | Where | Command |
|---|---|---|---|
| 1 | Write `projects/<slug>/story.json` (format below) | Mac | by hand / the `mural-video` agent |
| 2 | Draw cast sheets + panels in Flow | server 155 | `addons/mural/gen_panels.py <story.json> <out_dir>` via `systemd-run` (see the script docstring) |
| 3 | Review every panel by eye, pick a variant per panel; redraw failures | Mac | copy the picks to `projects/<slug>/assets/panels_src/<panel id>.png` |
| 4 | Upscale, clean, canvas, HyperFrames workspace, render, QA | Mac | `.venv/bin/python addons/mural/build.py projects/<slug>` |
| 5 | Review `qa/contact.jpg`, `qa/report.json` and key frames; fix and re-run with `--skip-upscale` | Mac | — |
| 6 | Deliver to `~/Desktop/<slug>/` and Telegram | server | `addons/mural/send_telegram.py "<caption>" files…` via `systemd-run --wait` with `EnvironmentFile` |

## story.json

```jsonc
{
  "slug": "plemyashka-naushnik", "title": "Племяшка и наушник",
  "layout": "strip",            // "strip" = panels in one row; the camera only travels RIGHT (owner's default)
                                 // "grid"  = 2 columns x 3 rows (the camera also moves down)
  "panel_aspect": "1:1",        // "1:1" | "9:16" | "16:9"
  "style": "STYLE: …",          // style anchor, put at the front of every prompt
  "characters": {"MAX": "MAX — …", "LILY": "LILY — …"},   // verbatim identity lines, repeated in every panel
  "sheets": [{"id": "sheet_max", "prompt": "Character: {MAX}"}],  // adult cast sheets only (refs)
  "panels": [{
    "id": "p1", "refs": ["sheet_max"], "duration": 6.6,
    "prompt": "Wide shot. …",
    "camera": [[t, fx, fy, zoom, ease, roll?], …],   // t: s from the panel's start; fx, fy: 0..1 inside the panel;
                                                     // zoom: 1 = the panel fills the frame height; ease: GSAP ease
    "captions": [[start, end, "фраза"], …],          // s from the panel's start, 2–5 words each
    "shake": [start, end, amplitude_px]              // optional
  }],
  "music": {"src": "path.mp3", "offset": 1.5, "volume": 0.5, "title": "…", "license": "…"}
}
```

The camera's first key in a panel (t > 0) is the **arrival**: the travel from the previous panel takes that long. The
last key of a panel is usually at `t = duration`.

## Rules (each one cost us a redo)

**Panels (Flow / Nano Banana Pro)**
- **No film words** in image prompts: "film still", "cinematic", "feature film", "8k", "9:16 vertical"-as-a-film-term.
  They make Nano Banana draw letterbox bands. End every prompt with the frame line in `gen_panels.py`.
- **Never write "panel"** or "in every panel" in a prompt. That makes it draw a 4-up comic inside one image.
  `gen_panels.py` appends "One single continuous image … not a comic, not a grid".
- **Identity:** keep each character's line verbatim in every prompt, and pass the **adult** cast sheets as refs.
  - Flow rejects children, and group pictures that contain a child, as references. A child is held by text alone, so
    expect small drift in a child's hair or face.
  - Describe secondary characters fully (hair, glasses, clothes), otherwise they change between panels.
- **On-screen text:** spell out the exact words ("only the words "Earbud" and "Connect"; no other text"). If you don't,
  the model invents gibberish.
- **Quota:** Nano Banana Pro has a per-model daily quota (`PUBLIC_ERROR_PER_MODEL_DAILY_QUOTA_REACHED`).
  - A run of 2 sheets + 6 panels × 2 variants fits in a day; other image jobs on the same day may push it over.
  - `gen_panels.py` stops on quota and resumes from where it stopped.
  - Avoid the free-plan peak, 14–17 UTC.
- **Review every image before use.** Open each one and check identity, the action asked for, no grid, no gibberish
  text, no bands. Redraw failures with `gen_panels.py … <panel id>` after deleting the old file.

**Canvas and camera (`build.py`)**
- **One continuous shot.** Never cut; the travel between panels is the transition. In a strip, x only ever
  increases. Inside a panel, push in, zoom or tilt toward the target.
- **The camera must never show anything outside the canvas.** `applyCamera()` does two things:
  - it clamps the frame centre to the canvas;
  - it floors the scale at `(frame + 60 px) / canvas`, so no zoom-out can ever reveal bands. Do not remove either.
- **Sharpness when zoomed.** A square panel at zoom 1 already fills the frame height (frame/panel ≈ 0.94).
  - Keep face zooms at or below ~1.7× zoom. Above that the 2x-upscaled panel gets soft.
  - With 9:16 panels the frame width is the limit.
- **Upscale.**
  - `build.py` runs Real-ESRGAN with a 15-minute watchdog per panel. On a Mac under memory pressure it can hang in
    state U at about 1% CPU. Then the panel falls back to Lanczos + light sharpen; the report says which panels did.
  - Close heavy apps before step 4.
- **The Flow sparkle watermark** in the bottom-right corner is inpainted automatically, at 9.4% of the short side
  from the corner.
- **Captions** are HyperFrames timed clips:
  - white, heavy font, dark outline;
  - about 360 px above the bottom;
  - 2–5 words per cue;
  - always on screen while that line plays.
- **Music:**
  - use a local Pixabay track (`~/Desktop/pixabay_примеры/`; `pixabay_music` gets HTTP 403 from the Mac);
  - set `offset` to skip a quiet intro;
  - the mix fades the music out over the last 2 s.

**Render**
- `build.py` runs `npx hyperframes check` and then `render --strict`. These are the same commands as
  `hyperframes_compose.render_existing`. They are called directly because that tool's 5 s `npm view` runtime probe
  fails when the registry answers in about 12 s.
- **QA:**
  - `qa/report.json` lists flat dark edge bands; it must be empty.
  - Dark *textured* edges, such as hair in a close-up, are fine; confirm by eye.
  - Always look at `qa/contact.jpg` (one frame per panel) and at frames around each zoom before delivering.

**Delivery**
- Telegram only from the server, through `systemd-run --wait` with `EnvironmentFile=/opt/openmontage/.env`.
  A plain `sudo -u factory python …` has no token or proxy and fails with "Network is unreachable".
- The video goes out with its real dimensions (`factory/tg.py` `send_video`). Put the canvas next to it on the
  Desktop.
