---
name: "clip-desk"
description: "Turn recordings into about three finished vertical clips a week plus YouTube packaging: whole-recording search with broad then focused passes, standalone hook-context-payoff, 60 to 120 s targets and up to 180 s for technical stories, never stretching a weak clip, dedupe, caption and technical-name QA, vertical framing check, thumbnail and title template, per-platform export presets. Load when cutting clips, Shorts or Reels from a recording or packaging a long video. Not for leak review (use safe-live) or the owner's voice."
updated: "2026-11-07"
---

# Pack: clip desk

Status: written 2026-11-07. Lengths and the weekly count are owner-set. The clip-quality rules are constraints, not findings, and no item has a measured effect on views: treat them as pilot until the owner's own analytics show otherwise. `safe-live` runs on every clip before release and its BLOCK stands.

## Rules

1. **Search the whole recording, not the highlights.** Pass 1 (broad): transcribe the full recording and list every candidate moment with start and end time; verify the transcript duration equals the recording duration within 5 s (`ffprobe -v error -show_entries format=duration -of csv=p=0 <file>`). Pass 2 (focused): re-read only the shortlisted spans and cut to the exact in and out points. The report states the recording length, the number of candidates from pass 1 and the number kept.
2. **Each clip stands alone: hook, context, payoff.** The first 3 s state the hook; context fits inside the clip; the payoff lands before the end. A reviewer who has not seen the recording must be able to say what the clip is about from the clip alone. Verify: the report gives one line each for hook, context and payoff with their timestamps; a clip missing one is not finished.
3. **Length: target 60 to 120 s; up to 180 s for a technical story** (owner-set). Check with `ffprobe` on the exported file. A clip outside the range is flagged, not trimmed by guess. Check each platform's current maximum against its live help page before export (URL and date in the report); this pack states none.
4. **Never stretch a weak clip.** If a candidate has no payoff, drop it. The weekly target is about 3 finished clips; fewer is a valid week and the report says "N of 3 met the bar". Padding, slow motion or looping to reach a length is a defect.
5. **Dedupe.** Keep a ledger file (recording, start, end, hook line, platform, publish date). A new clip whose span overlaps a ledgered span by more than 50% of the shorter clip, or whose hook line repeats one, is dropped or merged. Verify: the ledger was read before cutting and the report names any overlap found.
6. **Caption QA.** Every clip has captions, burned in or as a sidecar file. A grep for the technical-name list only finds spellings already present, so it is a prefilter and never the check. The check compares the captions against the transcript and the owner's technical-name list (tool names, CVE identifiers, protocols, acronyms; one per line, in the overlay) and reports missing and mismatched terms (a misspelling such as `kubctl` for `kubectl`, or an omitted term):

```bash
# args: terms.txt transcript.txt captions.txt ; prints terms the transcript has that the captions lack
grep -oiwFf "$1" "$2" | tr 'A-Z' 'a-z' | sort -u > /tmp/dia_t.$$
grep -oiwFf "$1" "$3" | tr 'A-Z' 'a-z' | sort -u > /tmp/dia_c.$$
comm -23 /tmp/dia_t.$$ /tmp/dia_c.$$; rm -f /tmp/dia_t.$$ /tmp/dia_c.$$
```

Each printed term is a missing or mismatched caption term: find it in the captions by eye and correct it. Also list tokens that appear in the captions but not the transcript (`tr -cs 'A-Za-z0-9-' '\n' < captions | sort -u` against the same for the transcript, `comm -13`) and read each against the audio. Read every line containing a digit, a hyphen or an uppercase run against the audio. Report each correction. A caption of more than 2 lines on screen, or outside the safe area, is flagged.
7. **Vertical framing check.** A hard center crop of a 16:9 recording is not accepted without a check. Build a vertical layout (face, screen or both, stacked or cropped on purpose) and verify with a frame grab (`ffmpeg -ss <t> -i clip.mp4 -frames:v 1 f.png`) that the subject, the caption area and any on-screen text are inside the destination safe area from `stage-crew`. Output is 9:16 (1080 x 1920 pixels is the planning size; verify the destination's current spec live).
8. **Thumbnail and title template (YouTube packaging).** One subject, three words or fewer of text, contrast checked by ratio (4.5:1 body text, 3:1 large text; compute the ratio, do not eyeball), one face or mascot position per series. A title leads with the concrete result and names no real third-party target as something to attack. Verify: the contrast ratio is in the report; `safe-live` has cleared the thumbnail. If the platform offers thumbnail or title testing, check live that it exists before relying on it.
9. **Per-platform export presets.** One preset per destination: container, resolution, frame rate, bitrate cap, loudness target and max duration. The values come from each platform's live help page with URL and date read, stored in a presets file with those two columns; a row with no source or older than the review is unknown and must be re-fetched. Verify with `ffprobe` that each export matches its preset row.
10. **Rights.** Guest footage, licensed music and third-party clips need recorded permission or a license in the provenance manifest (`asset-production`); a clip with an unrecorded source is held.
11. **Local-first drafting.** Drafting hook candidates, caption candidates and show notes goes to the local model step in the brief. Code does counting (durations, overlaps, caption lines); the model writes prose only, and its output is checked against the transcript. Public copy in the owner's voice goes to the private voice skill; nothing from it is restated here.

## Deliverable

Per clip: recording, in and out, duration, hook, context, payoff, ledger check, caption corrections, framing frame grab, thumbnail contrast ratio, export file per preset with its `ffprobe` result, and the `safe-live` verdict. Then the week's count against the target, facts to verify and not verified.

## How to verify (before you hand it back)

- The recording duration, pass-1 candidate count and kept count are stated and the transcript duration was checked.
- Every clip's duration comes from `ffprobe`, not from the edit list.
- The ledger was read and updated; overlaps are reported.
- Caption corrections cite the technical-name list and transcript files used and list the missing and mismatched terms found.
- Each export was probed against its preset and the preset source URLs have dates.
- No clip is released without a `safe-live` PASS.

## What this pack does not cover

Leak review (`safe-live`), live production and accessibility policy (`stage-crew`), schedule and posting (`front-door`), the owner's voice, and any claim that a clip will perform.
