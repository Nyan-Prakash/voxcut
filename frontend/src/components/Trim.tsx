import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { TrimInfo } from "../types";

type Cut = [number, number];

/** Merge overlapping cuts so totals and playback skipping are exact. */
function merge(cuts: Cut[]): Cut[] {
  const s = cuts.filter(([a, b]) => b - a > 0.03).sort((x, y) => x[0] - y[0]);
  const out: Cut[] = [];
  for (const [a, b] of s) {
    const last = out[out.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

const fmt = (t: number) =>
  `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;

/**
 * Trim step: review the raw recording before any clips are chosen.
 * Four ways to cut — auto pauses, flagged retakes, transcript selection and
 * waveform drags — all feeding one list of cuts. Nothing is changed until
 * "Trim and continue"; "Leave as is" keeps the recording untouched.
 */
export function TrimStage() {
  const { project, trim, waveform } = useStore();
  const info = trim as TrimInfo;
  const dur = info.raw_duration_s || 0;

  // Reopening a finished trim starts from the cuts already applied.
  const reopened = info.state === "done";
  const [pausesOn, setPausesOn] = useState(!reopened);
  const [minPause, setMinPause] = useState(info.defaults?.min_pause_s ?? 0.6);
  const [keepPause, setKeepPause] = useState(info.defaults?.keep_pause_s ?? 0.25);
  const [keptPauses, setKeptPauses] = useState<Set<number>>(new Set());
  const [retakesOn, setRetakesOn] = useState<Set<number>>(new Set());
  const [manual, setManual] = useState<Cut[]>(
    reopened ? info.cuts.map((c) => [c[0], c[1]] as Cut) : []);
  const [sel, setSel] = useState<[number, number] | null>(null); // word idx range
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);

  const audioRef = useRef<HTMLAudioElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  // Pauses the app would cut at the current threshold: [silenceIndex, cut].
  const pauses = useMemo(() => {
    const out: { i: number; cut: Cut; len: number }[] = [];
    info.silences.forEach(([s, e], i) => {
      if (e - s < minPause) return;
      const a = s + keepPause / 2, b = e - keepPause / 2;
      if (b - a > 0.06) out.push({ i, cut: [a, b], len: e - s });
    });
    return out;
  }, [info.silences, minPause, keepPause]);

  const cuts = useMemo(() => merge([
    ...(pausesOn ? pauses.filter((p) => !keptPauses.has(p.i)).map((p) => p.cut) : []),
    ...info.retakes.filter((_r, i) => retakesOn.has(i))
      .map((r) => [r.start_s, r.end_s] as Cut),
    ...manual,
  ]), [pausesOn, pauses, keptPauses, retakesOn, manual, info.retakes]);
  const removed = cuts.reduce((n, [a, b]) => n + (b - a), 0);
  const inCut = (x: number) => cuts.find(([a, b]) => x >= a && x < b);

  // Playback: the audio element is the clock; the (silent) facecam follows.
  // While playing, jump over every cut so the preview is the trimmed result.
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const a = audioRef.current;
      if (a) {
        const c = inCut(a.currentTime);
        if (c) a.currentTime = c[1];
        const v = videoRef.current;
        if (v && Math.abs(v.currentTime - a.currentTime) > 0.12) {
          v.currentTime = a.currentTime;
        }
        setT(a.currentTime);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, cuts]);

  const seek = (x: number) => {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = Math.max(0, Math.min(dur, x));
    if (videoRef.current) videoRef.current.currentTime = a.currentTime;
    setT(a.currentTime);
  };
  const toggle = () => {
    const a = audioRef.current, v = videoRef.current;
    if (!a) return;
    if (a.paused) { a.play(); v?.play().catch(() => {}); }
    else { a.pause(); v?.pause(); }
  };

  const cutSelection = () => {
    if (!sel) return;
    const [i0, i1] = [Math.min(...sel), Math.max(...sel)];
    const w0 = info.words[i0], w1 = info.words[i1], next = info.words[i1 + 1];
    // Take the gap after the last word too, so no dead air is left behind.
    setManual((m) => [...m, [w0.start_s, next ? next.start_s : w1.end_s]]);
    setSel(null);
  };

  const submit = async (list: Cut[]) => {
    if (!project) return;
    setBusy(true);
    try {
      await api.applyTrim(project.id, list);
      useStore.getState().setToast(list.length
        ? `✂ Applying ${list.length} cuts…` : "Keeping the recording as is…");
    } catch (e: any) {
      useStore.getState().setToast(`Trim failed: ${e.message}`);
      setBusy(false);
    }
  };

  if (!project) return null;
  const pauseAfter = new Map<number, { i: number; len: number }>();
  pauses.forEach((p) => {
    // Attach each pause to the last word that ends before it.
    let k = -1;
    for (let j = 0; j < info.words.length; j++) {
      if (info.words[j].start_s <= p.cut[0]) k = j; else break;
    }
    pauseAfter.set(k, { i: p.i, len: p.len });   // -1 = before the first word
  });
  const pauseChip = (p: { i: number; len: number }) => (
    <span className={`tp${keptPauses.has(p.i) ? " kept" : ""}`}
          title={keptPauses.has(p.i) ? "Pause kept — click to cut it"
                                     : "Pause will be cut — click to keep it"}
          onClick={() => setKeptPauses((s) => {
            const n = new Set(s);
            n.has(p.i) ? n.delete(p.i) : n.add(p.i);
            return n;
          })}>
      ⏸{p.len.toFixed(1)}s
    </span>
  );
  const lead = pausesOn ? pauseAfter.get(-1) : undefined;

  return (
    <div className="editor trim" style={{ height: "100%" }}>
      <div className="editor-top">
        <div className="preview-pane">
          {info.has_video ? (
            <video ref={videoRef} muted playsInline style={{ flex: 1, minHeight: 0 }}
                   src={api.trimVideoUrl(project.id)} onClick={toggle} />
          ) : (
            <div className="muted" style={{ textAlign: "center" }}>
              Audio-only recording.<br />Press play to hear it with the cuts applied.
            </div>
          )}
          <audio ref={audioRef} src={api.trimAudioUrl(project.id)}
                 onPlay={() => setPlaying(true)}
                 onPause={() => setPlaying(false)}
                 onEnded={() => { setPlaying(false); videoRef.current?.pause(); }} />
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={toggle}>{playing ? "⏸ Pause" : "▶ Play with cuts"}</button>
            <span className="tl-time">{fmt(t)} <em>/ {fmt(dur)}</em></span>
          </div>
        </div>

        <div className="inspector">
          <h2>Trim the recording</h2>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            Cut dead air and flubbed lines before any clips are chosen. Nothing
            changes until you continue, and you can leave it exactly as recorded.
          </div>

          <label>
            <input type="checkbox" checked={pausesOn} style={{ width: "auto", marginRight: 6 }}
                   onChange={(e) => setPausesOn(e.target.checked)} />
            Cut pauses ({pausesOn ? pauses.length - [...keptPauses].filter(
              (i) => pauses.some((p) => p.i === i)).length : 0})
          </label>
          <div className="trim-row">
            <span>longer than {minPause.toFixed(1)}s</span>
            <input type="range" min={0.3} max={2} step={0.1} value={minPause}
                   disabled={!pausesOn}
                   onChange={(e) => setMinPause(Number(e.target.value))} />
          </div>
          <div className="trim-row">
            <span>leave {keepPause.toFixed(2)}s</span>
            <input type="range" min={0.1} max={0.6} step={0.05} value={keepPause}
                   disabled={!pausesOn}
                   onChange={(e) => setKeepPause(Number(e.target.value))} />
          </div>
          <div className="muted" style={{ fontSize: 11 }}>
            Click a ⏸ marker in the transcript to keep that pause.
          </div>

          <label>Possible retakes ({info.retakes.length})</label>
          {info.retakes.length === 0 && (
            <div className="muted" style={{ fontSize: 12 }}>None found.</div>
          )}
          {info.retakes.map((r, i) => (
            <div key={i} className="trim-item">
              <input type="checkbox" checked={retakesOn.has(i)} style={{ width: "auto" }}
                     onChange={(e) => setRetakesOn((s) => {
                       const n = new Set(s);
                       e.target.checked ? n.add(i) : n.delete(i);
                       return n;
                     })} />
              <span className="trim-text" title="Jump to it"
                    onClick={() => seek(Math.max(0, r.start_s - 0.5))}>
                {fmt(r.start_s)} · {r.reason}: “{r.text}”
              </span>
            </div>
          ))}

          <label>Your cuts ({manual.length})</label>
          {manual.length === 0 && (
            <div className="muted" style={{ fontSize: 12 }}>
              Drag across the waveform, or click a word then shift-click
              another and press “Cut selected words”.
            </div>
          )}
          {manual.map((c, i) => (
            <div key={i} className="trim-item">
              <span className="trim-text" onClick={() => seek(Math.max(0, c[0] - 0.5))}>
                {fmt(c[0])} → {fmt(c[1])} ({(c[1] - c[0]).toFixed(1)}s)
              </span>
              <button className="ghost" title="Restore this part"
                      onClick={() => setManual((m) => m.filter((_x, j) => j !== i))}>✕</button>
            </div>
          ))}
        </div>
      </div>

      <div className="timeline-wrap">
        <div className="tl-toolbar">
          <button className="sec" disabled={!sel} onClick={cutSelection}>
            ✂ Cut selected words
          </button>
          {sel && <button className="ghost" onClick={() => setSel(null)}>clear selection</button>}
          <div className="spacer" />
          <span className="tl-time">
            {fmt(dur)} <em>→ {fmt(dur - removed)} · {cuts.length} cuts, −{removed.toFixed(1)}s</em>
          </span>
          <button className="sec" disabled={busy} onClick={() => submit([])}
                  title="Keep the recording exactly as uploaded">
            Leave as is →
          </button>
          <button disabled={busy || cuts.length === 0} onClick={() => submit(cuts)}>
            ✂ Trim and continue
          </button>
        </div>
        <TrimWave dur={dur} t={t} cuts={cuts} seek={seek}
                  peaks={waveform && Math.abs(waveform.peaks.length
                    / waveform.buckets_per_s - dur) < 1 ? waveform.peaks : null}
                  onDrag={(a, b) => setManual((m) => [...m, [a, b]])} />
        <div className="trim-words">
          {lead && <>{pauseChip(lead)}{" "}</>}
          {info.words.map((w, k) => {
            const cut = !!inCut((w.start_s + w.end_s) / 2);
            const picked = sel && k >= Math.min(...sel) && k <= Math.max(...sel);
            const p = pausesOn ? pauseAfter.get(k) : undefined;
            const now = t >= w.start_s && t < w.end_s;
            return (
              <span key={w.idx}>
                <span className={`tw${cut ? " cut" : ""}${picked ? " picked" : ""}${now ? " now" : ""}`}
                      onClick={(e) => {
                        if (e.shiftKey && sel) setSel([sel[0], k]);
                        else { setSel([k, k]); seek(w.start_s); }
                      }}>
                  {w.text.trim()}
                </span>{" "}
                {p && pauseChip(p)}{p ? " " : ""}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Waveform strip: click to seek, drag to mark a stretch to remove. */
function TrimWave({ dur, t, cuts, peaks, seek, onDrag }: {
  dur: number; t: number; cuts: Cut[]; peaks: number[] | null;
  seek: (t: number) => void; onDrag: (a: number, b: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);
  const at = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    return Math.max(0, Math.min(dur, ((clientX - r.left) / r.width) * dur));
  };

  useEffect(() => {
    const c = canvas.current;
    if (!c || !peaks) return;
    const w = (c.width = c.clientWidth * 2), h = (c.height = c.clientHeight * 2);
    const g = c.getContext("2d")!;
    g.clearRect(0, 0, w, h);
    g.fillStyle = getComputedStyle(c).color;
    const step = peaks.length / w;
    for (let x = 0; x < w; x++) {
      let m = 0;
      for (let k = Math.floor(x * step); k < Math.floor((x + 1) * step) + 1 && k < peaks.length; k++) {
        m = Math.max(m, peaks[k]);
      }
      const bar = Math.max(1, m * h * 0.9);
      g.fillRect(x, (h - bar) / 2, 1, bar);
    }
  }, [peaks, dur]);

  const pct = (x: number) => `${(x / Math.max(dur, 0.001)) * 100}%`;
  return (
    <div className="trim-wave" ref={ref}
         onMouseDown={(e) => { const x = at(e.clientX); setDrag([x, x]); }}
         onMouseMove={(e) => drag && setDrag([drag[0], at(e.clientX)])}
         onMouseLeave={() => setDrag(null)}
         onMouseUp={() => {
           if (!drag) return;
           const [a, b] = [Math.min(...drag), Math.max(...drag)];
           if (b - a > 0.15) onDrag(a, b); else seek(a);
           setDrag(null);
         }}>
      <canvas ref={canvas} />
      {cuts.map(([a, b], i) => (
        <div key={i} className="trim-cut" style={{ left: pct(a), width: pct(b - a) }} />
      ))}
      {drag && (
        <div className="trim-cut pending" style={{
          left: pct(Math.min(...drag)), width: pct(Math.abs(drag[1] - drag[0])) }} />
      )}
      <div className="trim-head" style={{ left: pct(t) }} />
    </div>
  );
}
