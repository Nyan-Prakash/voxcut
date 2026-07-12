import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";

function fmtBytes(n: number): string {
  if (n < 1e6) return `${(n / 1e3).toFixed(0)} KB`;
  if (n < 1e9) return `${(n / 1e6).toFixed(0)} MB`;
  return `${(n / 1e9).toFixed(2)} GB`;
}

function MusicSection() {
  const setToast = useStore((s) => s.setToast);
  const [data, setData] = useState<{ tracks: any[]; moods: string[] }>({ tracks: [], moods: [] });

  const load = () => api.musicList().then(setData).catch(() => {});
  useEffect(() => { load(); }, []);

  const upload = async (f: File) => {
    setToast("Adding track…");
    try { await api.musicUpload(f); setToast("Track added — tag its mood"); load(); }
    catch (e: any) { setToast(e.message); }
  };

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", marginTop: 28 }}>
        <h1>Music</h1>
        <label className="sec" style={{ padding: "8px 14px", borderRadius: 7, cursor: "pointer", width: "auto", margin: 0, color: "var(--text)" }}>
          Upload track
          <input type="file" accept="audio/*" style={{ display: "none" }}
                 onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>
      </div>
      <div className="muted" style={{ fontSize: 12, margin: "4px 0 10px" }}>
        Your tracks only — VOXCUT never bundles or fetches music. Tag each with a
        mood so “Suggest” can match them to the video’s tone.
      </div>
      <div className="grid">
        {data.tracks.map((t) => (
          <div key={t.name} className="card" style={{ margin: 0 }}>
            <strong style={{ fontSize: 13 }}>♪ {t.name}</strong>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              {t.duration_s?.toFixed(0)}s · {fmtBytes(t.size_bytes)}
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <select value={t.mood || ""} style={{ flex: 1 }}
                      onChange={async (e) => { await api.musicMood(t.name, e.target.value || null); load(); }}>
                <option value="">mood…</option>
                {data.moods.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <button className="ghost" style={{ color: "var(--bad)" }}
                      onClick={async () => { await api.musicDelete(t.name); load(); }}>✕</button>
            </div>
          </div>
        ))}
        {data.tracks.length === 0 && (
          <div className="muted">No tracks yet. Upload royalty-free music you own the rights to use.</div>
        )}
      </div>
    </>
  );
}

export function LibraryView() {
  const setToast = useStore((s) => s.setToast);
  const [data, setData] = useState<{ assets: any[]; disk_usage_bytes: number }>({ assets: [], disk_usage_bytes: 0 });
  const [q, setQ] = useState("");

  const load = () => api.libraryList(q).then(setData).catch(() => {});
  useEffect(() => { load(); }, []);

  const upload = async (f: File) => {
    setToast("Adding to library…");
    await api.libraryUpload(f);
    setToast("Added");
    load();
  };

  return (
    <div className="center">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Asset Library</h1>
        <span className="muted">{fmtBytes(data.disk_usage_bytes)} on disk</span>
      </div>
      <div className="row" style={{ margin: "10px 0" }}>
        <input placeholder="Search title / queries…" value={q}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && load()} style={{ maxWidth: 320 }} />
        <button className="sec" onClick={load}>Search</button>
        <label className="sec" style={{ padding: "8px 14px", borderRadius: 7, cursor: "pointer", width: "auto", margin: 0, color: "var(--text)" }}>
          Upload media
          <input type="file" accept="video/*,image/*" style={{ display: "none" }}
                 onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>
        <div className="spacer" />
        <button className="ghost" onClick={async () => { const r = await api.libraryPrune(undefined, 30); setToast(`Pruned ${r.count} assets`); load(); }}>
          Prune unused &gt;30d
        </button>
      </div>
      <div className="grid">
        {data.assets.map((a) => (
          <div key={a.id} className="card" style={{ margin: 0 }}>
            <strong style={{ fontSize: 13 }}>{a.title || a.source_id}</strong>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              {a.provider} · {a.duration_s?.toFixed(0)}s · {a.width}×{a.height} · {fmtBytes(a.size_bytes)}
            </div>
            <div className="flags">
              {a.has_subs && <span className="flag">subs</span>}
              {a.has_heatmap && <span className="flag">heatmap</span>}
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="ghost" onClick={async () => { await api.libraryPin(a.id, !a.pinned); load(); }}>
                {a.pinned ? "📌 pinned" : "pin"}
              </button>
            </div>
          </div>
        ))}
        {data.assets.length === 0 && <div className="muted">Library is empty. Clips download here as you generate.</div>}
      </div>
      <MusicSection />
    </div>
  );
}
