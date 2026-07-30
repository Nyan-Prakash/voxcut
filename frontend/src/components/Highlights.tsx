import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { HighlightClip } from "../types";

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${Math.floor(s % 60).toString().padStart(2, "0")}`;
}

export function HighlightsPanel() {
  const { project, highlightsNonce, seek } = useStore();
  const [clips, setClips] = useState<HighlightClip[]>([]);
  const [analyzed, setAnalyzed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!project) return;
    api.getHighlights(project.id)
      .then((r) => { setClips(r.clips); setAnalyzed(r.analyzed); })
      .catch(() => {});
  }, [project?.id, highlightsNonce]);

  if (!project) return null;

  const analyze = async () => {
    setBusy(true);
    try {
      await api.analyzeHighlights(project.id);
      useStore.getState().setToast("📱 Scouting the edit for TikTok-worthy clips…");
    } catch (e: any) { useStore.getState().setToast(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="inspector" style={{ overflowY: "auto" }}>
      <h2>📱 TikTok clips</h2>
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        The AI watches the finished edit and picks hook-first, self-contained
        moments that stand alone as TikToks. Export cuts a 9:16 vertical —
        full video centered over a blurred backdrop, final audio mix.
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={analyze} disabled={busy}>
          {analyzed ? "↻ Re-scout clips" : "🔍 Find TikTok clips"}
        </button>
      </div>
      {analyzed && clips.length === 0 && (
        <div className="muted" style={{ marginTop: 14, fontSize: 12 }}>
          Nothing stood alone as a clip — nothing scored above the bar.
          Re-scout after editing, or lower your expectations manually with ✂.
        </div>
      )}
      {clips.map((c) => (
        <ClipCard key={c.index} clip={c} onWatch={() => seek(c.start_s)} />
      ))}
    </div>
  );
}

function ClipCard({ clip, onWatch }: { clip: HighlightClip; onWatch: () => void }) {
  const { project } = useStore();
  const [busy, setBusy] = useState(false);

  const doExport = async () => {
    setBusy(true);
    try {
      await api.exportHighlight(project!.id, clip.index);
      useStore.getState().setToast("Cutting 9:16 vertical… download appears here when ready");
    } catch (e: any) { useStore.getState().setToast(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="card" style={{ marginTop: 10, padding: 12 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong style={{ fontSize: 13 }}>{clip.title || "Untitled clip"}</strong>
        <span className="chip" title="TikTok-worthiness score">
          {Math.round(clip.score * 100)}%
        </span>
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        {fmtTime(clip.start_s)}–{fmtTime(clip.end_s)} · {Math.round(clip.duration_s)}s
      </div>
      {clip.hook && (
        <div style={{ fontSize: 12, marginTop: 6 }}>
          🪝 “{clip.hook}”
        </div>
      )}
      {clip.reason && (
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>{clip.reason}</div>
      )}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="sec" onClick={onWatch} title="Jump the preview to this clip">
          ▶ Watch
        </button>
        <button className="sec" onClick={doExport} disabled={busy}
                title="Cut this range from the full-quality export into a 1080x1920 vertical — full video centered, blurred backdrop above and below">
          ⬇ Export 9:16
        </button>
        {clip.exported && (
          <a href={api.highlightDownloadUrl(project!.id, clip.index)} download>
            <button>💾 Download</button>
          </a>
        )}
      </div>
    </div>
  );
}
