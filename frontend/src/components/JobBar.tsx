import { useStore } from "../store";

export function JobBar() {
  const { jobs, activeJobId } = useStore();
  const job = activeJobId ? jobs[activeJobId] : null;
  if (!job || job.state === "done") return null;

  const steps = job.steps || [];
  const running = steps.find((s) => s.state === "running") || steps[steps.length - 1];
  const overall = steps.length
    ? steps.reduce((a, s) => a + (s.state === "done" ? 1 : s.progress), 0) / steps.length
    : 0;

  return (
    <div className="jobbar">
      <strong>{job.kind}</strong>
      <span className="muted">{running?.name}: {running?.message || running?.state}</span>
      <div className="bar"><i style={{ width: `${Math.round(overall * 100)}%` }} /></div>
      {job.state === "failed" && <span className="chip bad">failed: {job.error}</span>}
    </div>
  );
}
