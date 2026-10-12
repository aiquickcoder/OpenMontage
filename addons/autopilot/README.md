# Add-on: Autopilot (unattended cartoon-ad runs)

Optional module for the `cartoon-ad` pipeline, used only by the `factory`
service (`python -m factory worker`), which starts Claude Code headless with
this file and a brief. It is never applied in an interactive session unless
the user asks for it. Pipeline skills stay unchanged; this file adds rules on
top of them.

## Approval policy (pre-authorized by the user)

The owner pre-authorized unattended runs through the factory configuration.
This is the "user explicitly pre-authorizes the full run" case of
`skills/meta/checkpoint-protocol.md` (Step 5.5):

1. Before the first stage, add a `decision_log` entry with
   `category: "approval_policy"`, text
   `"Autopilot: owner pre-authorized gates idea..compose via factory; publish stays human-gated"`,
   and the brief id.
2. For every gated stage **except `publish`**:
   - do the stage's work and its reviewer pass as usual;
   - self-check every `quality_gates` item of that stage in
     `pipeline_defs/cartoon-ad.yaml`, and write the result to
     `projects/<id>/autopilot_log.md` (stage, each gate ✔/✘ with one line of
     evidence, cost so far);
   - if a gate fails, fix it and recheck. Allow at most 2 revision rounds per stage;
     after that, stop and fail (see "Failure");
   - write the checkpoint once with `status="awaiting_human"`, then re-write it with
     `status="completed"`, `human_approved=True`.
3. **`publish` is never auto-approved.** Produce the publish artifacts
   (`publish_log` with per-platform `export_path` and `metadata_used.description`
   / `hashtags`, plus `renders/final.mp4`), write `checkpoint_publish.json` with
   `status="awaiting_human"`, then **stop**. The owner approves the result in
   Telegram, and the factory hands it to the phone farm.

Do not ask questions: there is nobody to answer. Resolve ambiguity with the
brief, the style defaults below and the stage skills, and record the choice
in `autopilot_log.md`.

## Defaults when the brief is silent

- 9:16 vertical, 30–60 s, 3D Pixar-like style, Russian dialogue with native Veo audio.
- Product integration is native to the plot (stage skills already require this).
- **Series episodes** (the prompt says "Series episode … addons/series/README.md",
  i.e. the brief has `world:`) are not ads. The style, cast and runtime come from
  the world, and `addons/series/README.md` replaces the two defaults above and all
  advertiser/product/«Реклама» requirements.
- Comment-bait: apply `addons/comment-bait` when the brief says
  `comment_bait: hero_breaks_wall|meta_joke`. With `none` or no field, skip it.
- Background music: a licensed stock bed from `pixabay_music` (free, no key, Pixabay
  Content License, no attribution needed). The brief's `music:` option overrides this:
  `none` = Veo sound only; any other text is the mood/search query.
  - In the proposal set `music_source.source_type` to the stock option with a mood
    that fits the story (e.g. "playful kitchen ukulele"), not `none`, because the
    music tools are not configured.
  - In the assets stage, search 2–3 queries, prefer an instrumental track at least
    as long as the cut, and save it under `assets/music/` with its title, Pixabay URL
    and license in the asset manifest.
  - In the edit, keep it under the Veo dialogue (ducked, about −18 dB under speech,
    about −10 dB without it), fade in and out, and never let it mask the punchline
    or the CTA.
  - If a project resumed past the proposal has `source_type: "none"` only because
    "music is not configured", fetch the track in the assets stage anyway and note
    the change in `decision_log` (this costs no Flow credits).
  - If Pixabay fails twice, continue without music and say so in `autopilot_log.md`;
    never block a run on music.

## Keyframes and takes: look before you tick

Images are free, so keyframes are where an unattended run must be strict.
`scene-director.md` already says how; autopilot runs must actually do it.

- **Open every image you approve.** Read each keyframe PNG (and the contact
  sheet `scene-director.md` asks for) before ticking a gate. A gate ✔ without
  having looked at the image is a failed gate. In `autopilot_log.md`, give one
  line per keyframe on what you checked.
