# VOXCUT roadmap — from operator interview 2026-07-16

Context: exports go **straight to YouTube** — no outside editor. So every gap
in VOXCUT is visible to the audience. Operator's time per video goes to
(1) writing/recording VO [explicitly out of scope — VO stays theirs],
(2) reviewing/swapping clips, (3) finishing touches done elsewhere or skipped.

## P0 — Publish-ready audio (the loudest gap)

**1. Auto music bed.** "Auto everything": VOXCUT infers the video's mood from
the beats/tone, picks a track from a curated royalty-free pack (tagged by
mood), loops it to length, and auto-ducks it under the voiceover using the
VO's silence map (silences.json already exists per project). Override = swap
track or disable in a Music panel. The reference channel has continuous music
under 100% of runtime; VOXCUT's silence there is the most audible difference.
- Engine: ffmpeg mix stage in render (VO + ducked music), volume automation
  from VAD gaps; project setting `music: {track, volume, enabled}`.
- Licensing: bundle only CC0/royalty-free tracks, fetched on first use.

**2. Sound effects — auto + palette.** Auto placement: whoosh/impact at
high-emphasis beats (>=0.8) and hard cuts into punchline beats; record-scratch
on ironic beats — all deletable. Manual: small SFX palette in the inspector to
click-place on any beat. Same mix engine as music. CC0 pack.

## P1 — Editor power (operator's chosen direction)

**3. Drag-trim clips on the timeline.** Grab a clip's edges to adjust which
seconds of the source play (shifts source.in_s/out_s); drag the boundary
between two events to move the cut point (snaps to word boundaries/silences so
cuts stay on speech rhythm). Today this requires the candidates strip or JSON
surgery; it should be direct manipulation.

## P2 — Review accelerators (2nd-biggest time sink, not chosen as #1)

**4. Per-beat preview.** Click a beat → instantly play just that beat's
segment + VO slice, instead of scrubbing the stitched preview.
**5. Reroll one beat.** One button that re-runs the full tournament (fresh
searches, fresh judge) for a single beat. ("Search again" exists but reuses
your typed query; reroll should re-plan too.)

## P3 — Known selection nits (from tournament-era testing)

**6. Franchise variety guard.** Variety guard blocks repeat *videos* but not
repeat *franchises* (SpongeBob ×4 in one video). Track franchise/show names
per run; penalize 3rd+ use in the judge.
**7. Compilation filter.** "100 POPULAR MEMES" style compilations still slip
the judge occasionally; add an explicit compilation-downrank unless the beat
wants a montage.

## Deprioritized by operator (revisit only on request)
- Script/VO assistance of any kind (punch-up, drafting, in-app recording)
- Intro/outro/branding, thumbnails
- Shorts / 9:16 auto-versions
- Faster generation (unlimited-time tournament was an explicit trade)

## Standing constraints (operator preferences on record)
- No fast/choppy default pacing; density stays a user setting
- No auto motion effects
- Quick cuts only for genuine lists/escalations
- Ask before every git push
