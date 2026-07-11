import type { Beat, Edl, Job, Project, Waveform, Word } from "./types";

const token = new URLSearchParams(location.search).get("t") || "";

function url(path: string): string {
  const sep = path.includes("?") ? "&" : "?";
  return `/api${path}${sep}t=${token}`;
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(url(path), {
    headers: { "content-type": "application/json", "x-voxcut-token": token },
    ...opts,
  });
  if (!res.ok) {
    let detail: any = await res.text();
    try { detail = JSON.parse(detail); } catch { /* ignore */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : (res as any)) as T;
}

export const api = {
  token,
  mediaUrl: (path: string) => url(path),

  health: () => req<{ ok: boolean }>("/health"),
  system: () => req<{ ffmpeg: boolean; yt_dlp: boolean; yt_dlp_version: string | null;
    brain_ready: boolean; data_dir: string; library_bytes: number }>("/system"),
  canary: () => req<{ ok: boolean; error: string | null }>("/system/canary", { method: "POST" }),
  updateYtdlp: () => req<{ ok: boolean; version?: string; error?: string }>("/system/update_ytdlp", { method: "POST" }),

  libraryList: (q?: string) => req<{ assets: any[]; disk_usage_bytes: number }>(`/library${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  libraryUpload: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(url("/library/upload"), {
      method: "POST", headers: { "x-voxcut-token": token }, body: fd,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  libraryPin: (id: string, pinned: boolean) =>
    req(`/library/${id}/pin`, { method: "POST", body: JSON.stringify({ pinned }) }),
  libraryPrune: (max_gb?: number, older_than_days?: number) =>
    req<{ count: number }>("/library/prune", { method: "POST", body: JSON.stringify({ max_gb, older_than_days }) }),

  getSettings: () => req<Record<string, any>>("/settings"),
  putSettings: (values: Record<string, string>) =>
    req("/settings", { method: "PUT", body: JSON.stringify({ values }) }),
  testKey: (openai_api_key?: string, openai_model?: string) =>
    req<{ ok: boolean; error?: string; model?: string }>("/settings/test_key", {
      method: "POST", body: JSON.stringify({ openai_api_key, openai_model }),
    }),

  listProjects: () => req<Project[]>("/projects"),
  createProject: (name: string, context_brief: any, settings: any) =>
    req<Project>("/projects", {
      method: "POST",
      body: JSON.stringify({ name, context_brief, settings }),
    }),
  getProject: (id: string) => req<Project>(`/projects/${id}`),
  updateProject: (id: string, body: any) =>
    req<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  uploadVoiceover: async (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(url(`/projects/${id}/voiceover`), {
      method: "POST", headers: { "x-voxcut-token": token }, body: fd,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<{ job_id: string }>;
  },
  waveform: (id: string) => req<Waveform>(`/projects/${id}/waveform`),
  transcript: (id: string) => req<{ count: number; words: Word[] }>(`/projects/${id}/transcript`),

  getBeats: (id: string) => req<{ beats: Beat[]; mode: string }>(`/projects/${id}/beats`),
  rederiveBeats: (id: string) => req<{ job_id: string }>(`/projects/${id}/beats/rederive`, { method: "POST" }),

  generate: (id: string) => req<{ job_id: string }>(`/projects/${id}/generate`, { method: "POST" }),
  getEdl: (id: string) => req<Edl>(`/projects/${id}/edl`),
  applyOps: (id: string, base_version: number | null, ops: any[]) =>
    req<{ edl: Edl; dirty: string[] }>(`/projects/${id}/edl/ops`, {
      method: "POST", body: JSON.stringify({ base_version, ops }),
    }),
  undo: (id: string) => req<Edl>(`/projects/${id}/edl/undo`, { method: "POST" }),
  splitEvent: (id: string, event_id: string, at_s: number) =>
    req<{ edl: Edl; cut_s: number; event_ids: string[]; new_event_id: string }>(
      `/projects/${id}/edl/split`, {
        method: "POST", body: JSON.stringify({ event_id, at_s }),
      }),
  addSegment: (id: string, start_s: number, end_s: number) =>
    req<{ edl: Edl; new_event_id: string; removed: string[] }>(
      `/projects/${id}/edl/add_segment`, {
        method: "POST", body: JSON.stringify({ start_s, end_s }),
      }),
  reroll: (id: string, eventIds: string[]) =>
    eventIds.length === 1
      ? req<{ job_id: string }>(`/projects/${id}/events/${eventIds[0]}/reroll`, { method: "POST" })
      : req<{ job_id: string }>(`/projects/${id}/events/reroll`, {
          method: "POST", body: JSON.stringify({ event_ids: eventIds }),
        }),
  rebuildPreview: (id: string) => req<{ job_id: string }>(`/projects/${id}/preview/rebuild`, { method: "POST" }),
  previewUrl: (id: string) => url(`/projects/${id}/preview`),

  candidates: (id: string, ev: string) => req<any>(`/projects/${id}/candidates/${ev}`),
  pickMoment: (id: string, ev: string, in_s: number, out_s: number) =>
    req(`/projects/${id}/candidates/${ev}/pick_moment`, {
      method: "POST", body: JSON.stringify({ in_s, out_s }),
    }),
  research: (id: string, ev: string, query: string) =>
    req<{ job_id: string }>(`/projects/${id}/candidates/${ev}/research`, {
      method: "POST", body: JSON.stringify({ query }),
    }),
  pickFinalist: (id: string, ev: string, asset_id: string, in_s: number, out_s: number) =>
    req(`/projects/${id}/candidates/${ev}/pick_finalist`, {
      method: "POST", body: JSON.stringify({ asset_id, in_s, out_s }),
    }),

  getJob: (jobId: string) => req<Job>(`/jobs/${jobId}`),
  exportProject: (id: string, resolution: string) =>
    req<{ job_id: string }>(`/projects/${id}/export`, {
      method: "POST", body: JSON.stringify({ resolution }),
    }),
  exportUrl: (id: string) => url(`/projects/${id}/export/download`),
};

export function subscribeEvents(onEvent: (ev: any) => void): EventSource {
  const es = new EventSource(url("/events"));
  es.onmessage = (m) => {
    try { onEvent(JSON.parse(m.data)); } catch { /* ignore */ }
  };
  return es;
}
