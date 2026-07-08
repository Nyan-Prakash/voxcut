import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";

export function SettingsView() {
  const setToast = useStore((s) => s.setToast);
  const [s, setS] = useState<Record<string, any>>({});
  const [key, setKey] = useState("");
  const [testing, setTesting] = useState(false);

  useEffect(() => { api.getSettings().then(setS); }, []);

  const save = async () => {
    const values: Record<string, string> = {
      openai_model: s.openai_model || "gpt-4o",
      transcription_quality: s.transcription_quality || "balanced",
      export_resolution: s.export_resolution || "1080p",
      download_cap_gb: String(s.download_cap_gb || "50"),
    };
    if (key) values.openai_api_key = key;
    await api.putSettings(values);
    setKey("");
    setToast("Settings saved");
    api.getSettings().then(setS);
  };

  const test = async () => {
    setTesting(true);
    try {
      const r = await api.testKey(key || undefined, s.openai_model);
      setToast(r.ok ? `✓ OpenAI key works (${r.model})` : `✗ ${r.error}`);
    } finally { setTesting(false); }
  };

  return (
    <div className="center">
      <h1>Settings</h1>
      <div className="card">
        <h2>OpenAI (the edit brain)</h2>
        <div className="muted">
          Used for beat segmentation and edit planning. Without a key, VOXCUT
          falls back to a heuristic segmenter (still produces a video).
        </div>
        <label>API key {s.openai_api_key_set && <span className="chip ok">set</span>}</label>
        <input type="password" value={key} onChange={(e) => setKey(e.target.value)}
               placeholder={s.openai_api_key_set ? "•••••• (leave blank to keep)" : "sk-…"} />
        <label>Model</label>
        <input value={s.openai_model || ""} onChange={(e) => setS({ ...s, openai_model: e.target.value })}
               placeholder="gpt-4o" />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="sec" onClick={test} disabled={testing}>
            {testing ? "Testing…" : "Test key"}
          </button>
        </div>
      </div>
      <div className="card">
        <h2>Transcription</h2>
        <label>Quality</label>
        <select value={s.transcription_quality || "balanced"}
                onChange={(e) => setS({ ...s, transcription_quality: e.target.value })}>
          <option value="fast">Fast</option>
          <option value="balanced">Balanced</option>
          <option value="best">Best</option>
        </select>
      </div>
      <div className="card">
        <h2>Export & storage</h2>
        <label>Export resolution</label>
        <select value={s.export_resolution || "1080p"}
                onChange={(e) => setS({ ...s, export_resolution: e.target.value })}>
          <option value="720p">720p</option>
          <option value="1080p">1080p</option>
          <option value="4k">4K</option>
        </select>
        <label>Library disk cap (GB)</label>
        <input value={s.download_cap_gb || "50"}
               onChange={(e) => setS({ ...s, download_cap_gb: e.target.value })} />
      </div>
      <button onClick={save}>Save settings</button>
    </div>
  );
}
