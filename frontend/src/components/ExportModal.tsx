import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import { useStore } from "../store";
import type { HighlightClip } from "../types";

const RESOLUTIONS: Array<{ id: string; label: string; dims169: string; dims916: string }> = [
  { id: "720p", label: "720p", dims169: "1280×720", dims916: "720×1280" },
  { id: "1080p", label: "1080p", dims169: "1920×1080", dims916: "1080×1920" },
  { id: "4k", label: "4K", dims169: "3840×2160", dims916: "2160×3840" },
];

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${Math.floor(s % 60).toString().padStart(2, "0")}`;
}

export function ExportModal({ onClose }: { onClose: () => void }) {
  const { project, edl, highlightsNonce, exportNonce, setStage } = useStore();
  const [resolution, setResolution] = useState("1080p");
  const [ready, setReady] = useState(false);
  const [clips, setClips] = useState<HighlightClip[]>([]);
  const [analyzed, setAnalyzed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.getSettings()
      .then((s) => s.export_resolution && setResolution(s.export_resolution))
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (!project) return;
    api.exportStatus(project.id).then((r) => setReady(r.ready)).catch(() => {});
  }, [project?.id, exportNonce]);
  useEffect(() => {
    if (!project) return;
    api.getHighlights(project.id)
      .then((r) => { setClips(r.clips); setAnalyzed(r.analyzed); })
      .catch(() => {});
  }, [project?.id, highlightsNonce]);

  if (!project) return null;
  const vertical = edl?.aspect === "9:16";

  const doExport = async () => {
    setBusy(true);
    try {
      api.putSettings({ export_resolution: resolution }).catch(() => {});
      await api.exportProject(project.id, resolution);
      useStore.getState().setToast(`Exporting ${resolution}… download appears when ready`);
    } catch (e: any) { useStore.getState().setToast(e.message); }
    finally { setBusy(false); }
  };

  const scout = async () => {
    try {
      await api.analyzeHighlights(project.id);
      useStore.getState().setToast("📱 Scouting the edit for TikTok-worthy clips…");
    } catch (e: any) { useStore.getState().setToast(e.message); }
  };

  const exportAll = async () => {
    try {
      for (const c of clips.filter((c) => !c.exported)) {
        await api.exportHighlight(project.id, c.index);
      }
      useStore.getState().setToast(`Cutting ${clips.filter((c) => !c.exported).length} vertical clips…`);
    } catch (e: any) { useStore.getState().setToast(e.message); }
  };

  // Portal: ancestors use backdrop-filter, which hijacks position:fixed.
  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Export</h2>
          <button className="ghost" onClick={onClose} title="Close">✕</button>
        </div>

        <div className="modal-section">
          <strong style={{ fontSize: 13 }}>🎬 Full video</strong>
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            The whole timeline, full quality, loudness-normalized for YouTube.
          </div>
          <label>Resolution</label>
          <div className="seg" role="radiogroup" style={{ maxWidth: 340 }}>
            {RESOLUTIONS.map((r) => (
              <button key={r.id}
                      className={resolution === r.id ? "active" : "sec"}
                      title={vertical ? r.dims916 : r.dims169}
                      onClick={() => setResolution(r.id)}>
                {r.label}
              </button>
            ))}
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            {(RESOLUTIONS.find((r) => r.id === resolution) || RESOLUTIONS[1])[vertical ? "dims916" : "dims169"]}
            {" · "}{edl?.aspect || "16:9"}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button onClick={doExport} disabled={busy}>⬇ Export {RESOLUTIONS.find((r) => r.id === resolution)?.label}</button>
            {ready && (
              <a href={api.exportUrl(project.id)} download>
                <button className="sec">💾 Download last export</button>
              </a>
            )}
          </div>
        </div>

        <div className="modal-section">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong style={{ fontSize: 13 }}>📱 Short-form (9:16 verticals)</strong>
            {analyzed && (
              <button className="ghost" onClick={scout} title="Run the AI scout again">↻ Re-scout</button>
            )}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            AI-scouted TikTok/Shorts moments, cut from the full-quality master
            into 1080×1920 verticals — full video centered, blurred backdrop
            filling the top and bottom.
          </div>
          {!analyzed && (
            <div className="row" style={{ marginTop: 10 }}>
              <button className="sec" onClick={scout}>🔍 Find TikTok clips</button>
            </div>
          )}
          {analyzed && clips.length === 0 && (
            <div className="muted" style={{ marginTop: 10, fontSize: 12 }}>
              Nothing scored above the bar. Re-scout after editing.
            </div>
          )}
          {clips.map((c) => (
            <ClipRow key={c.index} clip={c} />
          ))}
          {clips.length > 1 && clips.some((c) => !c.exported) && (
            <div className="row" style={{ marginTop: 10 }}>
              <button className="sec" onClick={exportAll}>⬇ Export all</button>
            </div>
          )}
          {clips.length > 0 && (
            <div className="row" style={{ marginTop: 8 }}>
              <button className="ghost" onClick={() => { setStage("tiktok"); onClose(); }}>
                Open the TikTok panel for hooks & previews →
              </button>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function ClipRow({ clip }: { clip: HighlightClip }) {
  const { project } = useStore();
  const [busy, setBusy] = useState(false);
  const doExport = async () => {
    setBusy(true);
    try {
      await api.exportHighlight(project!.id, clip.index);
      useStore.getState().setToast("Cutting 9:16 vertical… it appears here when ready");
    } catch (e: any) { useStore.getState().setToast(e.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="clip-row">
      <div className="clip-meta">
        <strong>{clip.title || "Untitled clip"}</strong>
        <span className="muted" style={{ fontSize: 11 }}>
          {fmtTime(clip.start_s)}–{fmtTime(clip.end_s)} · {Math.round(clip.duration_s)}s
          · {Math.round(clip.score * 100)}%
        </span>
      </div>
      {clip.exported ? (
        <a href={api.highlightDownloadUrl(project!.id, clip.index)} download>
          <button className="sec">💾 Download</button>
        </a>
      ) : (
        <button className="sec" onClick={doExport} disabled={busy}>⬇ 9:16</button>
      )}
    </div>
  );
}
