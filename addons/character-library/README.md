# Add-on: Character Library (reuse characters across films)

Optional module for the `cartoon-ad` pipeline. Apply it only when the brief
lists library characters: the factory prompt then contains a
`Library characters:` block with one absolute path per character, e.g.

```
- Library characters: in the character_design stage apply addons/character-library/README.md ...
  - kot_barsik: /opt/openmontage/factory_data/characters/kot_barsik/character.json
```

Pipeline skills stay unchanged; this file adds rules on top of them, mostly
for the `character_design` stage.

## What a library character is

`<characters_dir>/<char_id>/character.json` is one `characters[]` entry of
`schemas/artifacts/character_bible.schema.json` (`id`, `name`, `role`,
`description`, `turnaround_path`, `voice`, `prompt_tokens`, optional
`expression_sheet_path`, `flow_media_ids`) plus a `library` block that is
**not** part of the schema:

| `library` field | Meaning |
|---|---|
| `style_lock` | the style the sheets were drawn in (verbatim string) |
| `tags`, `source`, `created_at`, `updated_at`, `archived` | bookkeeping only |

`turnaround_path` and `expression_sheet_path` are relative to the character's
own directory (e.g. `turnaround.png`).

## character_design stage

For each listed character:

1. Read its `character.json`. If the file is missing, `library.archived` is
   true, or `turnaround_path` is empty or points to a missing file, treat the
   character as not available: log it and design that role as usual.
2. Copy the sheets into the project:
   `projects/<id>/assets/characters/<char_id>/turnaround.<ext>` (and
   `expression.<ext>` when present). Never edit the library files.
3. Add the character to `character_bible.json` with these fields **verbatim**
   (no paraphrase, no translation, no "improvements"): `id`, `name`,
   `description`, `prompt_tokens`, `voice.language`, `voice.description`.
   `role` may change to fit this film's story. Set `turnaround_path` (and
   `expression_sheet_path`) to the copied files' paths inside the project.
   Drop the `library` block: the bible must stay schema-valid
   (`additionalProperties: false`).
4. Do **not** regenerate its sheets. The approved library sheet is the
   reference for every shot with this character.
5. `flow_media_ids` are an optimization only: pass them as refs when present,
   but Flow media ids may be invalid on another account or after cleanup. On any
   reference error, upload the copied sheet file and record the new ids in the
   project's bible (not in the library).

### style_lock

The bible's top-level `style_lock` must match the library characters' style.

- One library style (all listed characters share one `library.style_lock`, or
  only one character is listed): use that string **verbatim** as the bible's
  `style_lock`, unless the brief explicitly asks for a different style.
- Different styles among the listed characters: use the hero's style and log
  that the others may look off-style. Do not redraw them.
- If the brief demands a style that differs from a library character's
  `style_lock`, keep the library sheet anyway (consistency across films wins)
  and log the conflict.

### Other characters

Characters the story needs that are not in the library (and the product sheet,
locations) are designed and generated as usual, in the same `style_lock`, so
the cast looks like one film.

## Later stages

`scene_plan`, `assets`: every shot featuring a library character uses its
`prompt_tokens` and (for dialogue) `voice.description` verbatim, and passes
the copied turnaround sheet as a reference, exactly like any other bible
character.

## Autopilot

When running under `addons/autopilot`, add to `projects/<id>/autopilot_log.md`
under the `character_design` stage:

- each library character used: `char_id`, source path, copied sheet path,
  whether `flow_media_ids` were reused or re-uploaded;
- each listed character skipped, with the reason;
- the `style_lock` decision (library style used verbatim or conflict).

Library characters save credits: their sheets cost nothing in this run.
Mention that in the credit line of the stage.
