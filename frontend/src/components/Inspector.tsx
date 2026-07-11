import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { EdlEvent } from "../types";

export function Inspector() {
  const { edl, selectedEventId, applyOps } = useStore();
  const ev = edl?.events.find((e) => e.id === selectedEventId) || null;

  if (!ev) {
    return <div className="inspector"><div className="muted">
      Select a clip in the timeline to edit it.
    </div></div>;
  }
  return <InspectorBody key={ev.id} ev={ev} applyOps={applyOps} />;
}

function InspectorBody({ ev, applyOps }: { ev: EdlEvent; applyOps: (ops: any[]) => Promise<void> }) {
  const { project } = useStore();
  const [caption, setCaption] = useState(ev.caption?.text || "");
  const [cands, setCands] = useState<any>(null);
  const [query, setQuery] = useState(ev.queries?.[0] || "");

  useEffect(() => {
    if (!project) return;
    api.candidates(project.id, ev.id).then(setCands).catch(() => setCands(null));
  }, [ev.id, project?.id]);

  const dur = (ev.end_s - ev.start_s).toFixed(2);

  return (
    <div className="inspector">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>{ev.kind}</h2>
        <span className="muted">{ev.start_s.toFixed(2)}–{ev.end_s.toFixed(2)}s ({dur}s)</span>
      </div>
      {ev.flags?.length > 0 && (
        <div className="flags">
          {ev.flags.map((f) => <span key={f} className={`flag ${f.split(":")[0]}`}>{f}</span>)}
        </div>
      )}

      <label>Type</label>
      <select value={ev.kind} onChange={(e) => applyOps([{ op: "set_kind", event_id: ev.id, data: { kind: e.target.value } }])}>
        {["clip_literal", "clip_reaction", "meme_image", "broll", "caption_card"].map((k) =>
          <option key={k} value={k}>{k}</option>)}
      </select>

      <label>Caption</label>
      <input value={caption} onChange={(e) => setCaption(e.target.value)}
             onBlur={() => applyOps([{ op: "set_caption", event_id: ev.id,
               data: { text: caption, enabled: caption.length > 0 } }])} />
      <div className="row" style={{ marginTop: 6 }}>
        <select value={ev.caption?.style || "subtitle"} style={{ flex: 1 }}
                onChange={(e) => applyOps([{ op: "set_caption", event_id: ev.id, data: { style: e.target.value } }])}>
          {["meme_top", "meme_bottom", "subtitle", "label", "card"].map((s) =>
            <option key={s} value={s}>{s}</option>)}
        </select>
        <label style={{ margin: 0 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={!!ev.caption?.enabled}
                 onChange={(e) => applyOps([{ op: "set_caption", event_id: ev.id, data: { enabled: e.target.checked } }])} /> show
        </label>
      </div>

      <label>Source audio</label>
      <div className="seg">
        {["mute", "duck", "keep"].map((m) => (
          <button key={m} className={ev.audio?.mode === m ? "active" : "sec"}
                  onClick={() => applyOps([{ op: "set_audio", event_id: ev.id, data: { mode: m } }])}>{m}</button>
        ))}
      </div>

      {/* Tournament finalists — both angles fully verified, one click to swap */}
      {cands?.finalists?.length > 1 && (
        <>
          <label>Finalists (both verified — click to swap)</label>
          <div className="cand-strip">
            {cands.finalists.map((f: any, i: number) => {
              const chosen = f.asset_id === ev.asset_id;
              return (
                <div key={i} className={`cand ${chosen ? "chosen" : ""}`} title={f.title}
                     onClick={async () => {
                       if (chosen) return;
                       await api.pickFinalist(project!.id, ev.id, f.asset_id, f.in_s, f.out_s);
                       await api.rebuildPreview(project!.id);
                       useStore.getState().refreshEdl();
                     }}>
                  <div>{chosen ? "✓ " : ""}{(f.title || "clip").slice(0, 38)}</div>
                  <div className="muted">
                    vision {f.visual?.toFixed?.(2) ?? "—"} · {f.in_s?.toFixed?.(1)}s→{f.out_s?.toFixed?.(1)}s
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Candidate moments within the chosen source */}
      {cands?.moment_candidates?.length > 0 && (
        <>
          <label>Moment in this clip (pick the best seconds)</label>
          <div className="cand-strip">
            {cands.moment_candidates.map((m: any, i: number) => {
              const chosen = ev.source && Math.abs(ev.source.in_s - m.in_s) < 0.05;
              return (
                <div key={i} className={`cand ${chosen ? "chosen" : ""}`}
                     onClick={async () => {
                       await api.pickMoment(project!.id, ev.id, m.in_s, m.out_s);
                       await api.rebuildPreview(project!.id);
                       useStore.getState().refreshEdl();
                     }}>
                  <div>#{i + 1} · score {m.score?.toFixed?.(2)}</div>
                  <div className="muted">{m.in_s.toFixed(1)}s → {m.out_s.toFixed(1)}s</div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Alternate sources */}
      {cands?.source_candidates?.length > 0 && (
        <>
          <label>Other sources</label>
          <div className="cand-strip">
            {cands.source_candidates.slice(0, 4).map((c: any, i: number) => (
              <div key={i} className="cand" title={c.title}
                   onClick={() => reSource(project!.id, ev.id, c.title)}>
                {c.thumbnail && <img className="thumb" src={c.thumbnail} />}
                <div style={{ marginTop: 4, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {c.title?.slice(0, 40)}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <label>Search again (find new footage)</label>
      <div className="row">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="youtube search…" />
        <button className="sec" onClick={() => reSource(project!.id, ev.id, query)}>Go</button>
      </div>

      <div className="row" style={{ marginTop: 16 }}>
        <button className="ghost" onClick={() => applyOps([{ op: "lock", event_id: ev.id, data: { locked: !ev.locked } }])}>
          {ev.locked ? "🔒 locked" : "🔓 lock"}
        </button>
        <button className="ghost" style={{ color: "var(--bad)" }}
                onClick={() => { applyOps([{ op: "delete", event_id: ev.id }]); useStore.getState().select(null); }}>
          Delete
        </button>
      </div>
    </div>
  );
}

async function reSource(pid: string, evId: string, query: string) {
  if (!query.trim()) return;
  await api.research(pid, evId, query);
  useStore.getState().setToast("Searching for new footage…");
}
