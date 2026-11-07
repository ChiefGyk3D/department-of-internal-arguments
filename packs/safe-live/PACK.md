---
name: "safe-live"
description: "Leak prevention for live and recorded security content, with release-blocking authority: window-only or dedicated capture, stream delay sized to the takedown time, notification shutdown, a redaction scene, a pre-live checklist, frame-level scrub of VODs, clips and thumbnails, a lab-only scope line, a sponsor and affiliate disclosure kit always verified against the live platform or regulator text, and incident response (take down, rotate, notify). Load for any live stream, recording, clip, thumbnail or upload that shows a screen. Not for writing the content."
updated: "2026-11-07"
---

# Pack: safe live

Status: written 2026-11-07. Numbers marked owner-set are the owner's bar; numbers marked pilot are unmeasured defaults to be replaced by a measurement. Platform and regulator rules are never stated here: they are fetched (rule 11).

## Authority

This pack can BLOCK any release: a stream going live, a VOD, a clip, a thumbnail, an upload or a post. A BLOCK from this pack cannot be overridden by `media-dispatch`, `clip-desk`, `stage-crew`, `front-door`, a schedule, a deadline or a lead. Only the owner can lift a BLOCK, in the owner's own message, naming the item. Every other pack reports a safe-live BLOCK as open and stops.

## Rules

1. **Capture only what is meant to be shown.** Use window-only capture or a dedicated capture display; never capture a whole desktop that also holds mail, chat, a password manager or a terminal with history. Verify: open a canary window whose title and body read `CANARY-N0TST` on a display or window that is not captured, grab one frame of the program output, and confirm the canary is absent (`tesseract frame.png - | grep -c CANARY` prints `0` if OCR is installed, else read the frame by eye and say so).
2. **Stream delay covers the takedown time.** The owner sets the delay in seconds. It must be at least the worst of three stopwatch trials of "see a leak, press the redaction hotkey" plus 5 s (pilot margin). Verify: put a running clock in view, compare it with the viewer-side playback, and record the measured delay; it must be within 2 s of the setting and at least the takedown time.
3. **Notifications are off on the capture host before going live.** Do-not-disturb on, mail, chat and messaging clients closed, password manager locked. Verify: send a test notification to the capture host and confirm it neither appears in the program output nor plays a sound; record the command or setting used.
4. **One redaction scene.** A scene that shows only a static card, reachable by one hotkey, that switches in at most 1 s. Verify: with the canary window open, press the hotkey, grab a frame, confirm the canary is absent and the card is present; time the hotkey with a stopwatch, 3 trials, worst case <= 1 s.
5. **Pre-live checklist, dated, pass or fail per line.** Capture scope (rule 1), delay (2), notifications (3), redaction scene (4), secret scan of overlays and source files (rule 6 patterns), scope line loaded (rule 8), disclosure state (rule 9), audio check owned by `stage-crew`. Save it as a file with the date and time. A line that is not pass, or a missing checklist, is a BLOCK.
6. **Scrub every recording before it leaves the machine.** For a VOD, clip or thumbnail: sample frames at one per 5 s (`ffmpeg -i in.mp4 -vf fps=1/5 frames/f_%05d.png`), run OCR over them (`tesseract` if installed, else a human reads a contact sheet and the report says so), and grep the OCR text and the caption or transcript for secret shapes: `grep -HnEio '(token|secret|passw[a-z]*|api[_-]?key)[[:space:]]*[:=]|BEGIN [A-Z ]*PRIVATE KEY|[0-9]{1,3}(\.[0-9]{1,3}){3}'`, plus any owner list of names that must not appear. Report location and kind, never the matched value. Strip file metadata with `exiftool -all= -overwrite_original <file>` (no `_original` backup is left) and confirm with `exiftool <file>` that no GPS or device-serial tag remains. Verify: the number of frames sampled equals `floor(duration_seconds / 5)` within 1, and the report states it.
7. **Thumbnails and titles are scrubbed too.** A thumbnail is read at full size for addresses, tabs, names and prompts. A title or thumbnail that frames a real named target as an attack target is a BLOCK.
8. **Lab-only scope line.** Every demo opens with a spoken line and an on-screen card stating: lab, owned or authorized targets only, no live third-party systems. Each demonstrated technique names its target. Verify: the first 60 s of the transcript contains the scope line (`grep -ci` for the owner's fixed wording, from the overlay, returns >= 1) and the card appears in a sampled frame.
9. **Disclosure kit, filled from the live rule.** Kit parts: one plain-words disclosure sentence per platform, an on-screen text, a spoken form, a repeat for late joiners on a live stream, and the platform's own branded-content or paid-promotion setting. **The wording and placement rule for each part is ALWAYS verified against the live platform or regulator text, never taken from memory or from this pack:** fetch the current page, record its URL and the date read, compare. If it cannot be fetched, the item is a BLOCK and is listed under facts to verify. Whether a sponsor or affiliate relationship exists comes from the owner, never assumed. This is not legal advice.
10. **Capture credentials by name, never by value.** If a rule-6 hit or an incident exposes a credential, record its name and location only. Rotation is handed to the human with the exact steps; an agent does not rotate.
11. **No rule here is a platform rule.** Platform and regulator rules (disclosure, content policy, loudness) are fetched per use, with URL and date. A remembered rule gives no clearance.

## Incident response

If something sensitive was shown or published:
1. **Take down, first.** Switch to the redaction scene, then end the stream; set the VOD private or delete it; delete or unlist clips and posts; replace the thumbnail. Log each action with its time (UTC). Time from first sight to takedown is recorded.
2. **Rotate.** List every credential, address or identifier that appeared, by name. The human rotates or changes each; the agent hands over the steps and does not touch secrets.
3. **Notify.** The owner at once; anyone whose data appeared; a platform support channel if a platform copy cannot be removed. Mirrors and third-party clips are listed and requested for takedown.
4. **Record.** A short note: what the lab was, what appeared, how long, what was rotated, what changes the checklist. Keep it for an appeal; no secret value in it.

## Deliverable

PASS or BLOCK per item, each BLOCK with the rule number and the evidence. Then: the checklist file path, the measured delay, takedown time and redaction time, frames sampled and tool used, scan results by location and kind, the disclosure sources (URL, date read), facts to verify, and not verified.

## How to verify (before you hand it back)

- The canary test ran and its result (OCR count or eye check) is in the report.
- The delay, takedown and redaction numbers are measured values with the method, not copied from this pack.
- The frame count equals `floor(duration / 5)` within 1, and the report names the OCR tool or says a human read the sheet.
- No report line contains a secret value; hits are location and kind.
- Every disclosure line cites a page fetched in this session with its date.
- Any BLOCK is stated as a BLOCK, and no other pack's output lifts it.

## What this pack does not cover

Legal advice, the content itself, platform account security, and the owner's real schedule, gear and platform list, which live in the private overlay.
