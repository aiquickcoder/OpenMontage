# Edit Director - Cartoon Ad Pipeline

## Goal

Produce `edit_decisions`: the cut list, audio plan, subtitles and the end card,
using the approved clips as they are (no regeneration here).

## Cuts

- One cut per shot, in script order. Trim 0.2-0.5 s at heads/tails where Veo
  settles motion or morphs; never trim inside a spoken word — find word
  boundaries from the clip audio (transcribe with `transcriber` if needed).
- Prefer straight cuts on action. Use a short crossfade (≤ 8 frames) only
  between shots in the same location with similar framing. No flashy
  transitions — they cheapen character animation.
- J/L cuts: let ambient audio of the next shot start 3-6 frames early when it
  smooths a location change.
- Hold the packshot ≥ 2.5 s before the end card.

## Audio

- Veo clip audio is the dialogue + SFX track. Keep it as primary.
- Music under everything: −18 to −22 dB under dialogue (duck), back up in
  gaps and on the packshot. Fade out across the end card.
- Target −14 LUFS integrated, true peak ≤ −1 dBTP (applied in compose).

## Subtitles

Russian subtitles from the actual spoken audio (not the script — Veo may vary
wording). Max 2 lines, ≤ 32 characters per line, in the lower safe zone,
above platform UI. If the spoken words differ from the approved script in a
way that changes meaning or a claim, flag it.

## End Card (composited, exact)

3-4 s overlay or final card: real logo file, product name, CTA wording from the
brief, brand colors, and the ad marking «Реклама» + advertiser legal name
(+ ERID when provided) in readable size. Plan it as a HyperFrames / Remotion
overlay — never as generated video.

## Output

`cuts[]` with source path, in/out points; `audio`, `music`, `subtitles`,
`overlays` (end card), and `render_runtime` exactly as locked in the proposal.
