# Character Design Director - Cartoon Ad Pipeline

## Goal

Produce `character_bible`: the verbatim strings and reference sheets that every
later image and video prompt is built from. This stage decides consistency.

## 1. Style Lock

Write one paragraph, used **verbatim** at the start of every prompt. Default:

> 3D animated feature film still in the style of a modern Pixar movie: stylized
> characters with large expressive eyes and soft rounded shapes, subsurface
> scattering skin, detailed fabric and hair, warm cinematic lighting with soft
> rim light, shallow depth of field, rich saturated but harmonious colors,
> clean high-detail render, vertical 9:16 composition.

And a `negative_style` line appended to every prompt:

> no text, no subtitles, no captions, no watermarks, no logos except the
> product as described, no extra fingers or limbs, no photorealism, no 2D.

Do not mention real studio trademarks beyond the style reference, and never
copy existing characters.

## 2. Characters

For each character (1-2 ideal):

1. Write `description`: age, body proportions, face shape, eye color, hair,
   outfit with exact colors, one signature prop. Specific beats pretty.
2. Derive `prompt_tokens`: a 20-35 word identity string (e.g. «Masha, an
   8-year-old girl with a round face, big hazel eyes, freckles, two auburn
   pigtails with yellow ribbons, a teal hoodie with a white star»).
3. Generate a **turnaround sheet** with `flow_image`:
   `model: nano-pro`, `aspect_ratio: 16:9`, prompt = style lock +
   prompt_tokens + "character turnaround sheet: front view, three-quarter view,
   side profile, full body, neutral A-pose, neutral expression, plain light
   grey background, even studio lighting" + negative_style.
   Generate 2-4 variants (free), pick the best with the user.
4. Generate an **expression sheet** (happy, sad, surprised, determined,
   talking mid-word) with the approved turnaround in `image_paths`.
5. Write the **voice descriptor** (Russian, verbatim in every speaking shot):
   «детский девчачий голос, около восьми лет, звонкий, быстрый темп, живая
   интонация». Include gender, age, timbre, pace, manner. Veo has no voice id,
   so repeating this exact string is the only consistency handle.

## 3. Product Sheet

From the brief's real product photos (`image_paths`, up to 10 refs on nano-pro):
generate a stylized product sheet consistent with the style lock that keeps
**exact shape, proportions, colors and materials**. Record `must_show`
(e.g. "orange cap", "rounded bottle silhouette") and `must_not`
(e.g. "no invented text on label", "no wrong color"). Labels and small text
will be unreadable or wrong in generation — accept a clean label design and
put the real logo in the end card.

## 4. Locations

1-3 locations; one reference image each (`flow_image`, 9:16, no characters),
with a fixed lighting description. Reuse them as refs in storyboard frames.

## 5. Flow Media Reuse

After approval, record Flow media ids returned by `flow_image` in
`flow_media_ids` so later stages reference uploaded sheets without re-upload.

## Review

- Same character looks identical across all views of the sheet.
- Silhouette readable at phone size; colors distinct from the background.
- Product sheet side by side with the photos: shape and colors match.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
