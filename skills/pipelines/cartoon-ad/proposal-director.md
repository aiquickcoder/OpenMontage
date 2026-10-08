# Proposal Director - Cartoon Ad Pipeline

## Goal

Produce `proposal_packet` and `decision_log`: at least three genuinely
different story concepts with a native integration, a locked production plan
and an honest Flow credit estimate.

## Native Integration Principles

- **The product is a plot device, not a pause.** The hero has a concrete,
  relatable problem; the product is what resolves it (or makes the twist
  possible). Remove the product and the story should break.
- **Show the benefit, not the feature list.** One benefit, shown in action.
- **Screen time ≤ 20-25 %** of the runtime, including the packshot.
- **Truthful.** Only claims present in the brief; nothing in
  `forbidden_claims`. Exaggeration is fine only when it is obviously cartoon
  fantasy and does not imply a real product property.
- **Introduce the product early enough** (by ~40 % of runtime) so the payoff
  feels earned, then land the packshot / CTA in the last 3-5 s.

## Concept Options (≥ 3)

For each concept give: logline (one sentence), cast (1-2 characters ideal,
3 maximum — every extra character multiplies consistency risk), the problem,
how the product resolves it, the emotional arc, shot count, and why it fits
the audience. Make them differ in genre (comedy / heartfelt / adventure), not
just in props.

Optionally render one free concept frame per option with `flow_image`
(`model: nano-pro`, `aspect_ratio: 9:16`) to make the choice visual.

## Production Plan

- Format 9:16, duration, shot count (4-11, each ≤ 8 s).
- Per-shot model plan: `veo-fast` default; `veo-quality` for the hero moment
  and the product shot (≤ 2 shots). `veo-quality` does not accept reference
  images — it is always image-to-video from the keyframe.
- Render runtime: Present both options (`remotion` and `hyperframes`) when
  both are available and lock the choice as `render_runtime` in the
  proposal_packet, logged in decision_log under `render_runtime_selection`.
  Typical answer here: `remotion` for the cut of Veo clips, with the end card
  authored in hyperframes and rendered to a clip. Never swap it silently later.
- Music plan: mood, source (`music_gen` / `pixabay_music` / `freesound_music`),
  ducking under dialogue.

## Cost Estimate

`cost_estimate` must state **USD 0 API spend** and the Flow credit plan:

| Item | Pro | Ultra |
|---|---|---|
| Images (bible, storyboard) | 0 (daily quota) | 0 |
| veo-lite-lp draft clip | — | 0 (lower-priority queue) |
| veo-lite clip | 10 | 5 |
| veo-fast clip | 20 | 20 |
| veo-quality clip | 100 | 100 |

Example 60 s on Ultra: drafts free, 6 × 20 × 1.2 + 2 × 100 ≈ 345 credits.
On Pro: 8 × 20 × 1.5 takes + 2 × 100 ≈ 440 credits. Compare against the
current balance and `FLOW_CREDIT_BUDGET_PER_PROJECT`; if it does not fit,
offer a shorter cut or fewer quality shots.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
