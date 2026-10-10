# Add-on: Autopilot (unattended cartoon-ad runs)

Optional module for the `cartoon-ad` pipeline, used only by the `factory`
service (`python -m factory worker`), which starts Claude Code headless with
this file and a brief. It is never applied in an interactive session unless
the user asks for it. Pipeline skills stay unchanged; this file adds rules on
top of them.

## Approval policy (pre-authorized by the user)

The owner pre-authorized unattended runs through the factory configuration.
This is the "user explicitly pre-authorizes the full run" case of
`skills/meta/checkpoint-protocol.md` (Step 5.5):

1. Before the first stage, add a `decision_log` entry with
   `category: "approval_policy"`, text
   `"Autopilot: owner pre-authorized gates idea..compose via factory; publish stays human-gated"`,
   and the brief id.
2. For every gated stage **except `publish`**:
   - do the stage's work and its reviewer pass as usual;
   - self-check every `quality_gates` item of that stage in
     `pipeline_defs/cartoon-ad.yaml`, and write the result to
     `projects/<id>/autopilot_log.md` (stage, each gate ✔/✘ with one line of
     evidence, cost so far);
   - if a gate fails, fix it and recheck. Allow at most 2 revision rounds per stage;
     after that, stop and fail (see "Failure");
   - write the checkpoint once with `status="awaiting_human"`, then re-write it with
     `status="completed"`, `human_approved=True`.
3. **`publish` is never auto-approved.** Produce the publish artifacts
   (`publish_log` with per-platform `export_path` and `metadata_used.description`
   / `hashtags`, plus `renders/final.mp4`), write `checkpoint_publish.json` with
   `status="awaiting_human"`, then **stop**. The owner approves the result in
   Telegram, and the factory hands it to the phone farm.

Do not ask questions: there is nobody to answer. Resolve ambiguity with the
brief, the style defaults below and the stage skills, and record the choice
in `autopilot_log.md`.

## Defaults when the brief is silent

- 9:16 vertical, 30–60 s, 3D Pixar-like style, Russian dialogue with native Veo audio.
- Product integration is native to the plot (stage skills already require this).
- Comment-bait: apply `addons/comment-bait` when the brief says
  `comment_bait: hero_breaks_wall|meta_joke`. With `none` or no field, skip it.

## Credits

- Flow tools run through `FLOW_DRIVER=agent`. Every result carries
  `credits_spent` and the ledger summary. Read `ledger.remaining` after each
  generation.
- Generate keyframes (images) first and animate only approved keyframes.
- Draft and test clips use the cheapest available video model for `FLOW_PLAN`
  (`veo-lite-lp` on ultra, otherwise `veo-lite`). Use a higher tier only for hero
  shots, and only if the remaining budget covers all remaining shots at the
  cheapest model.
- If a Flow result has `model_mismatch_suspected: true`, note it in the log;
  don't regenerate just because of it.
- When the budget can't cover the remaining shots
  (`FlowBudgetExceeded` or `ledger.remaining` too low), stop with
  `AUTOPILOT_RESULT: BUDGET` (see below). Never raise the budget yourself.

## Failure and final line

Never retry a paid Flow call blindly. A failure after submission may already
have spent credits. Re-run a failed shot at most once, and only if the error is
not `PUBLIC_ERROR_*`, `unavailable` or a budget error.

The last line of your final message must be exactly one of:

```
AUTOPILOT_RESULT: READY projects/<id>
AUTOPILOT_RESULT: BUDGET projects/<id>
AUTOPILOT_RESULT: FAILED projects/<id> <one-line reason>
```

The factory parses this line. Anything else counts as FAILED.
