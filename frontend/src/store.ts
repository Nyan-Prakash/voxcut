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
  select: (id: string | null) => void;
  applyOps: (ops: any[]) => Promise<void>;
  undo: () => Promise<void>;
  setTool: (t: State["tool"]) => void;
  splitAt: (eventId: string, atS: number) => Promise<void>;
  addSegmentRange: (startS: number, endS: number) => Promise<void>;
  reroll: (eventIds: string[]) => Promise<void>;
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
    set({ project, view: "editor", selectedEventId: null });
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

  select: (id) => set({ selectedEventId: id }),

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

  reroll: async (eventIds) => {
    const { project } = get();
    if (!project || !eventIds.length) return;
    try {
      await api.reroll(project.id, eventIds);
      get().setToast(eventIds.length === 1
        ? "🎲 Rerolling clip — fresh plan, fresh footage…"
        : `🎲 Rerolling ${eventIds.length} clips…`);
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
