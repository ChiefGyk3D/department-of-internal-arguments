---
name: "stage-crew"
description: "Live production and accessibility checks: one saved audio preset with alerts under voice, a six-scene set with hotkeys including a redaction scene, an alert diet, WCAG AA contrast 4.5:1 and 3:1 on overlays, a live captions policy that is an owner decision, safe-area crops per destination, a vertical live layout, and separate alert paths per platform. Load when building or auditing a stream scene set, overlays, audio or alerts. Not for leak review (safe-live) or schedule."
updated: "2026-11-07"
---

# Pack: stage crew

Status: written 2026-11-07. Contrast ratios are WCAG 2.2 values and the audio alignment and peak numbers are the OBS documentation values, each to be re-read at its source before use. Every other number (alert length, queue cap, scene count) is an owner-set or pilot default, marked. `safe-live` BLOCKS stand over everything here.

Live assets are live by default: change a copy named as work in progress and let the owner promote it. Never edit a running overlay or alert service in place.

## Rules

1. **One audio preset, saved and loadable.** One mixer and limiter configuration is stored as a file. Alignment: speech averages about -20 dBFS and peaks stay at or below -9 dBFS (OBS audio-mixer documentation; re-read the page and record URL and date). Verify: record 60 s of normal speech in the live chain, measure with `ffmpeg -i rec.wav -af ebur128=peak=true -f null -` and report integrated loudness and true peak; the owner sets the loudness target, this pack does not.
2. **Alerts sit under the voice.** Measure the alert sound and the voice in the same chain; the alert's loudest moment must be at least 6 dB below voice peaks (pilot margin). Verify: both measured with the command above; the difference is in the report.
3. **Six scenes, one collection, hotkeys.** Pilot set: starting, main (screen and camera), screen only, camera only, away, redaction. Shared sources, one overlay frame, one hotkey per scene, and the redaction hotkey is reachable with no other key held. Verify: export the scene collection and count scenes (an OBS-style JSON export, `jq '.sources | map(select(.id=="scene")) | length'`, or the tool's equivalent) equals six; list each hotkey; a duplicate hotkey is a defect. More scenes need a stated reason in the report.
4. **Redaction scene matches `safe-live`.** Static card only, switch in <= 1 s; the `safe-live` rule 4 measurement is repeated after any scene change.
5. **Alert diet.** One alert style; a length cap (pilot 5 s); a queue cap; big treatment only for the largest events (raids, subscriptions, milestones); no alert for the lowest-value event on high-volume days; one mute-all hotkey. Verify: trigger each alert type from the test function, time it from the recording, and report any over the cap. Alert text carries the viewer's name legibly (rule 6).
6. **WCAG AA contrast on every text overlay.** Normal text >= 4.5:1, large text (18 pt, or 14 pt bold) and UI components >= 3:1 (WCAG 2.2 1.4.3 and 1.4.11; re-read at w3.org and record the date). Verify by computing the ratio from the sampled foreground and background of a frame grab (relative luminance formula), report the ratio per overlay, and never write "looks fine". Text over changing video is measured on the worst frame of a 60 s sample.
7. **Live captions policy is an OWNER decision.** The pack does not set it. The report lists the options (none, platform-generated, a captioning service, human captions), the cost and failure mode of each, and records the owner's choice with its date. Until recorded, status is "undecided" and nothing claims the stream is captioned. WCAG 1.2.4 (captions, live, AA) is the reference; whether it applies to a stream is the owner's call, not a legal finding.
8. **Safe-area crops per destination.** A table: destination, aspect ratio, pixel size, safe margin, source URL, date read. Rows come from each platform's live help page; a row with no source or older than the review is unknown. Verify: a frame grab of every scene is overlaid with each destination's safe box and no text or face crosses it; the report lists the grabs.
9. **Vertical live layout.** A vertical live destination gets its own layout (a 9:16 canvas with camera and screen composed for a phone), not a center crop of the horizontal scene. The vertical output obeys `safe-live` rule 1 too and has the redaction card. Multistream tools are checked live for their current capture behavior before use.
10. **Alert paths per platform, separate.** Each destination has its own alert source and a written line saying which service feeds it; a destination fed by a different service than the others is not folded into the main one. For each path: the trigger test result, and any alert that cuts off mid-sound recorded as a known defect with the count (`N of M` tests cut off). The path names come from the overlay facts file. Local alerts and text-to-speech are a later task and are not assumed.
11. **Camera, lights and capture hardware** are described by role in the report (key, fill, prompter, capture input), not by model. Model facts live in the overlay.

## Deliverable

Audio numbers (integrated, true peak, alert-to-voice difference); the scene table with hotkeys and the count; the alert table with measured lengths; the contrast table with ratios; the captions status; the safe-area table with sources and dates; the alert path table with test results; the `safe-live` redaction timing; facts to verify; not verified.

## How to verify (before you hand it back)

- Loudness and peak came from a measured recording, with the command in the report.
- The scene count and hotkey list came from an export, not from memory.
- Every contrast figure is a computed ratio with the sampled colors.
- The captions line says "undecided" or records the owner's dated choice.
- Every safe-area row cites a URL and a date read.
- Nothing was changed on a live overlay; work was on a copy.

## What this pack does not cover

Leak review (`safe-live`), clips (`clip-desk`), schedule and links (`front-door`), aesthetic choices, and the owner's real gear, tools and settings, which live in the overlay.
