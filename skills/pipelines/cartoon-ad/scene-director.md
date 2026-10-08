# Scene Director - Cartoon Ad Pipeline (Storyboard)

## Goal

Produce `scene_plan`: one scene per script section, each with an **approved
9:16 keyframe** and the fully assembled Veo prompt. Images are free — this is
where quality is won. Do not move on with a keyframe you would not ship.

## Keyframe Generation

For each shot call `flow_image` (or `image_selector` with
`allowed_providers: ["google_flow"]`):

- `model: nano-pro`, `aspect_ratio: 9:16`, `number_of_images: 2-4`.
- `image_paths`: the turnaround(s) of characters in the shot + location
  reference + product sheet when the product is visible (nano-pro accepts up
  to 10; keep 2-5 for best adherence).
- Prompt = style lock + "[character prompt_tokens] [pose / expression /
  action start state], [location description], [camera framing], [lighting]"
  + negative_style.
- The keyframe is the **first frame** of the clip: show the starting pose of
  the action, mouth closed or neutral, eyes open, hands clearly visible or
  clearly out of frame.

Compose for vertical: subject in the upper two-thirds, keep the bottom ~20 %
free for subtitles, avoid important detail at the very edges (platform UI).

## Continuity

- Same location in consecutive shots: pass the previous approved keyframe as
  an extra reference so props, light direction and color temperature match.
- If the next shot must continue the motion exactly, plan
  `first_last_frame_to_video` or use the previous clip's last frame (extract
  with `frame_sampler` in assets) as the next keyframe reference.
- Keep a consistent screen direction (left/right) for characters across cuts.

## Veo Prompt Template (assembled now, used verbatim in assets)

```
{style_lock}
{character prompt_tokens}. {action — one beat, present tense, physical verbs}.
Camera: {shot size}, {movement: slow push-in / static / gentle pan}.
Lighting: {location lighting}.
{speaker name} говорит по-русски: «{line_ru}»
Голос: {voice descriptor}.
Звуки: {ambient + sfx}.
{negative_style}
```

No dialogue → replace the speech lines with "no dialogue, characters do not speak".
The product shot names the product with its `description` and `must_show`.

## Output

`scenes[]`: `id` = shot id, `type: "character_scene"`, `description`,
`start_seconds` / `end_seconds`, `framing`, `movement`, `character_actions`,
`hero_moment` for the hero/product shot.

`metadata.storyboard` — map by scene id:

```json
{"s03": {"keyframe_path": "assets/images/s03_kf.png",
         "alternates": ["assets/images/s03_kf_b.png"],
         "end_frame_path": null,
         "veo_prompt": "…assembled template…",
         "draft_model": "veo-lite-lp",
         "model_plan": "veo-fast",
         "duration_s": 8,
         "reference_images": ["assets/characters/masha_turnaround.png"]}}
```

`draft_model` is `veo-lite-lp` on Ultra (free drafts) and `veo-fast` on Pro;
`model_plan` is the final-pass model (`veo-fast`, or `veo-quality` for hero /
product shots).

## Review

Every keyframe vs. the bible: face, hair, outfit colors, proportions, product
shape. Reject on any mismatch and regenerate (free). Show the user a contact
sheet of all keyframes in order before the gate.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