- **2–4 variants per shot with references**, as `scene-director.md` says. If Nano
  Banana echoes the turnaround back, retry with one turnaround + the location
  only, and say "use the reference only for the character's identity; new pose
  and scene". Do not drop references altogether.
- **Image prompts are not video prompts.**
  - Keep `veo_prompt` and the keyframe prompt separate; store the keyframe prompt
    as `metadata.storyboard[*].keyframe_prompt`.
  - In image prompts use shot size ("wide shot", "medium close-up") but no camera
    movement (push-in, pan, handheld sway). Movement belongs to Veo.
  - Never put film wording in image prompts: "feature film", "film still",
    "cinematic frame", or "9:16 vertical" as text. Nano Banana Pro then draws a
    widescreen still and pads the vertical frame with blur. This was verified
    on 2026-10-12: all four keyframes of the first ad had blurred top and bottom
    bands for this reason. The aspect goes only through `aspect_ratio: 9:16`.
  - End image prompts with "full-bleed vertical composition filling the frame
    edge to edge, no letterbox, no borders".
- **Composition rules for image prompts:**
  - at most 3 identical objects; ring or clutter is shown by one or two hero
    props, not by a counted crowd;
  - several people must differ visibly (height, build, hair, one clothing detail)
    and stand apart. Never "two matching / identical" people: that produces
    merged clones;
  - mouth closed, hands visible or clearly out of frame (start pose).
- **Reject and redraw** (free, up to 3 rounds per shot) any keyframe with:
  - cloned or merged people, or extra limbs;
  - the wrong count of key props, or floating or detached objects;
  - blurred bands or a letterbox (top or bottom fifth noticeably softer than the
    middle);
  - text or logos;
  - a character off-model against the bible.
- **Takes:** after each clip, pull 3 frames with `frame_sampler`, open them, and
  check them the same way. A bad take follows the credit and retry rules below;
  log it under `metadata.rejected_takes` even when you keep it.

## Credits

- Flow tools run through `FLOW_DRIVER=agent`. Every result carries
  `credits_spent` and the ledger summary. Read `ledger.remaining` after each
  generation.
- Generate keyframes (images) first and animate only approved keyframes.
- Draft and test clips use the cheapest available video model for `FLOW_PLAN`
  (`veo-lite-lp` on ultra, otherwise `veo-lite`). Use a higher tier only for hero
  shots, and only if the remaining budget covers all remaining shots at the
  cheapest model.
- If a Flow result has `model_mismatch_suspected: true`, note it in the log;
  don't regenerate just because of it.
- When the budget can't cover the remaining shots
  (`FlowBudgetExceeded` or `ledger.remaining` too low), stop with
  `AUTOPILOT_RESULT: BUDGET` (see below). Never raise the budget yourself.
- A Flow error mentioning `PUBLIC_ERROR_USER_QUOTA_REACHED` (daily generation limit of
  the account) also ends the run with `AUTOPILOT_RESULT: BUDGET` at once: no retries,
  no other model, no "test" generation — failed attempts count against the quota too.
- A Flow result with `pending: true` ("still queued in <project>") is already paid.
  Never generate that shot again: call `flow_veo_video` with the same inputs plus
  `collect_from=<project url>` (0 credits; it waits up to `FLOW_VIDEO_TIMEOUT_SEC`).
  Repeat the collect call while it stays pending; it does not count as a retry.

## Failure and final line

Never retry a paid Flow call blindly. A failure after submission may already
have spent credits. Re-run a failed shot at most once, and only if the error is
not `PUBLIC_ERROR_*`, `unavailable` or a budget error.

The last line of your final message must be exactly one of:

```
AUTOPILOT_RESULT: READY projects/<id>
AUTOPILOT_RESULT: BUDGET projects/<id>
AUTOPILOT_RESULT: FAILED projects/<id> <one-line reason>
```

The factory parses this line. Anything else counts as FAILED.
