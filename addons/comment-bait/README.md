# Add-on: Comment-Bait Ending

Optional module for the `cartoon-ad` pipeline. It is **not** loaded by the
pipeline automatically: apply it only when the user asks for it for a given
film ("добавь байт", "с концовкой МУЛЬТ" and the like). The pipeline skills
stay unchanged; this file adds instructions on top of them, per stage.

Goal: the film ends with a short beat that turns the viewer's "how was this
made?" into a comment with a keyword. The reply to that comment is a guide
on making such cartoons for free.

## Modes

| Mode | When | What happens |
|---|---|---|
| `hero_breaks_wall` | **default** | After the punchline the hero notices the viewer, turns to camera and asks them to comment the keyword. No second character. |
| `meta_joke` | about every 3rd–4th film, only if it fits the story | The hero suspects they were drawn by AI: «Погоди… меня что, нейросеть нарисовала?» Silence, then the end card answers. |
| `none` | brand films whose CTA belongs to the advertiser | Bait only in the caption and pinned comment; nothing on screen. |

## Rules

- The bait comes **after** the payoff, never before. It is its own shot of
  4–6 s with ≤ 10 words, plus a 2–3 s end card.
- Keyword: one short Russian word that is easy to say and type (default
  **МУЛЬТ**). Keep it the same across a series.
- **«Бесплатно» is intentional** — keep it in the line or on the end card.
- Change the wording every film. The hero's manner follows the story (smug,
  sleepy, conspiratorial, out of breath…). Never repeat a line used in an
  earlier film of the series; keep them in `used_lines`.

## Where it plugs in (per stage)

**script** — add one extra section (the bait shot) and record the choice in
`script.metadata.ending`:

```json
{
  "mode": "hero_breaks_wall",
  "keyword": "МУЛЬТ",
  "shot_id": "s05",
  "line_ru": "Чего смотришь? Тоже так хочешь? Пиши «мульт» — расскажу, как бесплатно!",
  "end_card_text": "Пиши МУЛЬТ 👇",
  "caption": "…",
  "pinned_comment": "…",
  "auto_reply": "…",
  "used_lines": []
}
```

The bait shot follows the normal dialogue budget (~2–2.5 words/s, 0.7 s of
silence at both ends) and uses the hero's voice descriptor verbatim.

**scene_plan** — keyframe for the bait shot:

- `hero_breaks_wall`: the hero right after the punchline, already turned to
  the camera, direct eye contact, medium close-up, mouth closed. Same location
  and light as the previous shot; pass that shot's keyframe as a reference.
  Veo action: "turns fully to the camera, raises an eyebrow, leans in toward
  the viewer and speaks directly into the lens".
- `meta_joke`: the hero freezes, looks at their own paws or hands, then up at
  the camera with suspicion. No text inside the generated frame.

**assets** — generate the bait shot like any other shot (draft, then final).
In take review, also check that the keyword is pronounced clearly.

**compose** — end card from `end_card_text`, 2–3 s after the bait shot:

- large keyword, one line, centered in the safe zone;
- a ↓ arrow toward the comments;
- pop-in or bounce animation (hyperframes).

The subtitle of the bait line writes the keyword in CAPS. For `meta_joke` the
end card reads «Да. Бесплатно. Как — пиши МУЛЬТ 👇».

**publish** — comment-bait kit for each platform:

| Platform | How the guide gets delivered |
|---|---|
| Instagram | ManyChat: a keyword comment triggers a DM |
| Telegram / VK | bot, or a pinned comment with the link |
| YouTube Shorts | no DMs: pinned comment with the link |
| TikTok | pinned comment |

Texts to write:

- `caption`: a hook plus «Пиши МУЛЬТ — пришлю гайд, как делать такие мультики бесплатно»;
- `pinned_comment`: the same promise, shorter;
- `auto_reply`: the guide link as `{GUIDE_URL}` — the user fills it in.

## Brand films

If the film carries an advertiser's CTA, use `none` or put the bait in a
separate film. Two calls to action in one film compete with each other.
