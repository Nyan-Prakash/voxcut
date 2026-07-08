# VOXCUT

AI fast-cut commentary video editor. Feed it a voiceover; it segments the audio
into beats, plans an edit, sources clips, and assembles a fast-cut video you can
refine in a timeline editor. Local-first: one Python process, SQLite, ffmpeg,
faster-whisper. See `docs/architecture.md` for the full design.

## Run it

```bash
# macOS: double-click launcher/Start VOXCUT.command
# or from a terminal:
./launcher/start.sh
```

The launcher installs `uv` if missing, syncs a pinned Python 3.12 environment,
starts the server on `http://127.0.0.1:8484`, and opens the browser with a
per-install security token.

## Status — milestone by milestone (spec §17)

| M | Deliverable | State |
|---|---|---|
| 0 | Skeleton: FastAPI + SQLite + job runner + SSE + launcher | ✅ done |
| 1 | Upload → ASR → transcript + waveform | 🚧 in progress |
| 2 | Beat segmentation + review overlay | ⬜ |
| 3 | Planner → EDL (caption cards) → assembly → proxy render | ⬜ |
| 4 | YouTube sourcing + library + candidate ranking | ⬜ |
| 5 | Moment selection (signals + scene snap + candidates strip) | ⬜ |
| 6 | Editor v1 (swap/trim/move/delete/captions/undo) | ⬜ |
| 7 | Export (1080p) + progress/ETA | ⬜ |
| 8 | First-run wizard, settings, pruning, polish | ⬜ |

## Layout

```
backend/voxcut/   FastAPI app, job runner, pipeline steps, media engine
frontend/         Vite React SPA (built into backend/voxcut/static/)
launcher/         one-command start scripts (mac/win/linux)
```
