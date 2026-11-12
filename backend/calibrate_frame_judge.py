"""Frame-judge calibration harness — a script, not a pytest.

Talks to the real data dir (~/VOXCUT or $VOXCUT_DATA_DIR), ffmpeg and, for
the `judge` command, OpenAI. Nothing here writes to the project's EDL or DB.

WHY
  The vision frame judge is the only gate between "a video was downloaded"
  and "this exact second ships": jobs/steps/moment.py escalates to another
  source below 0.45, verifies a second finalist below 0.7, flags review
  below 0.5, and QC audits the result. A judge that hands 0.8–0.9 to a
  numbered list card never trips any of those gates — on the first audited
  project the mean score was 0.83 and nothing scored under 0.5. This script
  turns a finished project into a labelled calibration set so a judge
  change is measured against human labels instead of eyeballed.

LAYOUT  (default --out: backend/calibration/<project_id>/)
  events.json         one record per placed event: beat text, kind, asset
                      title, the chosen window, every candidate window the
                      fine verify judged, the stored production visual
                      score and the QC verdict
  labels.txt          ground truth YOU edit: "<idx>  <good|bad|?>  <event_id>  # note"
  frames/NN_*.jpg     the judge's view of the chosen window (3-frame tile)    [gitignored]
  contact_sheet_N.jpg every chosen window with idx / stored score / QC         [gitignored]
  tiles/<event>/      cached tiles for every candidate window                  [gitignored]
  scores_<tag>.json   one re-run of a judge over every event's candidate windows
  report.md           the last `report` output

COMMANDS  (run from backend/)
  uv run python calibrate_frame_judge.py extract <project_id>
  uv run python calibrate_frame_judge.py judge   <project_id> --tag new
  uv run python calibrate_frame_judge.py judge   <project_id> --tag old --legacy
  uv run python calibrate_frame_judge.py report  <project_id> [--tags old new]

  `judge --legacy` re-runs the pre-2026-10-18 judge (one 320px center frame,
  low detail, the old prompt kept verbatim below) over the same windows, so
  "before" and "after" are measured on identical inputs rather than against
  scores stored months apart. `--only 3,10,19` limits a run to a few events
  while tuning; `--detail low` measures the cheap coarse-scan configuration.
  Scores are merged into scores_<tag>.json after every event, so a killed or
  hung run keeps what finished; `--resume` re-judges only the missing or
  errored events of an existing tag.

LABELLING
  Open the contact sheets, decide per clip, edit the middle column of
  labels.txt. "good" = you would keep this clip. "bad" = it must not ship
  (title card, logo'd stock, talking head, unreadable, generic middle).
  "?" = unlabelled, excluded from agreement. Labels survive re-extraction.

AGREEMENT  (what `report` prints)
  bad  agrees when score <  0.45 — ESCALATE_BELOW, the gate that would have
                                   fetched another source
  good agrees when score >= 0.6  — comfortably clear of review (0.5) and
                                   escalation (0.45)
  The report also prints what fraction of events would escalate (< 0.45) or
  verify a second finalist (< 0.7) under each judge, so the effect on the
  existing tournament machinery is visible without changing a threshold.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from voxcut.config import settings
from voxcut.db import session_scope
from voxcut.models import Asset
from voxcut.moments.frames import (extract_frame, extract_tile, frame_data_url,
                                   is_portrait, tile_times)

ESCALATE_BELOW = 0.45   # mirrors jobs/steps/moment.py — read, never imported,
SECOND_FINALIST_BELOW = 0.7  # so this script can't drift the production gates
GOOD_AT_LEAST = 0.6

HERE = Path(__file__).resolve().parent


# --- the judge as it was before 2026-10-18, verbatim, for the baseline -------
LEGACY_FRAME_SYSTEM = """\
You pick the exact MOMENT inside a downloaded video for a fast-cut commentary
edit. The clip plays MUTED under the narrator's voiceover.

