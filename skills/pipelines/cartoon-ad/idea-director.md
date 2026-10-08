# Idea Director - Cartoon Ad Pipeline

## Goal

Produce `brief`: everything about the advertiser, product and audience that the
story must honor, plus the files the later stages need on disk.

## Intake (ask, do not invent)

Collect from the user, in one compact message:

1. **Brand and product** — name, what it is, the single benefit to show.
2. **Audience** — who watches (age, platform habits), and what they care about.
3. **Key message** — one sentence the viewer should remember.
4. **CTA** — exact wording (e.g. «Попробуй в приложении»), link / promo code if any.
5. **Forbidden claims and mandatory lines** — what legal / brand forbids
   (medical, "the best", price promises, competitors), required disclaimers.
6. **Advertiser legal name** and, if known, ad-marking token (ERID) for the
   «Реклама» label in Russia.
7. **Files** — 3-6 real product photos from different angles on a clean
   background, the logo (SVG or transparent PNG), brand colors / fonts.
8. **Tone** — funny, heartwarming, adventurous; age rating.
9. **Platforms and duration** — Reels / Shorts / TikTok / VK Clips; 30, 45, 60 or 90 s.

If photos or logo are missing, the brief is not ready: the product sheet and
end card depend on them.

## Output Mapping

- `title`, `hook`, `key_points`, `core_message`, `cta`, `tone`, `target_audience`.
- `style`: "Pixar-style 3D animated short" unless the user chose otherwise.
- `target_platform`: the primary vertical platform; `target_duration_seconds`: 30-90.
- `metadata.advertiser`:
  `{ "brand", "product", "legal_name", "erid", "product_photos": [...],
     "logo_path", "brand_colors": [...], "forbidden_claims": [...],
     "mandatory_lines": [...], "aspect_ratio": "9:16" }`.

Copy user files into `projects/<id>/assets/brand/` and reference those paths.

## Quality Bar

A writer who has never seen the product could write a truthful, on-brand
story from this brief alone.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
