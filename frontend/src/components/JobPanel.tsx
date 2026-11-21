import { Fragment, useEffect, useState } from "react";
import { api, type FailedPage, type Job } from "../api";
import { paginate } from "../langs";

export function useJobs(projectId: string, active: boolean) {
  const [jobs, setJobs] = useState<Job[]>([]);
  useEffect(() => {
    let stop = false;
    const tick = () => api.listJobs(projectId).then((j) => { if (!stop) setJobs(j); }).catch(() => undefined);
    tick();
    const t = setInterval(tick, active ? 1500 : 5000);
    return () => { stop = true; clearInterval(t); };
  }, [projectId, active]);
  return jobs;
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} giây`;
  const m = Math.floor(s / 60); const r = s % 60;
  if (m < 60) return `${m} phút ${r.toString().padStart(2, "0")} giây`;
  return `${Math.floor(m / 60)} giờ ${(m % 60).toString().padStart(2, "0")} phút`;
}

/** Elapsed time of a job: start → finish, or start → now while it is still running. */
export function jobDuration(j: Job, now = Date.now()): string {
  if (!j.started_at) return j.status === "queued" ? "đang chờ" : "";
  const start = Date.parse(j.started_at);
  const end = j.finished_at ? Date.parse(j.finished_at) : now;
  return formatDuration(end - start);
}

export const JOB_STATUS_LABEL: Record<Job["status"], string> = {
  queued: "đang chờ", running: "đang chạy", done: "xong", partial: "lỗi một phần", failed: "lỗi", cancelled: "đã hủy",
};

/** `partial` is a warning, never success: a batch with one failing page must not look done. */
export function jobBadgeClass(status: Job["status"]): string {
  if (status === "failed") return "badge err";
  if (status === "partial") return "badge warn";
  if (status === "done") return "badge ok";
  return "badge";
}

/** Failed pages of a job in page order; falls back to the per-page map for jobs recorded before `failed_pages` existed. */
export function failedPages(j: Job): FailedPage[] {
  const listed = j.progress.failed_pages;
  if (listed && listed.length > 0) return [...listed].sort((a, b) => a.position - b.position);
  return Object.entries(j.progress.pages ?? {})
    .filter(([, p]) => p.status === "failed")
    .map(([page_id, p]) => ({ page_id, position: -1, display_name: page_id, step: p.step, error: p.error }));
}

/** Only finished jobs with failed pages can be retried; the server reuses the source job's provider when none is passed. */
export function canRetryFailed(j: Job): boolean {
  return (j.status === "partial" || j.status === "failed") && failedPages(j).length > 0;
}

export function jobScopeLabel(j: Job): string {
  if (j.scope === "page") return "1 trang";
  if (j.progress.retry_of) return `${j.progress.total ?? "?"} trang lỗi (chạy lại)`;
  return "cả lô";
}

export const JOBS_PER_PAGE = 8;

export function JobList({ jobs, onChanged, providerId, pageSize = JOBS_PER_PAGE }: { jobs: Job[]; onChanged: () => void; providerId?: string; pageSize?: number }) {
  const [page, setPage] = useState(1);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [retryErr, setRetryErr] = useState<{ id: string; message: string } | null>(null);
  const retry = (j: Job) => {
    setRetrying(j.id); setRetryErr(null);
    api.retryFailed(j.id, { provider_profile_id: providerId || undefined })
      .then(onChanged)
      .catch((e) => setRetryErr({ id: j.id, message: (e as Error).message }))
      .finally(() => setRetrying(null));
  };
  if (jobs.length === 0) return <p className="muted">Chưa có công việc.</p>;
  const view = paginate(jobs, page, pageSize);
  return (
    <>
    <table>
      <thead><tr><th>Bước</th><th>Phạm vi</th><th>Trạng thái</th><th>Tiến độ</th><th>Thời gian</th><th></th></tr></thead>
      <tbody>
        {view.items.map((j) => {
          const total = j.progress.total ?? 0; const done = (j.progress.done ?? 0) + (j.progress.failed ?? 0);
          const failed = failedPages(j);
          return (
            <Fragment key={j.id}>
              <tr>
                <td>{j.step}</td>
                <td>{jobScopeLabel(j)}</td>
                <td>
                  <span className={jobBadgeClass(j.status)} title={j.status}>{JOB_STATUS_LABEL[j.status] ?? j.status}</span>
                  {j.error && <div className="err" style={{ fontSize: 12 }}>{j.error}</div>}
                </td>
                <td style={{ minWidth: 160 }}>
                  <div className="progress"><div style={{ width: total ? `${(100 * done) / total}%` : "0%" }} /></div>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {done}/{total} trang{j.progress.failed ? `, ${j.progress.failed} lỗi` : ""}
                    {j.progress.estimate && ` · gửi ${j.progress.estimate.regions} vùng / ${j.progress.estimate.characters} ký tự`}
                  </span>
                </td>
                <td className="muted" style={{ whiteSpace: "nowrap" }} title={j.started_at ? `Bắt đầu ${new Date(j.started_at).toLocaleString()}` : undefined}>
                  {jobDuration(j)}{j.status === "running" && "…"}
                </td>
                <td>{(j.status === "queued" || j.status === "running") && !j.cancel_requested && (
                  <button onClick={() => api.cancelJob(j.id).then(onChanged)}>Hủy</button>)}</td>
              </tr>
              {failed.length > 0 && (
                <tr className="job-failed-pages">
                  <td colSpan={6} style={{ fontSize: 12 }}>
                    <div className="row" style={{ alignItems: "baseline" }}>
                      <span className="warn">Trang lỗi ({failed.length}):</span>
                      {canRetryFailed(j) && (
                        <button disabled={retrying === j.id} onClick={() => retry(j)}
                          title={j.step === "ocr" || j.step === "render" || providerId ? "Tạo công việc mới chỉ cho các trang lỗi" : "Dùng lại nhà cung cấp của công việc này"}>
                          Chạy lại trang lỗi
                        </button>
                      )}
                      {retryErr?.id === j.id && <span className="err">{retryErr.message}</span>}
                    </div>
                    <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                      {failed.map((p) => (
                        <li key={p.page_id}>
                          {p.position >= 0 && <b>#{p.position + 1} </b>}{p.display_name}{p.step ? ` · ${p.step}` : ""}
                          {p.error && <span className="err"> — {p.error}</span>}
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
    {view.pages > 1 && (
      <nav className="pager" aria-label="Trang công việc">
        <button disabled={view.page <= 1} onClick={() => setPage(view.page - 1)}>‹</button>
        {Array.from({ length: view.pages }, (_, i) => i + 1).map((n) => (
          <button key={n} className={n === view.page ? "current" : ""} aria-current={n === view.page ? "page" : undefined} onClick={() => setPage(n)}>{n}</button>
        ))}
        <button disabled={view.page >= view.pages} onClick={() => setPage(view.page + 1)}>›</button>
        <span className="muted">{jobs.length} công việc · trang {view.page}/{view.pages}</span>
      </nav>
    )}
    </>
  );
}
