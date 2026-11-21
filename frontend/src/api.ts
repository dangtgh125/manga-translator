// Typed client for the backend API. All text from the server is rendered as text, never as HTML.

export type Point = [number, number];
export interface Box { x: number; y: number; w: number; h: number }
export interface GlossaryEntry { source: string; target: string; note: string }

export interface Page {
  id: string; project_id: string; position: number; display_name: string; width: number; height: number;
  format: string; status: string; steps: Record<string, { status: string; message?: string | null; at?: string }>;
  error: string | null; source_lang_override: string | null; detected_lang: string | null; lang_uncertain: boolean;
  has_render: boolean; render_stale: boolean;
  /** Regions whose translation is outdated (target language/glossary/source changed): the page needs re-translation. */
  translation_stale_count: number; translation_stale: boolean;
  render_report: { rendered: number; skipped: number; inpainted?: number; inpainter?: string; kept?: number; warnings: { region_id: string; type: string; action?: string; message: string }[] } | null;
  version: number; created_at: string; updated_at: string;
}
export interface Project {
  id: string; name: string; target_lang: string; reading_direction: "rtl" | "ltr"; glossary: GlossaryEntry[];
  settings: Record<string, unknown>; version: number; created_at: string; updated_at: string; pages: Page[];
}
export interface ProjectSummary { id: string; name: string; target_lang: string; page_count: number; created_at: string; updated_at: string }
export interface Region {
  id: string; page_id: string; polygon: Point[]; direction: "vertical" | "horizontal" | "unknown"; reading_order: number;
  source_lang: string; lang_confidence: number; lang_uncertain: boolean; ocr_text: string; ocr_confidence: number;
  ocr_engine: string; source_text: string; source_edited: boolean;
  /** Set when the user changed the field by hand; an OCR re-run then keeps the user's value. */
  polygon_edited: boolean; direction_edited: boolean; source_lang_edited: boolean;
  translated_text: string; final_text: string;
  final_edited: boolean; final_lang: string; translation_provider: string; translation_stale: boolean; translation_flags: string[];
  review_status: "pending" | "approved" | "flagged"; mask_mode: "auto" | "box" | "none"; mask_box: Box | null;
  text_box: Box | null; font_size: number | null; lines: { polygon: Point[]; text: string; confidence: number; furigana: boolean }[];
  origin: string; version: number;
}
export interface FailedPage { page_id: string; position: number; display_name: string; step: string | null; error: string | null }
export interface Job {
  id: string; project_id: string; scope: "all" | "page"; page_id: string | null; step: "ocr" | "translate" | "render" | "all";
  /** done = every page ok; partial = some pages failed (see progress.failed_pages); failed = all pages failed or job error. */
  status: "queued" | "running" | "done" | "partial" | "failed" | "cancelled"; progress: {
    total?: number; done?: number; failed?: number; steps?: string[]; estimate?: { regions: number; characters: number };
    pages?: Record<string, { status: string; step: string | null; error: string | null }>;
    failed_pages?: FailedPage[];
    /** Set on jobs created by retryFailed: id of the job whose failed pages are being re-run. */
    retry_of?: string;
  }; error: string | null; attempts: number; cancel_requested: boolean; created_at: string; updated_at: string;
  started_at: string | null; finished_at: string | null;
}
export type ProviderKind = "google" | "openai" | "anthropic" | "responses_compatible" | "mock";
export interface ProviderProfile {
  id: string; name: string; provider: ProviderKind; model_id: string; endpoint: string | null;
  options: Record<string, unknown>; has_secret: boolean; created_at: string; updated_at: string;
}
export interface ProviderTestResult { ok: boolean; provider: string; model_id: string; message: string; models: string[]; data_recipient: string }
export interface CostEstimate { regions: number; characters: number; provider: string; note: string }
export interface Health { status: string; version: string; ocr_engine: string; secrets_available: boolean; worker_alive: boolean | null }

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

export function errorMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; errors?: string[] };
    if (d.message) return d.errors?.length ? `${d.message}: ${d.errors.join("; ")}` : d.message;
    return JSON.stringify(detail).slice(0, 300);
  }
  return "request failed";
}

async function request<T>(method: string, url: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const init: RequestInit = { method, headers: { ...headers }, credentials: "same-origin" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.body = JSON.stringify(body); (init.headers as Record<string, string>)["Content-Type"] = "application/json"; }
  const res = await fetch(url, init);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new ApiError(res.status, errorMessage((data as { detail?: unknown })?.detail ?? data));
  return data as T;
}

