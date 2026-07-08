import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";

function FirstRun() {
  const setView = useStore((s) => s.setView);
  const [show, setShow] = useState(false);
  useEffect(() => {
    api.system().then((s) => setShow(!s.brain_ready)).catch(() => {});
  }, []);
  if (!show) return null;
  return (
    <div className="card" style={{ borderColor: "var(--accent)" }}>
      <h2>👋 Welcome to VOXCUT</h2>
      <div className="muted">
        VOXCUT turns a voiceover into a fast-cut commentary edit. For the smartest
        beat segmentation and edit planning, add an OpenAI API key in Settings —
        without one, VOXCUT still works using a heuristic segmenter.
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button onClick={() => setView("settings")}>Add OpenAI key</button>
        <span className="muted">…or just create a project and try it now.</span>
      </div>
    </div>
  );
}

export function ProjectsView() {
  const { projects, loadProjects, openProject } = useStore();
  const [creating, setCreating] = useState(false);

  return (
    <div className="center">
      <FirstRun />
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Projects</h1>
        <button onClick={() => setCreating(true)}>+ New project</button>
      </div>
      {creating && <NewProject onDone={() => { setCreating(false); loadProjects(); }} />}
      <div className="grid" style={{ marginTop: 18 }}>
        {projects.map((p) => (
          <div key={p.id} className="card projtile" onClick={() => openProject(p.id)}>
            <strong>{p.name}</strong>
            <div className="muted" style={{ marginTop: 6 }}>
              {p.status} · {p.duration_s ? `${p.duration_s.toFixed(0)}s` : "no audio"}
            </div>
          </div>
        ))}
        {projects.length === 0 && !creating && (
          <div className="muted">No projects yet. Create one to start.</div>
        )}
      </div>
    </div>
  );
}

function NewProject({ onDone }: { onDone: () => void }) {
  const { openProject } = useStore();
  const [name, setName] = useState("Untitled video");
  const [subject, setSubject] = useState("");
  const [notes, setNotes] = useState("");
  const [aspect, setAspect] = useState("16:9");
  const [density, setDensity] = useState("normal");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    try {
      const p = await api.createProject(
        name,
        { subject, notes, aspect, tone: "infer" },
        { aspect, cut_density: density }
      );
      if (file) await api.uploadVoiceover(p.id, file);
      await openProject(p.id);
      onDone();
    } catch (e: any) {
      useStore.getState().setToast("Error: " + e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h2>New project</h2>
      <label>Name</label>
      <input value={name} onChange={(e) => setName(e.target.value)} />
      <label>Subject / what's this about?</label>
      <input value={subject} onChange={(e) => setSubject(e.target.value)}
             placeholder="e.g. the Kevin chili incident" />
      <label>Notes (any context, named references, avoid-list…)</label>
      <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
      <div className="row">
        <div style={{ flex: 1 }}>
          <label>Aspect</label>
          <select value={aspect} onChange={(e) => setAspect(e.target.value)}>
            <option value="16:9">16:9 (landscape)</option>
            <option value="9:16">9:16 (shorts)</option>
          </select>
        </div>
        <div style={{ flex: 1 }}>
          <label>Cut density</label>
          <select value={density} onChange={(e) => setDensity(e.target.value)}>
            <option value="chill">Chill</option>
            <option value="normal">Normal</option>
            <option value="hyperactive">Hyperactive</option>
          </select>
        </div>
      </div>
      <label>Voiceover audio</label>
      <input type="file" accept="audio/*,video/*"
             onChange={(e) => setFile(e.target.files?.[0] || null)} />
      <div className="row" style={{ marginTop: 14 }}>
        <button onClick={create} disabled={busy || !name}>
          {busy ? "Creating…" : "Create & transcribe"}
        </button>
        <button className="ghost" onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}
