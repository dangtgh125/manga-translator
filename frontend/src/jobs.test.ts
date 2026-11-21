import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  JOB_STATUS_LABEL, JobList, canRetryFailed, failedPages, formatDuration, jobBadgeClass, jobDuration, jobScopeLabel,
} from "./components/JobPanel";
import { paginate } from "./langs";
import type { Job } from "./api";

const base: Job = {
  id: "j", project_id: "p", scope: "all", page_id: null, step: "all", status: "done", progress: {}, error: null,
  attempts: 1, cancel_requested: false, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  started_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:01:05Z",
};

describe("job duration", () => {
  it("formats seconds, minutes and hours", () => {
    expect(formatDuration(4_400)).toBe("4 giây");
    expect(formatDuration(65_000)).toBe("1 phút 05 giây");
    expect(formatDuration(3_720_000)).toBe("1 giờ 02 phút");
    expect(formatDuration(-1)).toBe("");
  });
  it("uses finish time when done and now while running", () => {
    expect(jobDuration(base)).toBe("1 phút 05 giây");
    const running = { ...base, status: "running" as const, finished_at: null };
    expect(jobDuration(running, Date.parse("2026-01-01T00:00:12Z"))).toBe("12 giây");
    expect(jobDuration({ ...base, status: "queued", started_at: null })).toBe("đang chờ");
  });
});

const partial: Job = {
  ...base, status: "partial", error: "1/3 page(s) failed",
  progress: {
    total: 3, done: 2, failed: 1,
    pages: { pg2: { status: "failed", step: "ocr", error: "cannot identify image" } },
    failed_pages: [{ page_id: "pg2", position: 1, display_name: "p1.png", step: "ocr", error: "cannot identify image" }],
  },
};

const render = (jobs: Job[], providerId?: string) =>
  renderToStaticMarkup(createElement(JobList, { jobs, onChanged: () => undefined, providerId }));

describe("partially failed jobs", () => {
  it("shows partial as a warning, never as success", () => {
    expect(JOB_STATUS_LABEL.partial).toBe("lỗi một phần");
    expect(jobBadgeClass("partial")).toBe("badge warn");
    expect(jobBadgeClass("done")).toBe("badge ok");
    expect(jobBadgeClass("failed")).toBe("badge err");
  });
  it("lists failed pages in page order, falling back to the per-page map", () => {
    const two = { ...partial, progress: { ...partial.progress, failed_pages: [
      { page_id: "b", position: 2, display_name: "b.png", step: "render", error: "x" },
      { page_id: "a", position: 0, display_name: "a.png", step: "ocr", error: "y" },
    ] } };
    expect(failedPages(two).map((p) => p.page_id)).toEqual(["a", "b"]);
    const legacy = { ...partial, progress: { total: 3, done: 2, failed: 1, pages: partial.progress.pages } };
    expect(failedPages(legacy)).toEqual([{ page_id: "pg2", position: -1, display_name: "pg2", step: "ocr", error: "cannot identify image" }]);
    expect(failedPages(base)).toEqual([]);
  });
  it("only offers a retry for finished jobs that have failed pages", () => {
    expect(canRetryFailed(partial)).toBe(true);
    expect(canRetryFailed({ ...partial, status: "failed" })).toBe(true);
    expect(canRetryFailed({ ...partial, status: "running" })).toBe(false);
    expect(canRetryFailed(base)).toBe(false);
  });
  it("labels retry jobs by the number of pages they re-run", () => {
    expect(jobScopeLabel({ ...base, progress: { total: 2, retry_of: "j" } })).toBe("2 trang lỗi (chạy lại)");
    expect(jobScopeLabel(base)).toBe("cả lô");
    expect(jobScopeLabel({ ...base, scope: "page" })).toBe("1 trang");
  });
  it("renders the warning badge, the failed pages and the retry button", () => {
    const html = render([partial], "prov");
    expect(html).toContain('class="badge warn"');
    expect(html).toContain("lỗi một phần");
    expect(html).not.toContain("badge ok");
    expect(html).toContain("1/3 page(s) failed");
    expect(html).toContain("#2");
    expect(html).toContain("p1.png");
    expect(html).toContain("cannot identify image");
    expect(html).toContain("Chạy lại trang lỗi");
  });
  it("renders a successful job without any failed-page row", () => {
    const html = render([{ ...base, progress: { total: 3, done: 3, failed: 0 } }]);
    expect(html).toContain('class="badge ok"');
    expect(html).not.toContain("Trang lỗi");
    expect(html).not.toContain("Chạy lại trang lỗi");
  });
});

describe("job list pagination", () => {
  it("splits jobs into pages and shows the pager only when there is more than one", () => {
    const many = Array.from({ length: 10 }, (_, i): Job => ({ ...base, id: `j${i}`, step: `step${i}` as Job["step"] }));
    const html = renderToStaticMarkup(createElement(JobList, { jobs: many, onChanged: () => undefined, pageSize: 4 }));
    expect(html).toContain("step0"); expect(html).toContain("step3"); expect(html).not.toContain("step4");
    expect(html).toContain("trang 1/3");
    const few = renderToStaticMarkup(createElement(JobList, { jobs: many.slice(0, 3), onChanged: () => undefined, pageSize: 4 }));
    expect(few).not.toContain("class=\"pager\"");
  });
  it("clamps an out-of-range page and keeps the last page partial", () => {
    expect(paginate([1, 2, 3, 4, 5], 9, 2)).toEqual({ items: [5], page: 3, pages: 3 });
    expect(paginate([], 1, 4)).toEqual({ items: [], page: 1, pages: 1 });
  });
});

import { sfxPolicy } from "./langs";

describe("sfxPolicy", () => {
  it("defaults to keep and ignores unknown values", () => {
    expect(sfxPolicy(undefined)).toBe("keep");
    expect(sfxPolicy({})).toBe("keep");
    expect(sfxPolicy({ sfx_policy: "bogus" })).toBe("keep");
    expect(sfxPolicy({ sfx_policy: "white_box" })).toBe("white_box");
  });
});