export const api = {
  health: () => request<Health>("GET", "/api/health"),
  listProjects: () => request<ProjectSummary[]>("GET", "/api/projects"),
  createProject: (body: { name: string; target_lang: string; reading_direction: "rtl" | "ltr" }) => request<Project>("POST", "/api/projects", body),
  getProject: (id: string) => request<Project>("GET", `/api/projects/${id}`),
  updateProject: (id: string, body: Partial<Project> & { version: number }) => request<Project>("PATCH", `/api/projects/${id}`, body),
  deleteProject: (id: string) => request<void>("DELETE", `/api/projects/${id}`),
  uploadPages: (id: string, files: File[]) => { const fd = new FormData(); files.forEach((f) => fd.append("files", f)); return request<Page[]>("POST", `/api/projects/${id}/pages`, fd); },
  reorderPages: (id: string, page_ids: string[]) => request<Page[]>("POST", `/api/projects/${id}/pages/reorder`, { page_ids }),
  getPage: (id: string) => request<Page>("GET", `/api/pages/${id}`),
  updatePage: (id: string, body: { version: number; source_lang_override?: string | null; clear_source_lang_override?: boolean }) => request<Page>("PATCH", `/api/pages/${id}`, body),
  deletePage: (id: string) => request<void>("DELETE", `/api/pages/${id}`),
  listRegions: (pageId: string) => request<Region[]>("GET", `/api/pages/${pageId}/regions`),
  createRegion: (pageId: string, body: { polygon: Point[]; direction?: string; source_lang?: string; source_text?: string }) => request<Region>("POST", `/api/pages/${pageId}/regions`, body),
  updateRegion: (pageId: string, regionId: string, body: Partial<Region> & { version: number }) => request<Region>("PATCH", `/api/pages/${pageId}/regions/${regionId}`, body),
  deleteRegion: (pageId: string, regionId: string) => request<void>("DELETE", `/api/pages/${pageId}/regions/${regionId}`),
  reorderRegions: (pageId: string, region_ids: string[]) => request<Region[]>("POST", `/api/pages/${pageId}/regions/reorder`, { region_ids }),
  retryPage: (pageId: string, body: { step: Job["step"]; provider_profile_id?: string; options?: Record<string, unknown> }) => request<Job>("POST", `/api/pages/${pageId}/retry`, body),
  createJob: (projectId: string, body: { scope: Job["scope"]; page_id?: string; step: Job["step"]; provider_profile_id?: string; options?: Record<string, unknown> }) =>
    request<Job>("POST", `/api/projects/${projectId}/jobs`, body, { "Idempotency-Key": crypto.randomUUID() }),
  listJobs: (projectId: string) => request<Job[]>("GET", `/api/projects/${projectId}/jobs`),
  getJob: (id: string) => request<Job>("GET", `/api/jobs/${id}`),
  cancelJob: (id: string) => request<Job>("POST", `/api/jobs/${id}/cancel`),
  /** New job for exactly the pages that failed in `id`; step/provider default to the source job's. */
  retryFailed: (id: string, body: { step?: Job["step"]; provider_profile_id?: string; options?: Record<string, unknown> } = {}) =>
    request<Job>("POST", `/api/jobs/${id}/retry-failed`, body, { "Idempotency-Key": crypto.randomUUID() }),
  listProviders: () => request<ProviderProfile[]>("GET", "/api/providers"),
  createProvider: (body: { name: string; provider: ProviderKind; model_id: string; endpoint?: string | null; options?: Record<string, unknown>; secret?: string | null }) => request<ProviderProfile>("POST", "/api/providers", body),
  updateProvider: (id: string, body: { name?: string; model_id?: string; endpoint?: string; options?: Record<string, unknown> }) => request<ProviderProfile>("PATCH", `/api/providers/${id}`, body),
  setProviderSecret: (id: string, secret: string) => request<ProviderProfile>("PUT", `/api/providers/${id}/secret`, { secret }),
  deleteProviderSecret: (id: string) => request<ProviderProfile>("DELETE", `/api/providers/${id}/secret`),
  deleteProvider: (id: string) => request<void>("DELETE", `/api/providers/${id}`),
  testProvider: (body: { profile_id?: string; provider?: ProviderKind; model_id?: string; endpoint?: string; options?: Record<string, unknown>; secret?: string }) => request<ProviderTestResult>("POST", "/api/providers/test", body),
  estimate: (providerId: string, projectId: string) => request<CostEstimate>("GET", `/api/providers/${providerId}/estimate/${projectId}`),
  /** ZIP/PNG exports answer 409 when a page's translation is outdated unless `allowStaleTranslation` is set. */
  exportUrl: (projectId: string, fmt: "zip" | "json" | "txt" | "png", pageIds?: string[], includeOriginal = false, allowStaleTranslation = false) =>
    `/api/projects/${projectId}/export?fmt=${fmt}${pageIds?.length ? `&pages=${encodeURIComponent(pageIds.join(","))}` : ""}${includeOriginal ? "&include_original=1" : ""}${allowStaleTranslation ? "&allow_stale_translation=1" : ""}`,
  pageImageUrl: (pageId: string) => `/api/pages/${pageId}/image`,
  pageRenderUrl: (pageId: string, bust: string | number) => `/api/pages/${pageId}/render.png?v=${encodeURIComponent(String(bust))}`,
};
