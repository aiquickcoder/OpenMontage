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

## Craft comes from OpenMontage's skills, not from this file

This add-on only overrides advertising, the world inputs and free-Flow limits. For
craft, **read and follow these OpenMontage skills at their stage**. Their formulas
win over any shorthand below.

| Stage | Read and apply |
|---|---|
| script | `skills/pipelines/cinematic/script-director.md` (beat map: hook → escalation → reveal → landing; `silence_windows`), `skills/creative/storytelling.md` (Anti-Subjective rule; name subject transitions revealing/disappearing/switching; **one camera-intent line per beat**), `skills/creative/cinematic.md` (shot length by genre, breathing rhythm, Murch rule), `skills/pipelines/cartoon-ad/script-director.md` (one action and one speaker per clip, words per second, 0.7 s head/tail silence), `skills/meta/voice-performance-director.md` |
| scene_plan | `skills/pipelines/cinematic/scene-director.md` (**5-aspect checklist** for every shot: Subject, Subject Motion, Scene, Spatial Framing, Camera; hero frames; small transition set; overlays listed separately), `skills/pipelines/cartoon-ad/scene-director.md` (keyframe = first frame, 2–4 variants, contact sheet before the gate), `skills/creative/image-gen-usage.md` |
| Veo prompts | `skills/creative/prompting/veo-prompting.md` (14 components, 100–250 words, labelled fields as in its example), `skills/creative/video-gen-prompting.md` (5-aspect skeleton, camera primitive groups, **identity anchoring: repeat the same 3–6 attributes verbatim in every shot**, replace emotions with their visible cause, one speaker per clip, iteration strategy), `.agents/skills/gemini-omni/SKILL.md` for Omni Flash (single continuous shot, `[0-2s]` timecodes, `<FIRST_FRAME>`, English only) |
| review | `skills/meta/reviewer.md`: run the **slideshow-risk** check on the scene plan (`lib/slideshow_risk.py`; every scene needs `shot_intent`). Use the **sample-first** order from `skills/meta/video-reference-analyst.md` and `voice-performance-director.md`: generate the most performance-critical shot first, review it, lock the formula and voices, then generate the rest. |
| edit / compose | `skills/pipelines/cartoon-ad/edit-director.md`, `skills/creative/video-stitching.md`, `skills/core/subtitle-sync.md` (word-timed cues), `compose-director.md` |

**Verbatim strings.** Write these once in the bible and copy them unchanged into every keyframe and Veo prompt:
- each character's identity line (`Name — 3–6 visual attributes`);
- each character's voice line;
- the location line;
- the lighting line;
- the style line.

Paraphrasing them between shots is the main source of drift.

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
    credits. Do not spend on throwaway tests: sample-first means generating the
    most critical **real** shot first and reviewing it before the others.

## script: a shot list with coverage

**Shot length follows the genre** (`skills/creative/cinematic.md`, *Average Shot Length*). It is not the 1–3 s short-form rule.

| Episode genre | Average shot length | Example for 30 s |
|---|---|---|
| Quiet drama / slice of life | 4–8 s, holds up to 10 s on the emotional beat | 5–7 shots |
| Warm comedy sketch | 3–5 s, quicker on the gag | 6–9 shots |
| Gag / slapstick burst | 2–3 s | 9–12 shots |

- Vary the length like breathing (long → medium → short → long). Never use the same length three times in a row.
- Use a clip whole when the shot is the beat: a line plus its pause, an exit, a phone call. Do not chop a good 8 s take into 2 s pieces.
- Take two cuts from one clip only when it carries two distinct beats.
- OpenMontage's reference short «THE LAST BANANA» used 6 clips of about 10 s in 60 s, with narration, music and word-level captions.

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
- **Veo prompt** uses the labelled 14-component structure of `veo-prompting.md`
  (Subject, Action, Scene, Camera, Lens, Lighting, Mood as a visible cause,
  Style, Audio with the quoted line, Negative), at 100–250 words.
  - Fill the 5-aspect skeleton; nothing is left implicit.
  - Use the verbatim identity, voice, location, lighting and style lines.
  - Put the line in Russian as `Name says in Russian: «…»`. Keep every other
    word in English, because Veo reads Russian stage directions aloud.
  - In a shot with two people, state that only the visible speaker talks and
    the other stays silent with mouth closed.
  - Never request a cut or fade inside a clip.
  - The negative is the world `negative_style` plus "no subtitles, no captions,
    no text overlays, no background music".

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
- **Pacing.** Follow the genre table in *script*: a quiet drama runs about 5–7
  cuts in 30 s, a sketch 6–9. Hold the emotional beat and the silences, and
  never trim a pause just to add a cut.
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
