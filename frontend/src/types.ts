export interface Project {
  id: string;
  name: string;
  status: string;
  duration_s: number;
  edl_version: number;
  context_brief: Record<string, any>;
  settings: Record<string, any>;
  voiceover_path: string | null;
}

export interface Word {
  idx: number;
  text: string;
  start_s: number;
  end_s: number;
  confidence: number;
  corrected_text: string | null;
}

export interface Beat {
  id: string;
  start_s: number;
  end_s: number;
  word_start_idx: number;
  word_end_idx: number;
  text: string;
  gist: string;
  tone: string;
  emphasis: number;
  visual_affinity: string;
  concrete_entities: string[];
  locked: boolean;
}

export interface EdlSource {
  in_s: number;
  out_s: number;
  chosen_rank?: number;
  confidence?: number;
}

export interface EdlEvent {
  id: string;
  beat_id: string | null;
  start_s: number;
  end_s: number;
  kind: string;
  asset_id: string | null;
  source: EdlSource | null;
  queries: string[];
  joke_queries?: string[];
  treatment: Record<string, any>;
  audio: { mode: string; duck_db?: number };
  flags: string[];
  locked: boolean;
  moment_candidates?: EdlSource[];
  source_candidates?: any[];
  finalists?: any[];
}

export interface Edl {
  version: number;
  aspect: string;
  events: EdlEvent[];
}

export interface JobStep {
  name: string;
  state: string;
  progress: number;
  message: string;
}

export interface Job {
  id: string;
  project_id: string | null;
  kind: string;
  state: string;
  steps: JobStep[];
  error: string | null;
}

export interface Waveform {
  version: number;
  buckets_per_s: number;
  peaks: number[];
}
