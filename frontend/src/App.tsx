import { useEffect, useState } from "react";
import { api, subscribeEvents } from "./api";
import { useStore } from "./store";

type ThemeMode = "auto" | "light" | "dark";

function applyTheme(mode: ThemeMode) {
  const dark = mode === "dark" ||
    (mode === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>(
    (localStorage.getItem("voxcut-theme") as ThemeMode) || "auto");
  useEffect(() => {
    applyTheme(mode);
    localStorage.setItem("voxcut-theme", mode);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme(mode);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mode]);
  const next: Record<ThemeMode, ThemeMode> = { auto: "light", light: "dark", dark: "auto" };
  const icon = { auto: "◐", light: "☀", dark: "☾" }[mode];
  return (
    <button className="ghost" title={`Theme: ${mode}`}
            onClick={() => setMode(next[mode])}>{icon}</button>
  );
}
import { ProjectsView } from "./components/Projects";
import { SettingsView } from "./components/Settings";
import { LibraryView } from "./components/Library";
import { Editor } from "./components/Editor";
import { JobBar } from "./components/JobBar";

export function App() {
  const { view, setView, onEvent, toast, project } = useStore();

  useEffect(() => {
    useStore.getState().loadProjects();
    const es = subscribeEvents(onEvent);
    return () => es.close();
  }, []);

  return (
    <div className="app">
      <div className="aurora" aria-hidden><i /><i /><i /></div>
      <div className="topbar">
        <span className="logo">VOXCUT</span>
        <SystemChips />
        <div className="spacer" />
        {view === "editor" && project && <span className="muted">{project.name}</span>}
        <button className="ghost" onClick={() => setView("projects")}>Projects</button>
        <button className="ghost" onClick={() => setView("library")}>Library</button>
        <button className="ghost" onClick={() => setView("settings")}>Settings</button>
        <ThemeToggle />
      </div>
      <div className="content">
        {view === "projects" && <ProjectsView />}
        {view === "library" && <LibraryView />}
        {view === "settings" && <SettingsView />}
        {view === "editor" && <Editor />}
      </div>
      <JobBar />
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

function SystemChips() {
  const set = useStore((s) => s.setToast);
  useEffect(() => {
    api.system().then((s) => {
      if (!s.ffmpeg) set("⚠ ffmpeg not found");
    }).catch(() => {});
  }, []);
  return null;
}
