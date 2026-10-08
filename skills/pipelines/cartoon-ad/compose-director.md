# Compose Director - Cartoon Ad Pipeline

## Goal

Render the final 1080x1920 film and produce `render_report` + `final_review`.

## Steps

1. **Upscale** (optional, recommended): Flow clips are 720p. Run
   `flow_video_upscale` (`scale: 1080p`) per approved clip using its
   `media_id`. If upscale is unavailable on the account, upscale in render
   (Lanczos) and note it in the report.
2. **Normalize** every clip to 1080x1920, 24 or 30 fps (pick the clips'
   native rate, do not mix), yuv420p, AAC 48 kHz.
3. **Assemble** per `edit_decisions` with the runtime locked in the proposal
   (`video_compose` → Remotion or ffmpeg; end card via `hyperframes_compose`).
4. **Mix** with `audio_mixer`: dialogue clip audio primary, music ducked,
   loudness −14 LUFS integrated, true peak ≤ −1 dBTP.
5. **Subtitles** burned in (vertical social) with a clean sans font, white
   with a soft dark outline/shadow, lower safe zone.
6. **End card** composited with the real logo and exact CTA text, «Реклама»
   label visible.

## QA (final_review)

- ffprobe: 1080x1920, expected duration ±0.5 s, audio stream present.
- Sample frames at every cut ±3 frames: no black flashes, frozen frames,
  color jumps, morph artifacts.
- Listen end to end: no clipped words, music never masks speech, consistent
  character voices, correct Russian.
- Logo / CTA pixel-exact; subtitles readable on a phone at arm's length.
- Product appears truthfully and within ≤ 25 % screen time.
- `render_runtime` matches the proposal.
