import React, { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";

const PX_PER_S = 60;

interface MusicRegion {
  id: string; file: string; start_s: number; end_s: number; gain_db?: number;
}

export function Timeline() {
  const { edl, beats, project, selectedEventIds, select, playheadS, seek,
          tool, splitAt, addSegmentRange, reroll } = useStore();
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null);
  const dur = project?.duration_s || (edl ? Math.max(...edl.events.map((e) => e.end_s)) : 0);
  const width = Math.max(600, dur * PX_PER_S);

  if (!edl) return null;

  const tAt = (e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    return (e.clientX - rect.left) / PX_PER_S;
  };

  const onBackgroundClick = (e: React.MouseEvent) => {
    if (tool === "select") seek(tAt(e));
  };

  const onEventClick = (e: React.MouseEvent, evId: string) => {
    e.stopPropagation();
    if (tool === "cut") {
      const rect = (e.currentTarget as HTMLElement).closest(".tl-root")!.getBoundingClientRect();
      splitAt(evId, (e.clientX - rect.left) / PX_PER_S);
    } else {
      // cmd/ctrl-click toggles a clip in the selection; shift-click selects a range.
      select(evId, e.metaKey || e.ctrlKey ? "toggle" : e.shiftKey ? "range" : "single");
    }
  };

  // Drag-to-add: press, sweep a range, release → new segment.
  const onMouseDown = (e: React.MouseEvent) => {
    if (tool !== "add") return;
    const t = tAt(e);
    setDrag({ a: t, b: t });
  };
  const onMouseMove = (e: React.MouseEvent) => {
    if (tool !== "add" || !drag) return;
    setDrag({ a: drag.a, b: tAt(e) });
  };
  const onMouseUp = () => {
    if (tool !== "add" || !drag) return;
    const [a, b] = [Math.min(drag.a, drag.b), Math.max(drag.a, drag.b)];
    setDrag(null);
    if (b - a >= 0.5) addSegmentRange(a, b);
  };

  return (
    <div className={`tl-root tool-${tool}`}
         style={{ minWidth: width, position: "relative" }}
         onClick={onBackgroundClick}
         onMouseDown={onMouseDown} onMouseMove={onMouseMove}
         onMouseUp={onMouseUp} onMouseLeave={() => setDrag(null)}>
      <div className="playhead" style={{ left: playheadS * PX_PER_S }} />
      {drag && (
        <div className="add-range"
             style={{ left: Math.min(drag.a, drag.b) * PX_PER_S,
                      width: Math.abs(drag.b - drag.a) * PX_PER_S }} />
      )}
      <Wave width={width} />
      {/* Beat ruler */}
      <div className="track" style={{ height: 20 }}>
        <div className="tl-inner">
          {beats.map((b) => (
            <div key={b.id} className="beatmark"
                 style={{ left: b.start_s * PX_PER_S, width: (b.end_s - b.start_s) * PX_PER_S }}
                 title={b.gist} />
          ))}
        </div>
      </div>
      {/* Video track */}
      <div className="track-label">video</div>
      <div className="track" style={{ height: 56 }}>
        <div className="tl-inner">
          {edl.events.map((e) => {
            const cls = e.flags?.includes("gap_unfilled") ? "gap" : "clip";
            const rev = e.flags?.includes("needs_review") || e.flags?.includes("close_call")
              || e.flags?.includes("qc_middle");
            // Label with what actually PLAYS (tournament winner), not the query.
            const winner = e.finalists?.find((f: any) => f.asset_id === e.asset_id);
            const label = winner?.title || e.queries?.[0]
              || (e.flags?.includes("user_added") ? "new segment — search or reroll" : e.kind);
            return (
              <div key={e.id}
                   className={`evt ${cls} ${selectedEventIds.includes(e.id) ? "sel" : ""}`}
                   style={{ left: e.start_s * PX_PER_S,
                            width: Math.max(8, (e.end_s - e.start_s) * PX_PER_S) }}
                   onClick={(me) => onEventClick(me, e.id)}
                   title={tool === "cut" ? "click to cut here" : `${e.kind} · ${label}`}>
                {e.asset_id && project && (
                  <img alt=""
                       // Cache-buster tracks the footage: new asset or new
                       // moment → new URL → browser refetches the thumbnail.
                       src={api.mediaUrl(`/projects/${project.id}/thumb/${e.id}`)
                            + `&v=${e.asset_id}-${e.source?.in_s ?? 0}`}
                       onError={(ev) => ((ev.target as HTMLElement).style.display = "none")} />
                )}
                {rev && <span className="rev">⚑</span>}
                {tool === "select" && !e.locked && (
                  <button className="dice" title="Reroll: fresh plan + fresh footage for this clip"
                          onClick={(me) => { me.stopPropagation(); reroll([e.id]); }}>
                    🎲
                  </button>
                )}
                <span className="evt-label">{label}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Music section timeline: waveform + beat ruler for reference, music lane
 *  for editing. The video track lives in the clips section only. */
export function MusicTimeline() {
  const { edl, beats, project, playheadS, seek } = useStore();
  const dur = project?.duration_s || (edl ? Math.max(...edl.events.map((e) => e.end_s)) : 0);
  const width = Math.max(600, dur * PX_PER_S);

  const seekAt = (e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    seek((e.clientX - rect.left) / PX_PER_S);
  };

  return (
    <div className="tl-root" style={{ minWidth: width, position: "relative" }}
         onClick={seekAt}>
      <div className="playhead" style={{ left: playheadS * PX_PER_S }} />
      <Wave width={width} />
      <div className="track" style={{ height: 14 }}>
        <div className="tl-inner">
          {beats.map((b) => (
            <div key={b.id} className="beatmark"
                 style={{ left: b.start_s * PX_PER_S, width: (b.end_s - b.start_s) * PX_PER_S }}
                 title={b.gist} />
          ))}
        </div>
      </div>
      <MusicLane width={width} dur={dur} tall />
    </div>
  );
}

function MusicLane({ width, dur, tall }: { width: number; dur: number; tall?: boolean }) {
  const { project, updateMusic, setToast } = useStore();
  const laneRef = useRef<HTMLDivElement>(null);
  const [tracks, setTracks] = useState<any[]>([]);
  const [selTrack, setSelTrack] = useState("");
  const [vol, setVol] = useState<number | null>(null);
  const [drag, setDrag] = useState<null | {
    kind: "create" | "move" | "resizeL" | "resizeR";
    id?: string; anchor: number; cur: number; orig?: MusicRegion;
  }>(null);

  const music = { enabled: true, volume_db: -25, regions: [] as MusicRegion[],
                  ...(project?.settings?.music || {}) };
  const regions: MusicRegion[] = music.regions || [];

  useEffect(() => {
    api.musicList().then((d) => {
      setTracks(d.tracks);
      setSelTrack((cur) => cur || d.tracks[0]?.name || "");
    }).catch(() => {});
  }, []);

  const tAt = (e: React.MouseEvent) => {
    const r = laneRef.current!.getBoundingClientRect();
    return Math.max(0, Math.min(dur, (e.clientX - r.left) / PX_PER_S));
  };

  // Live view of regions while dragging (committed on mouseup).
  const view: MusicRegion[] = regions.map((r) => {
    if (!drag || drag.id !== r.id || !drag.orig) return r;
    const d = drag.cur - drag.anchor;
    const o = drag.orig;
    if (drag.kind === "move") {
      const len = o.end_s - o.start_s;
      const s = Math.max(0, Math.min(dur - len, o.start_s + d));
      return { ...r, start_s: s, end_s: s + len };
    }
    if (drag.kind === "resizeL") return { ...r, start_s: Math.min(Math.max(0, o.start_s + d), o.end_s - 2) };
    return { ...r, end_s: Math.max(Math.min(dur, o.end_s + d), o.start_s + 2) };
  });

  const commit = (regs: MusicRegion[]) => {
    const clean = regs.map((r) => ({ ...r, start_s: Math.round(r.start_s * 100) / 100,
                                     end_s: Math.round(r.end_s * 100) / 100 }))
      .filter((r) => r.end_s - r.start_s >= 2);
    updateMusic({ regions: clean });
  };

  const onLaneDown = (e: React.MouseEvent) => {
    e.stopPropagation();
    if ((e.target as HTMLElement).closest(".mreg")) return;
    if (!selTrack) { setToast("Upload a track in Library → Music first"); return; }
    const t = tAt(e);
    setDrag({ kind: "create", anchor: t, cur: t });
  };
  const onRegionDown = (e: React.MouseEvent, r: MusicRegion) => {
    e.stopPropagation();
    const el = e.currentTarget as HTMLElement;
    const x = e.clientX - el.getBoundingClientRect().left;
    const kind = x < 9 ? "resizeL" : x > el.clientWidth - 9 ? "resizeR" : "move";
    setDrag({ kind, id: r.id, anchor: tAt(e), cur: tAt(e), orig: { ...r } });
  };
  const onMove = (e: React.MouseEvent) => {
    if (!drag) return;
    e.stopPropagation();
    setDrag({ ...drag, cur: tAt(e) });
  };
  const onUp = (e: React.MouseEvent) => {
    if (!drag) return;
    e.stopPropagation();
    if (drag.kind === "create") {
      const [a, b] = [Math.min(drag.anchor, drag.cur), Math.max(drag.anchor, drag.cur)];
      if (b - a >= 2) {
        commit([...regions, { id: `mr_${Date.now()}`, file: selTrack,
                              start_s: a, end_s: b, gain_db: 0 }]);
      }
    } else {
      commit(view);
    }
    setDrag(null);
  };

  const suggest = async () => {
    if (!project) return;
    try {
      await api.musicSuggest(project.id);
      const p = await api.getProject(project.id);
      useStore.setState({ project: p });
      setToast("🎵 Music suggested from the video's tones — drag to adjust");
      await api.rebuildPreview(project.id);
    } catch (e: any) { setToast(e.message); }
  };

  const createView = drag?.kind === "create"
    ? { a: Math.min(drag.anchor, drag.cur), b: Math.max(drag.anchor, drag.cur) } : null;
  const short = (f: string) => f.replace(/\.[^.]+$/, "");

  return (
    <>
      <div className="track-label" style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <span>music</span>
        <span className="music-controls" onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}>
          <label style={{ margin: 0, display: "inline" }}>
            <input type="checkbox" style={{ width: "auto" }} checked={!!music.enabled}
                   onChange={(e) => updateMusic({ enabled: e.target.checked })} /> on
          </label>
          <select value={selTrack} onChange={(e) => setSelTrack(e.target.value)}
                  style={{ width: 130, padding: "1px 4px", fontSize: 10 }}>
            {tracks.length === 0 && <option value="">no tracks — Library → Music</option>}
            {tracks.map((t) => <option key={t.name} value={t.name}>{short(t.name)}</option>)}
          </select>
          vol <input type="range" min={-35} max={-12} step={1}
                     value={vol ?? music.volume_db}
                     onChange={(e) => setVol(Number(e.target.value))}
                     onMouseUp={() => { if (vol != null) { updateMusic({ volume_db: vol }); setVol(null); } }} />
          <button className="ghost" style={{ padding: "0 6px" }} onClick={suggest}
                  title="Match your mood-tagged tracks to the video's tone sections. Only fills the lane — everything stays editable.">
            ✨ Suggest
          </button>
        </span>
      </div>
      <div className={`track music-lane ${music.enabled ? "" : "disabled"}`}
           ref={laneRef} style={{ height: tall ? 52 : 30 }}
           onMouseDown={onLaneDown} onMouseMove={onMove}
           onMouseUp={onUp} onMouseLeave={() => setDrag(null)}
           onClick={(e) => e.stopPropagation()}>
        <div className="tl-inner">
          {createView && (
            <div className="mreg ghost-reg"
                 style={{ left: createView.a * PX_PER_S,
                          width: (createView.b - createView.a) * PX_PER_S }} />
          )}
          {view.map((r) => (
            <div key={r.id} className="mreg"
                 style={{ left: r.start_s * PX_PER_S,
                          width: Math.max(10, (r.end_s - r.start_s) * PX_PER_S) }}
                 onMouseDown={(e) => onRegionDown(e, r)}
                 title={`${r.file} · ${r.start_s.toFixed(1)}–${r.end_s.toFixed(1)}s (drag to move, edges to resize)`}>
              <span className="mreg-label">♪ {short(r.file)}</span>
              <button className="mreg-x" title="remove"
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => { e.stopPropagation(); commit(regions.filter((x) => x.id !== r.id)); }}>
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function Wave({ width }: { width: number }) {
  const { waveform } = useStore();
  const ref = useRef<HTMLCanvasElement>(null);
  const [themeTick, setThemeTick] = React.useState(0);
  useEffect(() => {
    // Repaint when the theme flips — canvas colors don't track CSS vars.
    const obs = new MutationObserver(() => setThemeTick((t) => t + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr; c.height = 46 * dpr;
    const ctx = c.getContext("2d")!;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, 46);
    if (!waveform || !waveform.peaks.length) return;
    const peaks = waveform.peaks;
    const bw = width / peaks.length;
    ctx.fillStyle = getComputedStyle(document.documentElement)
      .getPropertyValue("--wave").trim() || "rgba(128,128,140,0.4)";
    peaks.forEach((p, i) => {
      const h = Math.max(1, p * 42);
      ctx.fillRect(i * bw, 23 - h / 2, Math.max(0.5, bw - 0.5), h);
    });
  }, [waveform, width, themeTick]);
  return <canvas ref={ref} className="wave" style={{ width }} />;
}
