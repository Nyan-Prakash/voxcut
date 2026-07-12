import { create } from "zustand";
import { api } from "./api";
import type { Beat, Edl, EdlEvent, Job, Project, Waveform, Word } from "./types";

interface State {
  view: "projects" | "editor" | "settings" | "library";
  projects: Project[];
  project: Project | null;
  edl: Edl | null;
  beats: Beat[];
  words: Word[];
  waveform: Waveform | null;
  selectedEventId: string | null;
  selectedEventIds: string[];
  jobs: Record<string, Job>;
  activeJobId: string | null;
  previewNonce: number;
  toast: string | null;
  playheadS: number;
  videoEl: HTMLVideoElement | null;
  tool: "select" | "cut" | "add";

  setView: (v: State["view"]) => void;
  loadProjects: () => Promise<void>;
  openProject: (id: string) => Promise<void>;
  refreshEdl: () => Promise<void>;
  select: (id: string | null, mode?: "single" | "toggle" | "range") => void;
  applyOps: (ops: any[]) => Promise<void>;
  undo: () => Promise<void>;
  setTool: (t: State["tool"]) => void;
  splitAt: (eventId: string, atS: number) => Promise<void>;
  addSegmentRange: (startS: number, endS: number) => Promise<void>;
  reroll: (eventIds: string[], hint?: string) => Promise<void>;
  updateMusic: (patch: Record<string, any>) => Promise<void>;
  onEvent: (ev: any) => void;
  setToast: (t: string | null) => void;
  bumpPreview: () => void;
  setPlayhead: (t: number) => void;
  registerVideo: (el: HTMLVideoElement | null) => void;
  seek: (t: number) => void;
}

