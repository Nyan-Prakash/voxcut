import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { Timeline } from "./Timeline";
import { Inspector } from "./Inspector";

export function Editor() {
  const { project, edl, words } = useStore();
  const gen = async () => {
    if (!project) return;
    await api.generate(project.id);
    useStore.getState().setToast("Generating… watch the progress bar");
  };

  if (!project) return null;
  const hasTranscript = words.length > 0;

  return (
    <div className="editor" style={{ height: "100%" }}>
      <div className="editor-top">
        <Preview />
        <Inspector />
      </div>
      <div className="timeline-wrap">
        <div className="row" style={{ marginBottom: 6 }}>
          {!edl && (
            <button onClick={gen} disabled={!hasTranscript}>
              {hasTranscript ? "⚡ Generate edit" : "Waiting for transcript…"}
            </button>
          )}
          {edl && <EditorToolbar />}
        </div>
        {edl ? <Timeline /> : (
          <div className="muted" style={{ padding: 20 }}>
            {hasTranscript
              ? "Transcript ready. Click Generate edit to build the first cut."
              : "Transcribing your voiceover… the timeline appears when it's done."}
          </div>
        )}
      </div>
    </div>
  );
}

function EditorToolbar() {
  const { project, undo, refreshEdl } = useStore();
  return (
    <div className="row">
      <button className="sec" onClick={async () => {
        await api.generate(project!.id);
        useStore.getState().setToast("Regenerating…");
      }}>↻ Regenerate all</button>
      <button className="sec" onClick={async () => {
        await api.rebuildPreview(project!.id);
        useStore.getState().setToast("Rebuilding preview…");
      }}>▶ Rebuild preview</button>
      <button className="ghost" onClick={() => undo()}>↶ undo</button>
      <button className="ghost" onClick={() => refreshEdl()}>refresh</button>
      <ReviewNav />
      <div className="spacer" />
      <ExportButton />
    </div>
  );
}

function ReviewNav() {
  const { edl, select } = useStore();
  const jump = () => {
    if (!edl) return;
    const flagged = edl.events.filter((e) =>
      e.flags?.includes("needs_review") || e.flags?.includes("gap_unfilled"));
    if (flagged.length) {
      const cur = useStore.getState().selectedEventId;
      const idx = flagged.findIndex((e) => e.id === cur);
      select(flagged[(idx + 1) % flagged.length].id);
    } else {
      useStore.getState().setToast("Nothing flagged for review 🎉");
    }
  };
  const count = edl?.events.filter((e) =>
    e.flags?.includes("needs_review") || e.flags?.includes("gap_unfilled")).length || 0;
  return <button className="ghost" onClick={jump}>⚑ Review ({count})</button>;
}

function ExportButton() {
  const { project } = useStore();
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      await api.exportProject(project!.id, "1080p");
      useStore.getState().setToast("Exporting 1080p… download appears when ready");
    } catch (e: any) { useStore.getState().setToast(e.message); }
    finally { setBusy(false); }
  };
  return <button onClick={go} disabled={busy}>⬇ Export 1080p</button>;
}

function Preview() {
  const { project, previewNonce, edl, registerVideo, setPlayhead } = useStore();
  const ref = useRef<HTMLVideoElement>(null);
  const [ok, setOk] = useState(true);

  useEffect(() => {
    registerVideo(ref.current);
    return () => registerVideo(null);
  }, [ref.current]);

  useEffect(() => {
    setOk(true);
    if (ref.current) ref.current.load();
  }, [previewNonce, edl?.version]);

  if (!project) return null;
  const src = `${api.previewUrl(project.id)}&n=${previewNonce}`;

  return (
    <div className="preview-pane">
      {ok ? (
        <>
          <video
            ref={ref}
            controls
            style={{ flex: 1, minHeight: 0 }}
            onError={() => setOk(false)}
            onTimeUpdate={(e) => setPlayhead((e.target as HTMLVideoElement).currentTime)}
          >
            <source src={src} type="video/mp4" />
          </video>
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={() => {
              const v = ref.current;
              if (!v) return;
              v.paused ? v.play() : v.pause();
            }}>⏯ Play / Pause whole video</button>
            <span className="muted">plays the full stitched timeline</span>
          </div>
        </>
      ) : (
        <div style={{ textAlign: "center" }}>
          <div className="muted">No preview rendered yet.</div>
          <button className="sec" style={{ marginTop: 10 }} onClick={async () => {
            await api.rebuildPreview(project.id);
            useStore.getState().setToast("Building preview…");
          }}>Build preview</button>
        </div>
      )}
    </div>
  );
}
