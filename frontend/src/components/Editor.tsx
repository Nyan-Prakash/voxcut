import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { MusicTimeline, Timeline } from "./Timeline";
import { Inspector } from "./Inspector";
import { MusicSection } from "./Library";

export function Editor() {
  const { project, edl, words, stage } = useStore();
  const gen = async () => {
    if (!project) return;
    await api.generate(project.id);
    useStore.getState().setToast("Generating… watch the progress bar");
  };

  if (!project) return null;
  const hasTranscript = words.length > 0;

  if (stage === "music" && edl) return <MusicStage />;

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

function MusicStage() {
  const { project, setStage } = useStore();
  return (
    <div className="editor" style={{ height: "100%" }}>
      <div className="editor-top">
        <Preview />
        <div className="inspector">
          <h2>Music</h2>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            Score the accepted cut: drag on the lane below to place a track,
            or ✨ Suggest to match your tagged tracks to the video's tones.
          </div>
          <MusicSection compact />
        </div>
      </div>
      <div className="timeline-wrap">
        <div className="row" style={{ marginBottom: 6 }}>
          <button className="sec" onClick={() => setStage("clips")}>← Back to clips</button>
          <button className="sec" onClick={async () => {
            await api.rebuildPreview(project!.id);
            useStore.getState().setToast("Rebuilding preview with music…");
          }}>▶ Rebuild preview</button>
          <div className="spacer" />
          <ExportButton />
        </div>
        <MusicTimeline />
      </div>
    </div>
  );
}

function ToolSwitch() {
  const { tool, setTool } = useStore();
  const TOOLS: Array<{ id: typeof tool; label: string; hint: string }> = [
    { id: "select", label: "▣ Select", hint: "click clips to edit, click background to seek" },
    { id: "cut", label: "✂ Cut", hint: "click a clip to split it at that point (snaps to words)" },
    { id: "add", label: "＋ Segment", hint: "drag a range on the timeline to carve a new segment" },
  ];
  return (
    <div className="seg" role="toolbar">
      {TOOLS.map((t) => (
        <button key={t.id} title={t.hint}
                className={tool === t.id ? "active" : "sec"}
                onClick={() => setTool(t.id)}>{t.label}</button>
      ))}
    </div>
  );
}

function EditorToolbar() {
  const { project, undo, refreshEdl } = useStore();
  return (
    <div className="row">
      <ToolSwitch />
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
      <button className="ghost" title="Vision-audit every clip against the never-mediocre law; weak ones get flagged ⚑ for you to reroll"
              onClick={async () => {
                await api.runQc(project!.id);
                useStore.getState().setToast("🔎 QC: auditing clips against the never-mediocre law…");
              }}>🔎 QC</button>
      <div className="spacer" />
      <button onClick={() => useStore.getState().setStage("music")}
              title="Happy with the clips? Move on to scoring the video with music.">
        ✓ Accept clips → Music
      </button>
    </div>
  );
}

function ReviewNav() {
  const { edl, select } = useStore();
  const FLAGS = ["needs_review", "gap_unfilled", "close_call", "qc_middle"];
  const flagged = () => edl
    ? edl.events.filter((e) => e.flags?.some((f) => FLAGS.includes(f)))
    : [];
  const jump = () => {
    const list = flagged();
    if (list.length) {
      const cur = useStore.getState().selectedEventId;
      const idx = list.findIndex((e) => e.id === cur);
      select(list[(idx + 1) % list.length].id);
    } else {
      useStore.getState().setToast("Nothing flagged for review 🎉");
    }
  };
  return <button className="ghost" onClick={jump}>⚑ Review ({flagged().length})</button>;
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
