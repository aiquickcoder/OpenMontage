# Add-on: Series episode (cartoon-ad without the ad)

Optional module for the `cartoon-ad` pipeline. It applies only when the run prompt
says "Series episode … apply addons/series/README.md". This happens when the brief
has the `world: <id>` option, which Studio's planner and brief form set. Pipeline
skills stay unchanged; this file adds rules on top of them and, where they
conflict, overrides their **advertising** requirements and their **shot plan**
for this run only.

The episode is original entertainment for a recurring world: a short comedy or
quiet drama for Reels with the same characters every time. **There is no
advertiser, product, packshot, CTA, «Реклама» label or ERID.**

The bar is a film scene, not a slideshow of long static shots. The first
episode («Одна зарплата», 2026-10-14) failed this bar. It had 4 static 8 s
shots, 3 lines crammed into one clip, cuts and fades requested *inside* a Veo
clip, hard cuts only, and subtitles drawn huge from the script. The rules below
exist to prevent that.

## Inputs

- **World bible:** the `world.json` path given in the prompt.
  - Read `style_lock`, `negative_style`, `setting`, `episode_rules`, `duration`,
    `logline` and `audience`.
  - The `refs[]` images sit next to `world.json`. `cover` and `family` show the
    cast together; `style` is the drawing style; `location` is recurring sets.
- **Cast:** the library characters listed in the prompt. Bring them in through
  `addons/character-library/README.md`: copy the sheets and use the strings
  verbatim. Never redesign them.
- **Episode:** the brief text, i.e. the title, logline and synopsis with
  dialogue. Keep the beats and the quoted lines; log any change.

## idea / proposal

- Skip every advertiser, product and brand item. In `brief.metadata`, write
  `"series": {"world_id": …, "episode_id": …}` instead of `advertiser`.
- Review items about the brand, product photos, logo, product screen time,
  packshot, CTA or «Реклама» count as **n/a (series episode)** in
  `autopilot_log.md`. They are not failures.
- The concept is the planned synopsis. Do not invent a different one.
- **Lock `render_runtime: "ffmpeg"`** for these pure-video episodes. The FFmpeg
  compose path now does dissolves and dips (`transition_in`) and true-size
  subtitles. Remotion is not needed, and switching runtimes mid-run is a
  pipeline violation.
- **Credit plan.** List every shot with its model and cost against the ledger.
  - Veo 3.1 Lite costs 10 credits per clip.
  - Omni Flash costs 7 credits per 4 s clip at 720p.
  - The free plan has 50 credits per day. An episode may need more than one day.
    In that case end the day's run with `AUTOPILOT_RESULT: BUDGET`; the planner
    resumes the episode the next day. Never cut coverage to fit one day's
    credits, and never spend credits on tests.

## script: a shot list with coverage

The pipeline's "4–11 sections" is the floor. For a 30 s scene, plan **6–10
shots**. Each shot is one generated clip of **4 or 6 s**; the edit uses
1.5–4 s of it. Use 8 s clips only for one continuous action.

- **Coverage.** Mix shot sizes and include each of these:
  - one establishing or wide shot;
  - medium and medium close-up shots on the speaker for each line;
  - at least one silent **reaction** close-up of the listener;
  - **inserts** of the objects that carry the story (a folder, a phone, a door,
    a hand).

  Never use the same shot size three times in a row.
- **One beat per clip.** Each clip has one action and **one speaker**. Keep each
  speaker's lines to ≤ 6 words in 4 s and ≤ 10 words in 6 s, with ~0.5 s of
  silence at the head and the tail. An exchange of two lines is two clips,
  speaker and then reaction or reverse.
- **Nothing Veo cannot do.** Never ask Veo for a cut, montage, shot-reverse-shot,
  split screen, dissolve or fade *inside* a clip. Those are edit decisions
  (see edit / compose).
- **Screen direction.** Each character keeps the same side of the frame across
  the scene, and eyelines match between reverse shots.
- For each shot, write: size, camera move (start framing → end framing and
  duration), the single action with its timing, the line (or "no dialogue"),
  and the sound.

## character_design

- The bible's `style_lock` is the world's `style_lock`, verbatim.
- `negative_style` is the world's `negative_style`, verbatim. **Ignore the
  Pixar default and its "no 2D" negative.** A 2D, clay or paper world must stay
  2D, clay or paper.
- The `characters` come from the library.
- Add `locations[]` from the world `setting` and its `location` refs.
- There is no `product`.

## scene_plan: keyframes and Veo prompts

