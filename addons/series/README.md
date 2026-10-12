# Add-on: Series episode (cartoon-ad without the ad)

Optional module for the `cartoon-ad` pipeline. It applies only when the run prompt
says "Series episode … apply addons/series/README.md". This happens when the brief
has the `world: <id>` option, which Studio's planner and brief form set. Pipeline
skills stay unchanged; this file adds rules on top of them and, where they
conflict, overrides their **advertising** requirements for this run only.

The episode is original entertainment for a recurring world: a family comedy
sketch for Reels with the same characters every time. **There is no advertiser,
product, packshot, CTA, «Реклама» label or ERID.**

## Inputs

- **World bible:** the `world.json` path given in the prompt.
  - Read `style_lock`, `negative_style`, `setting`, `episode_rules`, `duration`, `logline` and `audience`.
  - The `refs[]` images sit next to `world.json`. `cover` and `family` show the cast together; `style` is drawing style; `location` is recurring sets.
- **Cast:** the library characters listed in the prompt. Bring them in through
  `addons/character-library/README.md`: copy the sheets and use the strings
  verbatim. Never redesign them.
- **Episode:** the brief text, i.e. the title, logline and synopsis with
  dialogue. Keep the synopsis beats and the quoted lines unless one breaks a
  rule below; log any change.

## Stage by stage

- **idea / proposal.**
  - Skip every advertiser, product and brand item. In `brief.metadata` write
    `"series": {"world_id": …, "episode_id": …}` instead of `advertiser`.
  - Review items about the brand, product photos, logo, product screen time,
    packshot, CTA or «Реклама» count as **n/a (series episode)** in
    `autopilot_log.md`. They are not failures.
  - Everything else stands: one clear comic beat, a hook in the first 2 s,
    runtime from `duration`, and the credit estimate.
  - The concept comes from the planned synopsis. Do not invent a different one.
- **script.**
  - 3–4 shots that follow the synopsis beats. The punchline lands in the last
    shot and no ad beat follows it.
  - Russian dialogue is native Veo audio, as usual.
- **character_design.**
  - The bible's `style_lock` is the world's `style_lock`, verbatim.
  - `negative_style` is the world's `negative_style`, verbatim. **Ignore the
    Pixar default and its "no 2D" negative.** A 2D, clay or paper world must stay
    2D, clay or paper.
  - The `characters` come from the library (see Inputs).
  - Add `locations[]` from the world `setting` and its `location` refs.
  - There is no `product`.
- **scene_plan.**
  - Keyframes follow the autopilot keyframe rules.
  - For `image_paths`, pass the cast's turnaround sheets plus one world `family`
    or `location` ref, 2–5 images in total.
  - The keyframe prompt starts with the world `style_lock`.
- **assets.**
  - Take review treats drift **away from the world's style** as the defect, for
    example a 2D world rendering as glossy 3D. The pipeline's "turns 2D" check
    applies only to 3D worlds.
  - Music follows the autopilot music rule.
- **edit / compose.**
  - No ad end card, no logo lock-up, no «Реклама».
  - End on the punchline. An optional 1–1.5 s series title card may show the
    world `name` in its style, with no CTA.
  - Subtitles as usual.
- **publish.**
  - The caption is a short hook plus 3–6 hashtags about the world and the joke.
  - No «Реклама», no advertiser marking, no ERID.

## Logging

- Put `Series episode: <world_id> / <episode_id>` in the first line of
  `autopilot_log.md`.
- In `decision_log`, record every place where this add-on overrode an advertising
  requirement of the pipeline.
