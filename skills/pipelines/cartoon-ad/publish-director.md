# Publish Director - Cartoon Ad Pipeline

## Goal

Produce `publish_log`: platform-ready exports with ad marking and metadata.

## Exports

- Master: 1080x1920 H.264 high, ~12-16 Mbps, AAC 192 kbps, `faststart`.
- Platform variants only if they differ (length caps, safe zones): Reels,
  YouTube Shorts, TikTok, VK Clips. Label each file by platform.
- Cover frame: pick a frame with the hero's expressive face and the product
  readable; export as JPG 1080x1920.

## Ad Marking (Russia)

Advertising distributed in Russia must be marked: the word «Реклама», the
advertiser's name / legal name, and the ERID token when the advertiser has
registered the creative. Confirm with the user that:

- «Реклама» and advertiser info are present in the video (end card) and in the
  post caption.
- ERID was provided by the advertiser (or explicitly not required).

This is a reminder, not legal advice — the advertiser is responsible for
registration.

## Metadata

Title, caption (hook + CTA + marking), hashtags per platform, link / promo
code from the brief. Record everything in `publish_log` with file paths.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
