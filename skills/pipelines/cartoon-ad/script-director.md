# Script Director - Cartoon Ad Pipeline

## Goal

Produce `script`: one section per generated shot, with the exact Russian line,
the visible action and the sound, timed to Veo's 4/6/8-second clips.

## Shot Rules

- 4-11 sections; each section = one Veo clip of 4, 6 or 8 s.
- Total 30-90 s; the last section is the packshot / CTA beat (its text is
  composited later — write it in `metadata`, not as something a character
  "shows on a sign").
- **One clear action per shot.** Veo handles one beat per clip well; two beats
  (walk in, then pick up, then turn) degrade quality.
- **Dialogue budget:** Russian speech ≈ 2-2.5 words/s. Max ~15 words in 8 s,
  ~10 in 6 s, ~6 in 4 s — and leave ~0.7 s of silence at the start and end of
  the clip so cuts never clip a word.
- **One speaker per shot** whenever possible. Two speakers in one 8 s clip
  often swap voices or lips; split into two shots instead.
- Write lines natural for spoken Russian: short words, no tongue-twisters,
  no abbreviations, no Latin brand spelling inside speech unless the brand is
  pronounced in Russian (write it phonetically if needed: «Яндекс», «Сбер»).
  Numbers as words.
- Mark stress for ambiguous words with an acute accent if needed (за́мок / замо́к).

## Section Format

Each section's text holds: the line(s) of dialogue with speaker id; in
`metadata` per section:

```json
{
  "shot_id": "s03",
  "duration_s": 8,
  "speaker": "masha",
  "line_ru": "Ну всё, без кофе я сегодня не проснусь…",
  "action": "Masha slumps over the kitchen table, then lifts her head as the kettle clicks",
  "emotion": "sleepy → hopeful",
  "location": "kitchen_morning",
  "product_on_screen": false,
  "sfx": "kettle click, morning birds outside",
  "camera": "medium close-up, slow push-in"
}
```

## Integration Check

- The problem appears in the first 1-2 shots.
- The product enters as the solution by ~40 % of runtime.
- Payoff shot shows the benefit in action.
- Packshot ≤ 5 s. Total product screen time ≤ 25 %.
- Every claim spoken or shown is in the brief and not forbidden.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