- **Keyframes** follow the autopilot keyframe rules (open every image; 2–4
  variants).
  - Draw each one at the shot's **starting framing**, so the camera move has
    somewhere to go.
  - For `image_paths`, pass the turnaround sheet of each character in the shot
    and at most one location ref. Characters who cannot be uploaded (Flow
    rejects children) are drawn from text alone.
  - Store the prompt as `storyboard[*].keyframe_prompt`.
- **Continuity.** For consecutive shots in one setup, pass the previous approved
  keyframe as an extra reference. When a move must continue, use the previous
  clip's last frame (`frame_sampler`) as the next keyframe.
- **Veo prompt: 120–180 words, in this order:**
  1. Shot size and **camera move**, using the primitives in
     `skills/creative/prompting/veo-prompting.md` and
     `skills/creative/video-gen-prompting.md`. Examples: "slow dolly-in from
     medium to close-up over 4 s", "rack focus from the folder to her face",
     "subtle handheld, slight drift right", "slow pan following him to the
     door".
     - At least half of the shots move with motivation: the move follows an
       action or reveals something.
     - Static only for a deliberate still beat, and then write "locked-off
       static camera".
  2. The single action with timing ("at 1 s she looks up; at 2.5 s she…").
  3. The line: `<Name> говорит по-русски: «…»` plus a one-line voice note. For a
     reaction or insert: "no dialogue, characters do not speak".
  4. The sound: room tone and one or two specific effects.
  5. A **short** style tag of ≤ 25 words taken from the world `style_lock`.
     Identity and look come from the keyframe; do not repeat full character
     descriptions. Use the name plus 2–3 identifiers ("Lena, copper curls in a
     bun, teal cardigan").
  6. The negative: the world `negative_style` plus "no subtitles, no captions,
     no on-screen text".

## assets

- Take review treats drift **away from the world's style** as the defect, for
  example a 2D world rendering as glossy 3D. The pipeline's "turns 2D" check
  applies only to 3D worlds.
- After each clip, open 3 frames (`frame_sampler`) and check:
  - lip movement on the speaker only;
  - the camera move actually happened;
  - no morphing.

  A clip that ignored its camera move or garbled its line counts as a failed
  take (credit rules apply).
- **Transcribe every clip** you keep with `transcriber` (`model_size: "small"`,
  `language: "ru"`) and save the word timings next to it. The edit and the
  subtitles depend on them.
- Music follows the autopilot music rule.

## edit / compose

- **Cuts.**
  - Cut on action or on a look. Trim the Veo settle at heads and tails
    (0.3–0.6 s), and never inside a word. Use the transcriber word timings.
  - You may take two shots from one clip, for example a reaction from its head
    and the line from its tail.
- **Transitions.** Set `transition_in` on the incoming cut; `video_compose`
  honours it.
  - `cut` within a scene: the default, and nearly always right.
  - `dissolve` (0.3–0.5 s) only for a time jump or a change of place.
  - `dip_to_black` (0.5 s) for a strong time skip or the final beat.
  - Nothing else.
- **Pacing.** A 30 s scene usually has 8–14 cuts. Hold longer only on the
  emotional beat.
- **Subtitles.**
  - Build the SRT from the **transcribed word timings** mapped onto the final
    timeline, accounting for trims and transition overlaps. Never use the
    script text or estimated timings.
  - Each cue is one phrase: ≤ 2 lines, ≤ 32 characters per line, on screen while
    it is spoken.
  - Set no `font_size`, `background` or box. `video_compose` sizes and places
    them for 9:16 (white, soft outline, above the platform UI). Choose a box
    (`border_style: 3`) only if the world style demands one.
- No ad end card, no logo lock-up, no «Реклама». End on the punchline or on the
  final emotional image. An optional 1–1.5 s series title card may show the
  world `name` in its style.
- **QA before ticking compose.**
  - Open frames at every cut (±0.2 s) and at the middle of every subtitle cue.
  - Check that each subtitle is readable, sits in the lower safe zone and
    covers no faces.
  - Check that the subtitles match the speech and that the characters stay
    on-model across cuts.
  - Any ✘ is fixed before the gate.

## publish

- The caption is a short hook plus 3–6 hashtags about the world and the joke.
- No «Реклама», no advertiser marking, no ERID.

## Logging

- Put `Series episode: <world_id> / <episode_id>` in the first line of
  `autopilot_log.md`.
- In `decision_log`, record every place where this add-on overrode the pipeline:
  advertising, shot plan, runtime.