You see numbered frames, each sampled from the middle of one candidate window.
Score each frame 0..1 for how well its window would play as the visual for the
narration:
- HIGH: the frame shows the named thing/action actually happening, an
  exaggerated facial reaction, physical comedy mid-action, an obviously absurd
  gag that commits to the bit, or the recognizable meme moment. Expressive and
  instantly readable with no sound. If the narration is marked [PUNCHLINE],
  chaotic high-energy moments win; if [setup], calm clear moments win.
- LOW: someone merely talking at the camera, static shots where nothing
  happens, title cards, intros/outros, channel branding, black/blurry
  transition frames, unrelated content.
Score every frame index exactly once. Be harsh: 0.8+ means "this exact moment
is the gag". If nothing fits, low scores everywhere are the right answer."""

LEGACY_FRAME_USER = """\
Narration beat: "{beat_text}"
Visual intent: {intent}
Video: {video_title}
{n} candidate windows; frame i is from the middle of window i."""


def out_dir(project_id: str, override: str | None) -> Path:
    d = Path(override) if override else HERE / "calibration" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_project(project_id: str) -> tuple[list[dict], dict]:
    pdir = settings().projects_dir / project_id
    edl = json.loads((pdir / "edl.json").read_text())
    beats_path = pdir / "beats.json"
    beats = ({b["id"]: b for b in json.loads(beats_path.read_text())["beats"]}
             if beats_path.exists() else {})
    events = [e for e in edl["events"] if e.get("asset_id") and e.get("source")]
    return events, beats


def _same_window(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) < 0.05 and abs(a[1] - b[1]) < 0.05


def build_records(project_id: str) -> list[dict]:
    events, beats = load_project(project_id)
    recs = []
    with session_scope() as db:
        for idx, ev in enumerate(events):
            asset = db.get(Asset, ev["asset_id"])
            beat = beats.get(ev.get("beat_id"), {})
            src = ev["source"]
            chosen = (float(src["in_s"]), float(src["out_s"]))
            windows = [(float(m["in_s"]), float(m["out_s"]))
                       for m in ev.get("moment_candidates", [])]
            if not any(_same_window(chosen, w) for w in windows):
                windows.insert(0, chosen)
            chosen_idx = next(i for i, w in enumerate(windows)
                              if _same_window(chosen, w))
            qc = ev.get("qc") or {}
            recs.append({
                "idx": idx,
                "event_id": ev["id"],
                "beat_id": ev.get("beat_id"),
                # same text the production judge is given (moment._judge_windows)
                "beat_text": beat.get("text") or beat.get("gist", ""),
                "gag": ev.get("gag") or "",
                "tone": beat.get("tone", ""),
                "kind": ev["kind"],
                "asset_id": ev["asset_id"],
                "source_id": asset.source_id if asset else None,
                "asset_title": (asset.title if asset else "") or "",
                "file_path": asset.file_path if asset else None,
                "duration_s": asset.duration_s if asset else None,
                "chosen": {"in_s": chosen[0], "out_s": chosen[1]},
                "windows": [list(w) for w in windows],
                "chosen_window_idx": chosen_idx,
                "stored_visual": src.get("visual"),
                "qc_verdict": qc.get("verdict"),
                "qc_reason": qc.get("reason"),
                "flags": ev.get("flags", []),
                "n_finalists": len(ev.get("finalists", [])),
            })
    return recs


# --- labels ------------------------------------------------------------------

def read_labels(path: Path) -> dict[str, tuple[str, str]]:
    """event_id -> (label, note). Tolerates a missing file."""
    labels: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return labels
    for line in path.read_text().splitlines():
        body, _, note = line.partition("#")
        parts = body.split()
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        labels[parts[2]] = (parts[1].lower(), note.strip())
    return labels


def write_labels(path: Path, recs: list[dict],
                 existing: dict[str, tuple[str, str]]) -> None:
    lines = ["# idx  label(good|bad|?)  event_id   # asset title | narration",
             "# Edit the middle column. Lines starting with # are ignored."]
    for r in recs:
        label, note = existing.get(r["event_id"], ("?", ""))
        note = note or f"{r['asset_title'][:45]} | {r['beat_text'][:60]}"
        lines.append(f"{r['idx']:>3}  {label:<4}  {r['event_id']}  # {note}")
    path.write_text("\n".join(lines) + "\n")