export const useStore = create<State>((set, get) => ({
  view: "projects",
  projects: [],
  project: null,
  edl: null,
  beats: [],
  words: [],
  waveform: null,
  selectedEventId: null,
  selectedEventIds: [],
  jobs: {},
  activeJobId: null,
  previewNonce: 0,
  toast: null,
  playheadS: 0,
  videoEl: null,
  tool: "select",

  setView: (v) => set({ view: v }),
  setTool: (t) => set({ tool: t }),

  loadProjects: async () => set({ projects: await api.listProjects() }),

  openProject: async (id) => {
    const project = await api.getProject(id);
    set({ project, view: "editor", selectedEventId: null, selectedEventIds: [] });
    try { set({ waveform: await api.waveform(id) }); } catch { /* not ready */ }
    try { const t = await api.transcript(id); set({ words: t.words }); } catch { /* */ }
    try { const b = await api.getBeats(id); set({ beats: b.beats }); } catch { /* */ }
    try { set({ edl: await api.getEdl(id) }); } catch { /* not generated */ }
  },

  refreshEdl: async () => {
    const { project } = get();
    if (!project) return;
    // Refresh everything a completed job might have produced, so the UI unlocks
    // (e.g. the "Generate" button enabling once transcription finishes).
    try { const t = await api.transcript(project.id); set({ words: t.words }); } catch { /* */ }
    try { set({ waveform: await api.waveform(project.id) }); } catch { /* */ }
    try { const b = await api.getBeats(project.id); set({ beats: b.beats }); } catch { /* */ }
    try { set({ edl: await api.getEdl(project.id) }); } catch { /* */ }
    try { set({ project: await api.getProject(project.id) }); } catch { /* */ }
  },

  select: (id, mode = "single") => {
    if (id === null) {
      set({ selectedEventId: null, selectedEventIds: [] });
      return;
    }
    const { selectedEventIds, selectedEventId, edl } = get();
    if (mode === "toggle") {
      const ids = selectedEventIds.includes(id)
        ? selectedEventIds.filter((x) => x !== id)
        : [...selectedEventIds, id];
      set({ selectedEventIds: ids, selectedEventId: ids[ids.length - 1] || null });
    } else if (mode === "range" && selectedEventId && edl) {
      // Everything between the last-selected clip and this one, in timeline order.
      const order = [...edl.events].sort((a, b) => a.start_s - b.start_s).map((e) => e.id);
      const [a, b] = [order.indexOf(selectedEventId), order.indexOf(id)];
      if (a === -1 || b === -1) {
        set({ selectedEventId: id, selectedEventIds: [id] });
        return;
      }
      const span = order.slice(Math.min(a, b), Math.max(a, b) + 1);
      const ids = [...new Set([...get().selectedEventIds, ...span])];
      set({ selectedEventIds: ids, selectedEventId: id });
    } else {
      set({ selectedEventId: id, selectedEventIds: [id] });
    }
  },

  applyOps: async (ops) => {
    const { project, edl } = get();
    if (!project || !edl) return;
    const res = await api.applyOps(project.id, edl.version, ops);
    set({ edl: res.edl });
    // Re-render the dirty preview segment(s).
    await api.rebuildPreview(project.id);
  },

  undo: async () => {
    const { project } = get();
    if (!project) return;
    const edl = await api.undo(project.id);
    set({ edl });
    await api.rebuildPreview(project.id);
  },

  splitAt: async (eventId, atS) => {
    const { project } = get();
    if (!project) return;
    try {
      const res = await api.splitEvent(project.id, eventId, atS);
      set({ edl: res.edl, selectedEventId: res.new_event_id });
      try { const b = await api.getBeats(project.id); set({ beats: b.beats }); } catch { /* */ }
      get().setToast(`✂ Cut at ${res.cut_s.toFixed(2)}s — reroll either half for new footage`);
      await api.rebuildPreview(project.id);
    } catch (e: any) { get().setToast(e.message); }
  },

  addSegmentRange: async (startS, endS) => {
    const { project } = get();
    if (!project) return;
    try {
      const res = await api.addSegment(project.id, startS, endS);
      set({ edl: res.edl, selectedEventId: res.new_event_id, tool: "select" });
      try { const b = await api.getBeats(project.id); set({ beats: b.beats }); } catch { /* */ }
      get().setToast("Segment added — search or reroll to fill it");
      await api.rebuildPreview(project.id);
    } catch (e: any) { get().setToast(e.message); }
  },

  updateMusic: async (patch) => {
    const { project } = get();
    if (!project) return;
    const music = { enabled: true, volume_db: -25, duck_db: 8, regions: [],
                    ...(project.settings?.music || {}), ...patch };
    try {
      const p = await api.updateProject(project.id, {
        settings: { ...project.settings, music },
      });
      set({ project: p });
      await api.rebuildPreview(project.id);
    } catch (e: any) { get().setToast(e.message); }
  },

  reroll: async (eventIds, hint) => {
    const { project } = get();
    if (!project || !eventIds.length) return;
    try {
      await api.reroll(project.id, eventIds, hint);
      const dir = hint?.trim() ? " with your direction" : "";
      get().setToast(eventIds.length === 1
        ? `🎲 Rerolling clip${dir} — fresh plan, fresh footage…`
        : `🎲 Rerolling ${eventIds.length} clips${dir}…`);
    } catch (e: any) { get().setToast(e.message); }
  },

  onEvent: (ev) => {
    const { project } = get();
    if (ev.job_id && ev.steps) {
      set((s) => ({
        jobs: { ...s.jobs, [ev.job_id]: { ...(s.jobs[ev.job_id] || {}), id: ev.job_id,
          kind: ev.kind, state: "running", steps: ev.steps, project_id: ev.project_id,
          error: null } as Job },
        activeJobId: ev.job_id,
      }));
    }
    if (ev.type === "job_done" || ev.type === "job_failed") {
      set((s) => ({
        jobs: { ...s.jobs, [ev.job_id]: { ...(s.jobs[ev.job_id] || {} as any),
          state: ev.type === "job_done" ? "done" : "failed", error: ev.error || null } },
      }));
      if (ev.project_id && project && ev.project_id === project.id) {
        get().refreshEdl();
      }
    }
    if (ev.type === "preview_updated" && project && ev.project_id === project.id) {
      get().bumpPreview();
    }
  },

  setToast: (t) => {
    set({ toast: t });
    if (t) {
      const msg = t;
      setTimeout(() => {
        if (get().toast === msg) set({ toast: null });
      }, 4000);
    }
  },
  bumpPreview: () => set((s) => ({ previewNonce: s.previewNonce + 1 })),
  setPlayhead: (t) => set({ playheadS: t }),
  registerVideo: (el) => set({ videoEl: el }),
  seek: (t) => {
    const { videoEl } = get();
    if (videoEl) {
      videoEl.currentTime = t;
      videoEl.play().catch(() => {});
    }
    set({ playheadS: t });
  },
}));
