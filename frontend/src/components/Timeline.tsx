import React, { useEffect, useRef } from "react";
import { api } from "../api";
import { useStore } from "../store";

const PX_PER_S = 60;

export function Timeline() {
  const { edl, beats, project, selectedEventId, select, playheadS, seek } = useStore();
  const dur = project?.duration_s || (edl ? Math.max(...edl.events.map((e) => e.end_s)) : 0);
  const width = Math.max(600, dur * PX_PER_S);

  if (!edl) return null;

  const seekAt = (e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    seek((e.clientX - rect.left) / PX_PER_S);
  };

  return (
    <div style={{ minWidth: width, position: "relative" }} onClick={seekAt}>
      <div className="playhead" style={{ left: playheadS * PX_PER_S }} />
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
      <div className="track">
        <div className="tl-inner">
          {edl.events.map((e) => {
            const cls = e.flags?.includes("gap_unfilled") ? "gap"
              : e.kind === "caption_card" ? "card" : "clip";
            const rev = e.flags?.includes("needs_review") || e.flags?.includes("close_call");
            // Label with what actually PLAYS (tournament winner), not the query.
            const winner = (e as any).finalists?.find((f: any) => f.asset_id === e.asset_id);
            const label = e.kind === "caption_card"
              ? (e.caption?.text || "card")
              : (winner?.title || e.queries?.[0] || e.kind);
            return (
              <div key={e.id}
                   className={`evt ${cls} ${selectedEventId === e.id ? "sel" : ""}`}
                   style={{ left: e.start_s * PX_PER_S,
                            width: Math.max(8, (e.end_s - e.start_s) * PX_PER_S) }}
                   onClick={() => select(e.id)}
                   title={`${e.kind} · ${label}`}>
                {e.asset_id && project && (
                  <img loading="lazy" alt=""
                       src={api.mediaUrl(`/projects/${project.id}/thumb/${e.id}`)}
                       onError={(ev) => ((ev.target as HTMLElement).style.display = "none")} />
                )}
                {rev && <span className="rev">⚑</span>}
                <span className="evt-label">{label}</span>
              </div>
            );
          })}
        </div>
      </div>
      {/* Caption track */}
      <div className="track-label">captions</div>
      <div className="track" style={{ height: 24 }}>
        <div className="tl-inner">
          {edl.events.filter((e) => e.caption?.enabled && e.caption?.text).map((e) => (
            <div key={e.id} className="capline"
                 style={{ left: e.start_s * PX_PER_S,
                          width: Math.max(8, (e.end_s - e.start_s) * PX_PER_S) }}
                 onClick={() => select(e.id)}>
              {e.caption.text}
            </div>
          ))}
        </div>
      </div>
    </div>
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