# --- extraction ---------------------------------------------------------------

def tile_path(out: Path, rec: dict, k: int) -> Path:
    return out / "tiles" / rec["event_id"] / f"w{k}.jpg"


def center_path(out: Path, rec: dict, k: int) -> Path:
    return out / "tiles" / rec["event_id"] / f"c{k}.jpg"


def ensure_tile(rec: dict, k: int, out: Path) -> Path | None:
    """Production-identical tile for window k (cached)."""
    p = tile_path(out, rec, k)
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    video = Path(rec["file_path"])
    in_s, out_s = rec["windows"][k]
    ok = extract_tile(video, tile_times(in_s, out_s), p,
                      portrait=rec.get("portrait", False))
    if not ok:
        ok = extract_frame(video, (in_s + out_s) / 2, p, width=640)
    return p if ok else None


def ensure_center(rec: dict, k: int, out: Path) -> Path | None:
    """Legacy-identical 320px center frame for window k (cached)."""
    p = center_path(out, rec, k)
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    in_s, out_s = rec["windows"][k]
    ok = extract_frame(Path(rec["file_path"]), (in_s + out_s) / 2, p)
    return p if ok else None


def contact_sheets(recs: list[dict], out: Path, per_page: int = 24) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont

    cols, thumb_w, thumb_h, pad, text_h = 3, 200, 150, 6, 46
    cell_w = 3 * (thumb_w + pad) + pad
    cell_h = thumb_h + text_h + pad
    font = ImageFont.load_default(size=13)
    small = ImageFont.load_default(size=11)
    pages: list[Path] = []
    for pg, start in enumerate(range(0, len(recs), per_page), 1):
        chunk = recs[start:start + per_page]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
        draw = ImageDraw.Draw(sheet)
        for j, r in enumerate(chunk):
            cx, cy = (j % cols) * cell_w, (j // cols) * cell_h
            fp = r.get("frame_path")
            if fp and Path(fp).exists():
                tile = Image.open(fp)
                w, h = tile.size
                parts = ([tile.crop((0, i * h // 3, w, (i + 1) * h // 3)) for i in range(3)]
                         if h > w else
                         [tile.crop((i * w // 3, 0, (i + 1) * w // 3, h)) for i in range(3)])
                for i, part in enumerate(parts):
                    part.thumbnail((thumb_w, thumb_h))
                    sheet.paste(part, (cx + pad + i * (thumb_w + pad), cy + pad))
            else:
                draw.text((cx + pad, cy + pad), "(no frame)", fill="red", font=font)
            vis = r["stored_visual"]
            line1 = (f"#{r['idx']}  vis={vis if vis is not None else '-'}  "
                     f"qc={r['qc_verdict'] or '-'}  {r['kind']}  "
                     f"[{r['chosen']['in_s']:.1f}-{r['chosen']['out_s']:.1f}s]")
            line2 = f"{r['asset_title'][:70]}"
            line3 = f"“{r['beat_text'][:85]}”"
            ty = cy + pad + thumb_h + 2
            draw.text((cx + pad, ty), line1, fill="black", font=font)
            draw.text((cx + pad, ty + 15), line2, fill=(60, 60, 60), font=small)
            draw.text((cx + pad, ty + 29), line3, fill=(0, 70, 140), font=small)
        p = out / f"contact_sheet_{pg}.jpg"
        sheet.save(p, quality=85)
        pages.append(p)
    return pages


def cmd_extract(args: argparse.Namespace) -> None:
    out = out_dir(args.project_id, args.out)
    recs = build_records(args.project_id)
    (out / "frames").mkdir(exist_ok=True)
    for r in recs:
        if not r["file_path"] or not Path(r["file_path"]).exists():
            print(f"#{r['idx']}: asset file missing — skipped", file=sys.stderr)
            continue
        r["portrait"] = is_portrait(Path(r["file_path"]))
        tile = ensure_tile(r, r["chosen_window_idx"], out)
        if tile:
            dest = out / "frames" / f"{r['idx']:02d}_{r['event_id'][-6:]}.jpg"
            dest.write_bytes(tile.read_bytes())
            r["frame_path"] = str(dest)
        print(f"#{r['idx']:2d} {'ok ' if tile else 'FAIL'} "
              f"{r['asset_title'][:50]!r} vis={r['stored_visual']}")
    (out / "events.json").write_text(json.dumps(recs, indent=1))
    pages = contact_sheets(recs, out)
    labels_path = out / "labels.txt"
    write_labels(labels_path, recs, read_labels(labels_path))
    print(f"\n{len(recs)} events → {out}")
    print("contact sheets:", ", ".join(p.name for p in pages))
    print(f"labels: {labels_path}  (edit the middle column)")


# --- judging -------------------------------------------------------------------

def judge_one(rec: dict, out: Path, *, legacy: bool, detail: str,
              context: tuple[str, str] | None = None) -> dict:
    from voxcut.brain.client import structured
    from voxcut.brain.judge import JUDGE_SCHEMA, judge_frames_detailed

    n = len(rec["windows"])
    result = {"idx": rec["idx"], "event_id": rec["event_id"],
              "chosen_window_idx": rec["chosen_window_idx"],
              "scores": [None] * n, "records": None, "error": None}
    try:
        paths = [(ensure_center if legacy else ensure_tile)(rec, k, out)
                 for k in range(n)]
        present = [(k, frame_data_url(p)) for k, p in enumerate(paths) if p]
        if not present:
            raise RuntimeError("no frames extracted")
        urls = [u for _k, u in present]
        if legacy:
            images = [(f"Frame {i}:", u) for i, u in enumerate(urls)]
            outj = structured(
                LEGACY_FRAME_SYSTEM,
                LEGACY_FRAME_USER.format(beat_text=rec["beat_text"],
                                         intent=rec["kind"],
                                         video_title=rec["asset_title"],
                                         n=len(urls)),
                JUDGE_SCHEMA, schema_name="frame_judge", temperature=0.2,
                max_tokens=1500, images=images, image_detail="low")
            scores = [0.0] * len(urls)
            for p in outj.get("picks", []):
                if 0 <= p["index"] < len(urls):
                    scores[p["index"]] = max(0.0, min(1.0, float(p["relevance"])))
            for (k, _u), s in zip(present, scores):
                result["scores"][k] = s
        else:
            detailed = judge_frames_detailed(
                rec["beat_text"], rec["kind"], rec["asset_title"], urls,
                detail=detail, gag=rec.get("gag") or "", context=context)
            result["records"] = [None] * n
            for (k, _u), d in zip(present, detailed):
                result["scores"][k] = d["score"]
                result["records"][k] = d
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    c = rec["chosen_window_idx"]
    result["chosen_score"] = result["scores"][c]
    if result["records"] and result["records"][c]:
        d = result["records"][c]
        result["chosen_flags"] = d["flags"]
        result["chosen_soft_flags"] = d.get("soft_flags", [])
        result["chosen_info"] = d.get("info", [])
        result["chosen_raw"] = d["raw_score"]
        result["chosen_rank"] = d["rank"]
        result["chosen_seen"] = d["seen"]
        result["chosen_reason"] = d["reason"]
    return result


def cmd_judge(args: argparse.Namespace) -> None:
    out = out_dir(args.project_id, args.out)
    recs = json.loads((out / "events.json").read_text())
    # --context: send what production now sends — the neighboring narration
    # (adjacent placed events stand in for adjacent beats) and the planner's
    # gag when the event has one. Compare a --context tag against a plain
    # one to measure what the extra lines do to agreement.
    ctx_of: dict[str, tuple[str, str]] = {}
    if args.context:
        for i, r in enumerate(recs):
            ctx_of[r["event_id"]] = (
                recs[i - 1]["beat_text"] if i > 0 else "",
                recs[i + 1]["beat_text"] if i + 1 < len(recs) else "")
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        recs = [r for r in recs if r["idx"] in wanted]
    recs = [r for r in recs if r.get("file_path") and Path(r["file_path"]).exists()]
    label = "legacy judge (1 center frame, low)" if args.legacy else \
        f"current judge_frames (3-frame tiles, {args.detail})"
    path = out / f"scores_{args.tag}.json"
    # Results are merged into the file after EVERY event, so a hung call or
    # a killed run keeps what finished; --only / --resume build on them.
    existing: dict[str, dict] = {}
    if path.exists() and (args.only or args.resume):
        existing = {r["event_id"]: r
                    for r in json.loads(path.read_text())["results"]}
    if args.resume:
        recs = [r for r in recs if r["event_id"] not in existing
                or existing[r["event_id"]].get("error")]
    print(f"judging {len(recs)} events with {label} → {path.name}")
    lock = threading.Lock()

    def flush() -> None:
        merged = sorted(existing.values(), key=lambda r: r["idx"])
        path.write_text(json.dumps({"tag": args.tag, "legacy": args.legacy,
                                    "detail": args.detail, "results": merged},
                                   indent=1))

    def run(r: dict) -> dict:
        res = judge_one(r, out, legacy=args.legacy, detail=args.detail,
                        context=ctx_of.get(r["event_id"]))
        flags = ",".join((res.get("chosen_flags") or [])
                         + (res.get("chosen_soft_flags") or []))
        print(f"#{r['idx']:2d} stored={r['stored_visual']}  now={res['chosen_score']}"
              f"{'  flags=' + flags if flags else ''}"
              f"{'  ERR ' + res['error'] if res['error'] else ''}", flush=True)
        with lock:
            existing[res["event_id"]] = res
            flush()
        return res

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(run, recs))
    print(f"wrote {path}")


# --- reporting -----------------------------------------------------------------

def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def summarize(name: str, scores: dict[str, float | None],
              labels: dict[str, tuple[str, str]]) -> dict:
    vals = [v for v in scores.values() if v is not None]
    bad = [e for e, (lab, _n) in labels.items() if lab == "bad" and scores.get(e) is not None]
    good = [e for e, (lab, _n) in labels.items() if lab == "good" and scores.get(e) is not None]
    bad_caught = sum(1 for e in bad if scores[e] < ESCALATE_BELOW)
    good_kept = sum(1 for e in good if scores[e] >= GOOD_AT_LEAST)
    return {
        "judge": name, "n": len(vals),
        "mean": statistics.mean(vals) if vals else None,
        "median": statistics.median(vals) if vals else None,
        "escalate": sum(1 for v in vals if v < ESCALATE_BELOW),
        "second_finalist": sum(1 for v in vals if v < SECOND_FINALIST_BELOW),
        "bad_caught": bad_caught, "n_bad": len(bad),
        "good_kept": good_kept, "n_good": len(good),
        "agreement": ((bad_caught + good_kept) / (len(bad) + len(good))
                      if (bad or good) else None),
    }


def cmd_report(args: argparse.Namespace) -> None:
    out = out_dir(args.project_id, args.out)
    recs = json.loads((out / "events.json").read_text())
    labels = read_labels(out / "labels.txt")
    tags = args.tags or sorted(p.stem[len("scores_"):]
                               for p in out.glob("scores_*.json"))
    runs: dict[str, dict[str, dict]] = {}
    for t in tags:
        p = out / f"scores_{t}.json"
        if p.exists():
            runs[t] = {r["event_id"]: r for r in json.loads(p.read_text())["results"]}

    columns = ["stored"] + list(runs)
    per_judge: dict[str, dict[str, float | None]] = {
        "stored": {r["event_id"]: r["stored_visual"] for r in recs}}
    for t, res in runs.items():
        per_judge[t] = {e: res[e]["chosen_score"] for e in res}

    lines = [f"# Frame-judge calibration — {args.project_id}", "",
             f"{len(recs)} placed events, "
             f"{sum(1 for lab, _ in labels.values() if lab in ('good', 'bad'))} labelled "
             f"({sum(1 for lab, _ in labels.values() if lab == 'bad')} bad, "
             f"{sum(1 for lab, _ in labels.values() if lab == 'good')} good).", "",
             "## Summary", "",
             "| judge | n | mean | median | <0.45 escalate | <0.7 2nd finalist "
             "| bad caught (<0.45) | good kept (>=0.6) | agreement |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c in columns:
        s = summarize(c, per_judge[c], labels)
        lines.append(
            f"| {c} | {s['n']} | {_fmt(s['mean'])} | {_fmt(s['median'])} "
            f"| {s['escalate']} ({s['escalate'] / max(s['n'], 1):.0%}) "
            f"| {s['second_finalist']} ({s['second_finalist'] / max(s['n'], 1):.0%}) "
            f"| {s['bad_caught']}/{s['n_bad']} | {s['good_kept']}/{s['n_good']} "
            f"| {_fmt(s['agreement'])} |")

    lines += ["", "## Labelled events", "",
              "| idx | label | " + " | ".join(columns) + " | flags (last run) | clip |",
              "|---|---|" + "---|" * len(columns) + "---|---|"]
    last = list(runs)[-1] if runs else None
    for r in recs:
        label = labels.get(r["event_id"], ("?", ""))[0]
        if label not in ("good", "bad") and not args.all:
            continue
        cells = [_fmt(per_judge[c].get(r["event_id"])) for c in columns]
        flags = ""
        if last and r["event_id"] in runs[last]:
            rr = runs[last][r["event_id"]]
            flags = ",".join((rr.get("chosen_flags") or [])
                             + [f"{f}(cap)" for f in rr.get("chosen_soft_flags") or []])
        lines.append(f"| {r['idx']} | {label} | " + " | ".join(cells) +
                     f" | {flags} | {r['asset_title'][:40]} |")

    if last:
        lines += ["", f"## What the `{last}` judge saw (chosen windows)", ""]
        for r in recs:
            res = runs[last].get(r["event_id"])
            if not res or res.get("chosen_seen") is None:
                continue
            label = labels.get(r["event_id"], ("?", ""))[0]
            lines.append(f"- #{r['idx']} [{label}] {res['chosen_score']} "
                         f"(raw {res.get('chosen_raw')}, rank {res.get('chosen_rank')}"
                         f"{', ' + ','.join(res['chosen_flags']) if res.get('chosen_flags') else ''}"
                         f"{', cap:' + ','.join(res['chosen_soft_flags']) if res.get('chosen_soft_flags') else ''}"
                         f"{', info:' + ','.join(res['chosen_info']) if res.get('chosen_info') else ''}) "
                         f"— {res['chosen_seen']} — {res['chosen_reason']}")
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text)
    print(text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("extract", "judge", "report"):
        sp = sub.add_parser(name)
        sp.add_argument("project_id")
        sp.add_argument("--out", help="output dir (default backend/calibration/<id>)")
        if name == "judge":
            sp.add_argument("--tag", required=True, help="name for scores_<tag>.json")
            sp.add_argument("--legacy", action="store_true",
                            help="re-run the pre-2026-10-18 judge as the baseline")
            sp.add_argument("--detail", default="high", choices=["low", "high"])
            sp.add_argument("--workers", type=int, default=4)
            sp.add_argument("--only", help="comma-separated event idx subset")
            sp.add_argument("--context", action="store_true",
                            help="also send neighboring narration + gag, as "
                                 "production does")
            sp.add_argument("--resume", action="store_true",
                            help="skip events already in scores_<tag>.json")
        if name == "report":
            sp.add_argument("--tags", nargs="*", help="score tags to include")
            sp.add_argument("--all", action="store_true",
                            help="list every event, not only labelled ones")
    args = ap.parse_args()
    {"extract": cmd_extract, "judge": cmd_judge, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
