# PRD: Interject — click-to-insert unmuted comedy beat

**Status:** Draft · **Owner:** Nyan · **Date:** 2026-08-05

## 1. Summary

A new timeline tool ("Interject") in the editor. The user clicks a point on the timeline; VOXCUT cuts the voiceover and the video track at exactly that point, inserts a few seconds of brand-new time (a silence in the narration), and auto-fills it with an AI-chosen clip that plays **with its native audio** — a reaction, sound bite, scream, or iconic line that lands as a funny interjection between two narration phrases. One click, auto-insert; swap via the existing reroll flow if the pick isn't funny.

This is the app's first **ripple insert**: unlike `split` and `add_segment`, which preserve total duration, Interject makes the project longer and shifts everything after the cut point.

## 2. Problem / motivation

The current pipeline produces a wall-to-wall narrated edit where every clip is muted under the VO. The strongest comedic device in this genre — the narrator *stopping* so a clip can deliver its own audio punchline — is impossible today:

- Total timeline length is locked to the VO master's length; there is no way to create silence.
- The sourcing judges (`PLAN_SYSTEM` rule 0, `JUDGE_SYSTEM`, `FRAME_SYSTEM`) explicitly reject talking-head/audio-forward footage *because* clips play muted — the exact footage an interjection needs.
- `audio.mode: "keep"` exists and renders correctly, but nothing upstream ever selects for audio quality, so flipping a clip to "keep" in the Inspector yields muted-selection footage with incidental audio.

## 3. Goals

1. One-click insertion of an unmuted, contextually funny clip at any point on the timeline.
2. Gap length is decided by the AI per clip: the gap is sized to the chosen clip's natural moment (clamped), never the other way around.
3. The narration resumes seamlessly after the gap — same VO file, no re-recording, no pitch/tempo artifacts.
4. Full undo, consistent with the existing version-snapshot model.
5. Reroll works on interject events with the same operator-hint flow as normal events.

## 4. Non-goals (v1)

- No candidate picker UI — auto-insert only, reroll to swap (per decision).
- No snapping of the cut point — the VO is cut at the exact click position, even mid-word (per decision; see Risks §12).
- No drag-resize of the inserted gap after the fact.
- No multi-track/overlay lane; the interject event lives on the single existing video track.
- No removal tool beyond what exists — deleting an interject event must also remove its inserted time (see §7.5), but a general "ripple delete arbitrary time" tool is out of scope.
- No captioning/subtitling of the interjected clip's speech.

## 5. UX

### 5.1 Tool

- Add `"interject"` to the `tool` union in `store.ts` (`"select" | "cut" | "add" | "interject"`) and a fourth button in `ToolSwitch` (Editor.tsx). Suggested icon/label: ⚡ "Interject".
- Cursor over the timeline in this mode: crosshair with a visual "insert caret" affordance.

### 5.2 Click flow

1. User clicks at time `t` on the timeline background or on a clip (both are valid; the click x-position → `t = x / PX_PER_S` is what matters).
2. Immediately: a **placeholder event** appears at `t` with a pulsing "finding a bit…" state, sized at a provisional 2.0s. Everything right of `t` visually shifts right by 2.0s. The timeline is interaction-locked for structural ops (split/add/interject/delete) until the job resolves; select/inspect stays live.
3. A new `interject` job runs (see §8). On completion the placeholder is replaced by the real event (thumbnail, actual duration — the timeline re-shifts to the final gap length), and a preview rebuild is triggered automatically.
4. On failure (nothing sourced, all candidates rejected): the insert is **rolled back entirely** — no gap is left behind. Toast: "Couldn't find a good interjection here — try again or reroll with a hint."

### 5.3 After insertion

- The event renders on the timeline with a distinct treatment (e.g. accent border + 🔊 badge) so unmuted interjections are scannable.
- Selecting it opens the existing Inspector: audio mode control (pre-set to `keep`), reroll with hint, delete.
- **Delete** on an interject event removes the event *and* its inserted time (ripple delete of exactly that gap), restoring the original narration flow. Confirm dialog not needed — undo covers it.
- **Reroll** keeps the gap length fixed and trims/pads the replacement clip to fit (avoids cascading re-shifts on every reroll). Hint text is passed through to the planner as today.

