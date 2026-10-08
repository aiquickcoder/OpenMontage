# Asset Director - Cartoon Ad Pipeline (Animation + Take Review)

## Goal

Produce `asset_manifest`: one approved video clip per shot (with Veo native
audio), plus the music bed. Spend credits only on prompts already proven by the
storyboard.

## Before Spending

1. Read `scene_plan.metadata.storyboard`.
2. `flow_veo_video` `dry_run` for each shot → total credits; compare with the
   balance and `FLOW_CREDIT_BUDGET_PER_PROJECT`. Present the per-shot plan
   (shot, model, duration, credits) and get the go-ahead for the batch.

## Generation

For each shot, sequentially (never in parallel — Flow is one browser session
and parallel bursts trigger the WAF):

```json
{"operation": "image_to_video",
 "image_path": "<keyframe_path>",
 "prompt": "<veo_prompt verbatim>",
 "model": "veo-fast",
 "duration": 8,
 "aspect_ratio": "9:16",
 "count": 1,
 "scene_id": "s03",
 "project_dir": "projects/<id>",
 "output_path": "projects/<id>/assets/video/s03_take1.mp4"}
```

- Use `first_last_frame_to_video` (`last_frame_path`) when the storyboard
  defines an end frame (exact landing pose, product reveal).
- **Ultra two-pass flow** (`FLOW_PLAN=ultra`):
  1. *Draft pass* — `model: "veo-lite-lp"` (0 credits), `count: 2-4`,
     `output_path: .../s03_draft.mp4`. Run take review on drafts; fix prompt or
     keyframe and re-draft until timing, camera, action and the line read right.
     Drafts are free but slow (lower-priority queue) — queue them shot by shot.
  2. *Final pass* — same keyframe + proven prompt verbatim on `veo-fast`
     (`count: 1`), or `veo-quality` for hero / product shots. Re-review.
  A lite-lp draft may ship as final only if it passes the full review and the
  user accepts the lower model quality for that shot.
- On Pro there is no free pass: draft directly on `veo-fast`.
- `veo-quality` only for the planned hero / product shots, and only after the
  same prompt produced a good draft (or the user approved spending).
- `reference_to_video` is a fallback for shots with no usable keyframe; it is
  not available on `veo-quality`.
- Never use `text_to_video` for a shot with a character or the product.
- Record the returned `media_id` (needed for `flow_video_upscale`).

## Take Review (every clip)

Sample frames with `frame_sampler` (start, 25 %, 50 %, 75 %, end) and listen to
the audio. Fail the take if any of:

- **Anatomy:** extra / missing fingers, merged limbs, melting faces, eyes drifting.
- **Identity:** face, hair, outfit colors differ from the bible.
- **Product:** wrong shape, color, proportions; invented text on the label.
- **Style drift:** turns photoreal, 2D, or changes lighting mid-clip.
- **Speech:** wrong words, non-Russian / accented speech, wrong speaker talks,
  voice differs from the character's other shots, lips not matching.
- **Unwanted text:** subtitles or captions burned in.
- **Cut points:** heavy morphing in the first / last 0.5 s (trim in edit if
  only there).

On fail: fix the prompt or keyframe (free) before re-rolling. Max 2 paid
re-rolls per shot without asking the user. Keep failed takes out of the
manifest but log them in `metadata.rejected_takes` with reasons.

If the same character's voice keeps drifting between shots, tell the user —
options: rephrase the voice descriptor, put both lines of a dialogue into one
shot, or accept.

## Music

Mood from the proposal. Instrumental only, no vocals (vocals fight Veo
dialogue). Length ≥ runtime. Record license.

## Output

Each asset: `type: "video"`, `path`, `source_tool: "flow_veo_video"`,
`scene_id`, `prompt`, `model`, `provider: "google_flow"`, `cost_usd: 0`,
`duration_seconds`, `resolution`, and in `generation_summary` the credits
spent and media id. Music as `type: "music"`.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
