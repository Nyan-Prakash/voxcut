import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { MusicTimeline, Timeline } from "./Timeline";
import { Inspector } from "./Inspector";
import { MusicSection } from "./Library";
import { HighlightsPanel } from "./Highlights";
import { ExportModal } from "./ExportModal";

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
  if (stage === "tiktok" && edl) return <TikTokStage />;

  return (
    <div className="editor" style={{ height: "100%" }}>
      <div className="editor-top">
        <Preview />
        <Inspector />
      </div>
      <div className="timeline-wrap">
        <div className="tl-toolbar">
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
        <div className="tl-toolbar">
          <button className="sec" onClick={() => setStage("clips")}>← Back to clips</button>
          <div className="tl-divider" />
          <button className="sec" onClick={async () => {
            await api.rebuildPreview(project!.id);
            useStore.getState().setToast("Rebuilding preview with music…");
          }}>▶ Rebuild preview</button>
          <div className="spacer" />
          <TimeReadout />
          <ExportButton />
        </div>
        <MusicTimeline />
      </div>
    </div>
  );
}

function TikTokStage() {
  const { setStage } = useStore();
  return (
    <div className="editor" style={{ height: "100%" }}>
      <div className="editor-top">
        <Preview />
        <HighlightsPanel />
      </div>
      <div className="timeline-wrap">
        <div className="tl-toolbar">
          <button className="sec" onClick={() => setStage("clips")}>← Back to clips</button>
          <div className="spacer" />
          <TimeReadout />
          <ExportButton />
        </div>
        <Timeline />
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
    { id: "interject", label: "⚡ Interject",
      hint: "click the timeline to cut the voiceover and drop in an unmuted clip whose audio is the joke" },
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
    <>
      <ToolSwitch />
      <div className="tl-divider" />
      <button className="ghost" title="Undo the last edit" onClick={() => undo()}>↶ Undo</button>
      <ReviewNav />
      <button className="ghost" title="Vision-audit every clip against the never-mediocre law; weak ones get flagged ⚑ for you to reroll"
              onClick={async () => {
                await api.runQc(project!.id);
                useStore.getState().setToast("🔎 QC: auditing clips against the never-mediocre law…");
              }}>🔎 QC</button>
      <div className="tl-divider" />
      <button className="ghost" title="Throw away this cut and generate a fresh one"
              onClick={async () => {
        await api.generate(project!.id);
        useStore.getState().setToast("Regenerating…");
      }}>↻ Regenerate</button>
      <button className="ghost" title="Re-render the stitched preview video"
              onClick={async () => {
        await api.rebuildPreview(project!.id);
        useStore.getState().setToast("Rebuilding preview…");
      }}>▶ Rebuild</button>
      <button className="ghost" title="Re-fetch the timeline from the server"
              onClick={() => refreshEdl()}>⟳ Refresh</button>
      <div className="spacer" />
      <TimeReadout />
      <ExportButton />
      <button onClick={() => useStore.getState().setStage("music")}
              title="Happy with the clips? Move on to scoring the video with music.">
        ✓ Accept → Music
      </button>
    </>
  );
}

/** Live playhead readout — isolated so per-frame time updates only
 *  re-render this pill, not the whole toolbar. */
function TimeReadout() {
  const playheadS = useStore((s) => s.playheadS);
  const dur = useStore((s) =>
    s.project?.duration_s
    || (s.edl?.events.length ? Math.max(...s.edl.events.map((e) => e.end_s)) : 0));
  const fmt = (t: number) =>
    `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
  return <span className="tl-time">{fmt(playheadS)} <em>/ {fmt(dur)}</em></span>;
}

function ReviewNav() {
  const { edl, select } = useStore();
  const FLAGS = ["needs_review", "gap_unfilled", "close_call", "qc_middle",
                 "cold_open_weak"];
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
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}
              title="Export the full video or AI-scouted short-form verticals">
        ⬇ Export
      </button>
      {open && <ExportModal onClose={() => setOpen(false)} />}
    </>
  );
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
