# Add-on: Ad Insert (advertiser campaigns from the Studio)

Optional module for the `cartoon-ad` pipeline. It is **not** loaded by the
pipeline automatically: apply it only when the prompt says so. The factory
adds that line for briefs created from a Studio campaign (or with an
advertiser + insert), together with:

- the path to `advertiser.json` (absolute, written by the Studio right before
  the run);
- the **rendered insert text** — the owner's instructions for how the product
  enters this film;
- the insert's `placement` and `max screen share`.

The pipeline skills stay unchanged; this file adds instructions on top of
them, per stage. Where this file and a stage skill disagree on quality, the
stage skill wins: **an insert never overrides a pipeline quality gate,
reviewer pass or budget rule.** It only decides *where and how much* the
product appears.

## advertiser.json

```jsonc
{
  "brand": "Ромашка",
  "product": "пицца «Маргарита»",
  "legal_name": "ООО «Ромашка»",
  "inn": "7700000000",
  "erid": "2Vtzq…",              // may be "" — see "ERID pending"
  "erid_not_required": false,
  "cta": "Закажи сейчас",
  "link": "https://…",
  "promo_code": "MULT10",
  "mandatory_lines": ["…"],
  "forbidden_claims": ["…"],
  "logo_path": "/abs/path/logo.png",       // may be null
  "product_photos": ["/abs/path/products/…jpg"],
  "marking": "Реклама. ООО «Ромашка», ИНН 7700000000. erid: 2Vtzq…",
  "campaign_id": "cmp_…",
  "notes": "…"
}
```

Treat it as the advertiser's answers to the idea-director intake. Do not ask
the intake questions again and **never write placeholders** («[бренд]»,
«TBD», «Lorem») into any artifact: every field either comes from this file
or stays empty.

## Placements

| `placement` | What it means |
|---|---|
| `plot` | The product drives the story: the problem the hero has is solved by it. |
| `hero_uses_product` | The hero uses the product naturally inside a scene; the story is about something else. |
| `packshot` | The story stays brand-free; the product appears only in a final packshot + end card. |
| `caption_comment` | Nothing on screen except the end-card marking; the product lives in the caption / pinned comment. |

**Max screen share** is the share of the film's runtime where the product or
logo is the visual subject (product in focus, logo readable, packshot, end
card). Count it in seconds in `scene_plan` and keep it ≤ the limit; the end
card counts. For `caption_comment` only the end card counts.

## Where it plugs in (per stage)

**idea** — copy `logo_path` and `product_photos` into
`projects/<id>/assets/brand/` and fill `brief.metadata.advertiser` from
advertiser.json: `brand`, `product`, `legal_name`, `erid`, `product_photos`
(project paths), `logo_path` (project path), `forbidden_claims`,
`mandatory_lines`, plus `inn`, `cta`, `link`, `promo_code`, `placement`,
`max_screen_share`, `campaign_id`. Put `cta` into `brief.cta` verbatim. Also
copy advertiser.json itself to `projects/<id>/assets/brand/advertiser.json`
(later stages read the project copy). If photos are missing, the product sheet
is built from the brand name and CTA only — note it in the log; never invent
packaging details.

**proposal** — the concept must realize the placement from the insert text.
State in one line how the product enters the story and the planned screen
time in seconds against the limit.

**script** — every `mandatory_lines` item is said or shown once; none of the
`forbidden_claims` appears in dialogue, on-screen text or captions (also not
paraphrased: "лучший", "№1", medical or price promises the advertiser did not
give). The product name is spelled exactly as `product` / `brand`.

**scene_plan** — mark product shots (`product_visible: true` in the shot
notes) and sum their duration; the total, with the end card, must stay within
`max_screen_share`. Pass product photos as references for every product shot.

**assets** — the logo is **never generated**: Veo / image models only produce
the product object; readable logos and text are composited later.

**edit / compose** — end card (2–3 s, safe zone):

- the real logo file from `assets/brand/` and the exact `cta` text (plus
  `promo_code` / `link` when present);
- the marking line from `marking` in readable size: «Реклама» + legal name
  (+ ИНН) + `erid: …` when ERID is present;
- composite logo and text as overlays (hyperframes / Remotion), not baked into
  generated frames.

For `caption_comment` the end card shows only the marking line.

**publish** — in every `publish_log` entry, `metadata_used.description`
contains the CTA and ends with the `marking` line on its own line (the Studio
also appends it at staging and skips duplicates); `link` / `promo_code` go
into the caption or pinned comment. Record `campaign_id` and `erid` in the
entry's `metadata_used` (`metadata_used.campaign_id`, `metadata_used.erid`) —
entries allow no other extra keys, so never add an `entry.metadata` field.

## ERID pending

When `erid` is empty and `erid_not_required` is false, the creative is not
registered yet. Keep the «Реклама» + legal name marking, leave `erid` empty
(no placeholder), and write «ERID pending» in the publish summary. The Studio
refuses to approve the release until the owner fills the ERID in the campaign;
on approval it rewrites the caption marking with the ERID. The end card is
burned into `final.mp4` at render time and keeps the ERID-less marking — if the
ERID must appear in the frame, the owner re-runs the brief after filling it.
Publishing stays human-gated.

## Interaction with comment-bait

A campaign film carries the advertiser's CTA: use comment-bait mode `none`
(see `addons/comment-bait/README.md`, "Brand films") unless the insert text
explicitly asks for a bait ending.