### 5.4 Playback

In preview and export, at `t` the narration stops, the interjected clip plays fullscreen with its own audio at full volume (music ducks if a music region overlaps — see §7.6), then narration resumes.

## 6. The core structural operation: ripple insert

New function in `timeline_ops.py`: `insert_time(project_id, at_s, duration_s)` → shifts the world; and its inverse `remove_time(project_id, start_s, duration_s)` for rollback/delete. Everything keyed to the VO clock must move:

| Artifact | Change |
|---|---|
| **VO master** (`Project.voiceover_path`) | Re-render as `concat(vo[0:t], silence(duration), vo[t:])` via ffmpeg. Same codec/loudness as `ingest.normalize` output. Write to a new file; keep the old for undo (§7.4). |
| `Project.duration_s` | `+= duration_s` |
| **EDL events** | Event straddling `t`: split at `t` (reuse `split_event` mechanics — beat split, proportional `_split_source` — but **no word snap**; cut exactly at `t`, allow `MIN_PIECE_S` refusal only for slivers < 0.25s, in which case the cut lands at the event edge instead). All events with `start_s ≥ t`: shift `start_s`/`end_s` by `+duration_s`. Insert the new interject event in array order at the gap. |
| **beats.json** | Straddling beat splits with the event (1 beat : 1 event invariant holds). A new beat is created for the interject event (`text: ""`, `gist` from the LLM's comedic intent, `rhythm: "interject"` or reuse `"escalation"`; see §9). All later beats shift. |
| **Transcript / word timings** | All word timestamps after `t` shift by `+duration_s`. (Word indices are unchanged; only times move.) |
| `silences.json` | Shift entries after `t`; add the new gap as a silence entry (it genuinely is one — future features like snap or music logic should see it). |
| **Music regions** (`Project.settings.music.regions`) | Regions entirely after `t`: shift. Region straddling `t`: extend `end_s` by `+duration_s` (music plays through the interjection, ducked — §7.6). `offset_s` untouched. |
| `highlights.json` | Invalidate (delete) — beat indices and times are stale. UI already handles the empty state. |
| **Rendered segments** | `_mark_dirty` for: the split event, the new event, and — because `adelay`/concat offsets changed — the concat + mux stage. Per-event segment files for merely-shifted events are still valid (they're cut from source assets, not from the timeline), so only concat/mux re-runs for them. |

`remove_time` is the exact inverse and is only ever called with the bounds of a known interject gap (rollback, delete, undo), never arbitrary ranges.

## 7. Backend design

### 7.1 API

- `POST /api/projects/{id}/edl/interject` `{ base_version, at_s, hint? }` → validates version (409 on mismatch, same as `apply_ops`), performs the provisional insert (2.0s placeholder gap + placeholder event with flags `["user_added","interject","sourcing"]`, `audio: {mode:"keep"}`), submits the `interject` job, returns `{edl, new_event_id, job_id}`.
- Interject **delete** rides the existing `delete` op but `apply_ops` detects the `interject` flag and routes through `remove_time`.

### 7.2 New job kind: `interject`

Registered in `jobs/steps/interject.py` via `@register("interject")`. Stages:

1. **Context assembly** — transcript text of ±2 beats around `t`, the words immediately before/after the cut, tone/gist of the surrounding beats, and 2–3 frames sampled from the surrounding events' segments (reuse `moments/frames.py::sample_window_frames`).
2. **Plan (LLM #1)** — new prompt `INTERJECT_PLAN` (see §9): returns `queries` (audio-forward search angles), a one-line `comedic_intent`, and a target duration range `{min_s, max_s}` within the global clamp **1.0–4.0s**.
3. **Search** — existing `provider.search()` (yt-dlp `ytsearch`), 8 results/angle, dedupe, compilation filter. Heuristic rank as today.
4. **Judge (LLM #2)** — new prompt `INTERJECT_JUDGE` (§9): thumbnail-based scoring that *rewards* talking heads, reactions, and clips whose title/metadata suggest the audio is the payload. Reject-all is a valid outcome → rollback path (§5.2.4).
5. **Download** best 2 finalists into the shared library (existing flow, `Asset` rows).
6. **Moment pick** — reuse `run_moment`'s signal fusion but add an **audio-energy signal** (RMS/onset via ffmpeg `astats` over candidate windows: the moment should *contain* the loud/spoken payload, not silence). Frame judge uses `INTERJECT_FRAME` variant (§9). Chosen window length → final `duration_s` (clamped 1.0–4.0).
7. **Commit** — adjust the provisional 2.0s gap to `duration_s` (a second, small `insert_time`/`remove_time` delta on the same version lineage), fill the event (`asset_id`, `source.in_s/out_s`, clear `sourcing` flag), save EDL, submit `assemble`, publish SSE (`preview_updated` + a new `interject_done` event for the UI placeholder swap).
8. **Failure at any stage** — `remove_time` the provisional gap, restore snapshots, publish `interject_failed`.

### 7.3 Reroll integration

`jobs/steps/reroll.py` handles interject events with a flag-aware branch: use the `INTERJECT_*` prompts, keep `duration_s` fixed, trim the new moment to fit (source window may be shorter → `tpad`/hold last frame as the renderer already does; slightly longer → trim tail).

### 7.4 Undo & versioning

Today `save_edl` snapshots only `edl.json`. Interject mutates VO, beats, transcript, silences, music, and `duration_s` — undo must restore all of them atomically:

- Introduce a **project snapshot** for structural ops: on `insert_time`/`remove_time`, snapshot `{beats.json, transcript timing deltas, silences.json, music regions, duration_s, voiceover_path}` alongside the existing `edl.v{n}.json` (e.g. `struct.v{n}.json` + retained old VO file). Keep the same 30-deep retention; garbage-collect superseded VO files with the snapshots.
- VO files are named per version (`voiceover.v{n}.m4a`) rather than overwritten, so undo is a pointer flip, not a re-render.
- `store.ts::undo` needs no UI change; the backend undo endpoint restores the full snapshot when the popped version was a structural op.

### 7.5 QC / other AI steps

- `judge_qc` must not flag interject events for "talking head / audio mismatch" — pass the `interject` flag into the QC context and instruct the judge that these clips are intentionally unmuted punchlines (judge them on *comedic landing*, not on the muted-clip rules).
- `run_highlights` (TikTok scout): interject moments are prime clip material — no change needed beyond the invalidation in §6; the next scout run sees them naturally.

### 7.6 Audio mix

- Interject event: `audio: {mode: "keep"}` → existing `_audio_overlays`/`_mux_final` path plays it at 0dB, `adelay`ed to its (new) `start_s`. The VO under it is genuine silence, so no collision.
- Loudness: normalize the overlay clip toward the VO target (−14 LUFS) with a per-clip `loudnorm` (or measured gain) so interjections don't blast or whisper relative to narration. This is a change to `_mux_final`'s overlay filter for events carrying the `interject` flag (or, better, for all `keep` overlays).
- Music: if a region spans the gap, duck it under the interjection using the existing region gain mechanics (fixed −12dB dip across the gap in v1).

## 8. Frontend design

- `store.ts`: add `interjectAt(t: number, hint?: string)` — optimistic placeholder insert + shift (mirror of backend provisional state), calls the API, listens for `interject_done`/`interject_failed` on the existing SSE channel, then reconciles with the returned EDL. `tool: "interject"` in the union; timeline click handler routes on tool.
- `Timeline.tsx`: placeholder event style (`.evt.sourcing` pulse), 🔊 badge for `audio.mode !== "mute"`, and the shift animation (CSS transition on `left` is sufficient at `PX_PER_S = 60`).
- `Editor.tsx` / `ToolSwitch`: fourth tool button + keyboard shortcut (suggest `I`).
- `Inspector.tsx`: no new controls needed; audio segmented control and reroll already cover it. Show `comedic_intent` as the event's description line.
- `Wave` component: waveform must re-fetch after VO changes (bust cache with `edl_version`, same nonce pattern as `Preview`).

## 9. New LLM surface (OpenAI, existing `structured()` wrapper)

Three new prompt/schema pairs in `brain/` — deliberately separate from the muted-clip prompts, whose rules are inverted here:

| Name | Purpose | Key instructions |
|---|---|---|
| `INTERJECT_PLAN` (`interject_plan` schema) | Search angles + intent + duration range | "The clip's AUDIO is the joke. Favor: famous reaction lines, screams, deadpan one-liners, meme sound moments. The clip interrupts this narration: <context>. Return 2 angles: one that riffs on the literal content, one absurdist contrast." Temp 0.7. |
| `INTERJECT_JUDGE` (`interject_judge` schema) | Score search candidates (thumbnails + metadata) | Inversion of `JUDGE_SYSTEM`: reward talking heads/reactions; reject music-only, ambience, and compilations; reject anything whose payoff is visual-only. Keep the franchise-fatigue guard. Temp 0.2. |
| `INTERJECT_FRAME` (`interject_frame` schema) | Pick the exact window (frames + audio-signal summary) | "Choose the tightest window that contains the complete audio payload — setup optional, punchline mandatory, no dead air on either side. Report `payload_confidence`." |

Cost note: one interject ≈ 3 LLM calls + 1–2 yt-dlp downloads — same order as one beat of the normal pipeline.

## 10. Success metrics

- **Keep rate:** ≥ 60% of interjections survive to export without reroll or delete.
- **Latency:** click → placeholder < 100ms; click → real clip previewable p50 < 45s (dominated by yt-dlp download, same as current sourcing).
- **Integrity:** zero desync — after N interjects + undos, `duration_s` ≡ VO length ≡ last event `end_s`, beats tile exactly (add an invariant check to `timeline_ops` tests).

## 11. Milestones

1. **M1 — Ripple core:** `insert_time`/`remove_time` + structural snapshots + undo; manual test via a temp endpoint inserting silent black. This is the risk concentrator; ship it alone first.
2. **M2 — Tool + placeholder UX:** frontend tool, optimistic shift, rollback on failure (still inserting a dummy clip).
3. **M3 — AI fill:** `interject` job, three prompts, audio-energy signal, loudness normalization.
4. **M4 — Polish:** reroll branch, QC exemption, music ducking, badges, metrics logging.

## 12. Risks & open questions

- **Mid-word VO cuts (accepted risk).** Per decision, the cut lands exactly at the click, which can clip a word and make the narration stutter around the gap. Mitigation available later as a toggle (word-gap snap using existing word timings + `silences.json`) without changing the architecture. The Interject cursor should show the local waveform zoomed, so the user can aim at natural pauses.
- **Transcript timing shift touches ASR artifacts.** Word times live in the transcript store written by `transcribe`; shifting them must not trigger re-transcription. If word times turn out to be derived-on-read anywhere, store a per-project offset table instead of rewriting.
- **`_absorb_gaps` interaction.** The renderer silently stretches neighbors over unsourced gaps. The provisional/placeholder event must be renderable (black or freeze-frame) so `_absorb_gaps` never eats the inserted time before the job lands.
- **Concurrent structural ops.** Two quick clicks race on `base_version`; the second gets a 409. The interaction lock (§5.2.2) makes this unreachable in the UI, but the API must still be safe.
- **YouTube audio rights.** Unmuted third-party audio is a heavier copyright surface than muted visuals. Out of scope to solve here; flag in export UI copy.
- **Open:** should Interject be offered proactively (the scout suggesting "a beat drop would land here")? Deferred; the highlights-scout pattern (frames → beat-indexed proposals) maps directly if wanted.
