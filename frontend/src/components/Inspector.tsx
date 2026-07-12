import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { EdlEvent } from "../types";

export function Inspector() {
  const { edl, selectedEventId, selectedEventIds, applyOps } = useStore();

  if (selectedEventIds.length > 1) {
    return <MultiPanel key={selectedEventIds.join(",")} />;
  }
  const ev = edl?.events.find((e) => e.id === selectedEventId) || null;
  if (!ev) {
    return <div className="inspector"><div className="muted">
      Select a clip in the timeline to edit it.<br /><br />
      ✂ Cut tool: click a clip to split it.<br />
      ＋ Segment tool: drag a range to carve a new segment.<br />
      🎲 on any clip: regenerate just that clip.<br />
      ⌘/ctrl-click or shift-click: select several clips, reroll them together.
    </div></div>;
  }
  return <InspectorBody key={ev.id} ev={ev} applyOps={applyOps} />;
}

function MultiPanel() {
  const { edl, selectedEventIds, select, reroll } = useStore();
  const [hint, setHint] = useState("");
  const sel = (edl?.events || [])
    .filter((e) => selectedEventIds.includes(e.id))
    .sort((a, b) => a.start_s - b.start_s);
  const unlocked = sel.filter((e) => !e.locked);

  return (
    <div className="inspector">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>{sel.length} clips selected</h2>
        <button className="ghost" onClick={() => select(null)}>clear</button>
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        {sel.map((e) => (
          <div key={e.id} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {e.start_s.toFixed(1)}s · {e.finalists?.find((f: any) => f.asset_id === e.asset_id)?.title
              || e.queries?.[0] || e.kind}{e.locked ? " 🔒" : ""}
          </div>
        ))}
      </div>

      <label>Direction for the new clips (optional)</label>
      <input value={hint} onChange={(e) => setHint(e.target.value)}
             placeholder="e.g. make these anime moments…" />
      <button style={{ width: "100%", marginTop: 8 }} disabled={!unlocked.length}
              onClick={() => reroll(unlocked.map((e) => e.id), hint)}>
        🎲 Reroll {unlocked.length} clip{unlocked.length === 1 ? "" : "s"}
      </button>
      {unlocked.length < sel.length && (
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          locked clips are skipped
        </div>
      )}
    </div>
  );
}

function InspectorBody({ ev, applyOps }: { ev: EdlEvent; applyOps: (ops: any[]) => Promise<void> }) {
  const { project, reroll } = useStore();
  const [cands, setCands] = useState<any>(null);
  const [query, setQuery] = useState(ev.queries?.[0] || "");
  const [hint, setHint] = useState("");

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

      <label>Direction for the new clip (optional)</label>
      <input value={hint} onChange={(e) => setHint(e.target.value)}
             placeholder="e.g. show the SpongeBob version of this…"
             onKeyDown={(e) => { if (e.key === "Enter" && !ev.locked) reroll([ev.id], hint); }} />
      <button style={{ width: "100%", marginTop: 8 }} disabled={ev.locked}
              title="Re-plans this beat with a fresh comedic angle, re-runs the tournament, and never returns the same footage. Your direction (if any) steers the plan."
              onClick={() => reroll([ev.id], hint)}>
        🎲 Reroll this clip
      </button>

      <label>Type</label>
      <select value={ev.kind} onChange={(e) => applyOps([{ op: "set_kind", event_id: ev.id, data: { kind: e.target.value } }])}>
        {["clip_literal", "clip_reaction", "meme_image", "broll"].map((k) =>
          <option key={k} value={k}>{k}</option>)}
      </select>

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
                  <div>#{i + 1} · {m.visual != null
                    ? `vision ${m.visual.toFixed(2)}`
                    : `score ${m.score?.toFixed?.(2)}`}</div>
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

      <label>Search again (find new footage with your own query)</label>
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
