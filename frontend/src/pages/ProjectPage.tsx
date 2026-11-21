import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type CostEstimate, type Job, type Page, type Project, type ProviderProfile } from "../api";
import { GlossaryEditor } from "../components/GlossaryEditor";
import { JobList, useJobs } from "../components/JobPanel";
import { SFX_POLICIES, sfxPolicy, targetLangOptions } from "../langs";

const LANGS: Record<string, string> = { ja: "Nhật", "zh-Hans": "Trung Giản thể", "zh-Hant": "Trung Phồn thể", unknown: "?" };

export default function ProjectPage() {
  const { id = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [providers, setProviders] = useState<ProviderProfile[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<Job["step"]>("all");
  const [scope, setScope] = useState<Job["scope"]>("all");
  const [pageId, setPageId] = useState("");
  const [providerId, setProviderId] = useState("");
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const fileRef = useRef<HTMLInputElement>(null);
  const load = useCallback(() => api.getProject(id).then(setProject).catch((e) => setErr(e.message)), [id]);
  useEffect(() => { load(); api.listProviders().then(setProviders).catch(() => undefined); }, [load]);
  // First profile in the list is the default so a single-provider setup needs no extra click.
  useEffect(() => { if (!providerId && providers.length) setProviderId(providers[0].id); }, [providers, providerId]);
  // Drop selections for pages that no longer exist.
  useEffect(() => {
    if (!project) return;
    const ids = new Set(project.pages.map((p) => p.id));
    setSelected((cur) => (Array.from(cur).every((x) => ids.has(x)) ? cur : new Set(Array.from(cur).filter((x) => ids.has(x)))));
  }, [project]);
  const jobs = useJobs(id, true);
  const active = useMemo(() => jobs.some((j) => j.status === "queued" || j.status === "running"), [jobs]);
  const lastSeen = useRef("");
  useEffect(() => {
    const sig = jobs.map((j) => `${j.id}:${j.status}:${j.updated_at}`).join("|");
    if (sig !== lastSeen.current) { lastSeen.current = sig; load(); }
  }, [jobs, load]);
  useEffect(() => {
    if (providerId && (step === "translate" || step === "all")) api.estimate(providerId, id).then(setEstimate).catch(() => setEstimate(null));
    else setEstimate(null);
  }, [providerId, step, id]);

  if (!project) return <p className={err ? "err" : "muted"}>{err || "Đang tải…"}</p>;

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setErr("");
    try { await api.uploadPages(id, Array.from(files)); await load(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const move = async (p: Page, delta: number) => {
    const ids = project.pages.map((x) => x.id); const i = ids.indexOf(p.id); const j = i + delta;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    await api.reorderPages(id, ids); load();
  };
  const run = async () => {
    setErr("");
    try {
      await api.createJob(id, { scope, page_id: scope === "page" ? pageId : undefined, step, provider_profile_id: providerId || undefined });
    } catch (e) { setErr((e as Error).message); }
  };
  const saveProject = async (patch: Partial<Project>) => {
    try { setProject(await api.updateProject(id, { ...patch, version: project.version })); } catch (e) { setErr((e as Error).message); load(); }
  };
  const needsProvider = step === "translate" || step === "all";
  const provider = providers.find((p) => p.id === providerId);
  const rendered = project.pages.filter((p) => p.has_render);
  const selectedIds = project.pages.filter((p) => selected.has(p.id)).map((p) => p.id);
  const toggle = (pid: string) => setSelected((cur) => { const n = new Set(cur); if (n.has(pid)) n.delete(pid); else n.add(pid); return n; });
  const selectAll = (only: "all" | "rendered" | "none") =>
    setSelected(new Set(only === "none" ? [] : (only === "rendered" ? rendered : project.pages).map((p) => p.id)));
  const pngName = (p: Page) => `page_${String(p.position + 1).padStart(4, "0")}.png`;
  // Image downloads of pages whose translation is outdated are refused by the server (409) unless the
  // user explicitly accepts exporting the old translation.
  const confirmStale = (pages: Page[]): boolean | null => {
    const stale = pages.filter((p) => p.translation_stale);
    if (!stale.length) return false;
    const names = stale.map((p) => p.position + 1).join(", ");
    return confirm(`Trang ${names} có bản dịch đã cũ (cần dịch lại).\nVẫn tải ảnh với bản dịch cũ?`) ? true : null;
  };
  const exportImages = (fmt: "zip" | "png", pageIds?: string[], includeOriginal = false) => {
    const involved = pageIds ? project.pages.filter((p) => pageIds.includes(p.id)) : project.pages;
    const allowStale = confirmStale(involved);
    if (allowStale === null) return;
    window.location.assign(api.exportUrl(id, fmt, pageIds, includeOriginal, allowStale));
  };

  return (
    <>
      <div className="panel">
        <div className="row">
          <h2 style={{ margin: 0 }}>{project.name}</h2>
          <label>Ngôn ngữ đích <select value={project.target_lang} onChange={(e) => e.target.value !== project.target_lang && saveProject({ target_lang: e.target.value })}>{targetLangOptions(project.target_lang).map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}</select></label>
          <label>Hướng đọc <select value={project.reading_direction} onChange={(e) => saveProject({ reading_direction: e.target.value as "rtl" | "ltr" })}><option value="rtl">Phải → trái</option><option value="ltr">Trái → phải</option></select></label>
          <label title="Chữ đè thẳng lên tranh (SFX, webtoon) mà không tách được nét: giữ nguyên ảnh gốc hay che hộp trắng rồi viết chữ dịch">SFX đè tranh <select value={sfxPolicy(project.settings)} onChange={(e) => saveProject({ settings: { ...project.settings, sfx_policy: e.target.value } })}>{SFX_POLICIES.map((p) => <option key={p.code} value={p.code}>{p.label}</option>)}</select></label>
          <span style={{ marginLeft: "auto" }} className="row">
            <button onClick={() => exportImages("zip")} title="Ảnh đã dịch + JSON + TXT của mọi trang">Tải ZIP đầy đủ</button>
            <a href={api.exportUrl(id, "json")} target="_blank" rel="noreferrer"><button>JSON</button></a>
            <a href={api.exportUrl(id, "txt")} target="_blank" rel="noreferrer"><button>TXT</button></a>
          </span>
        </div>
        {err && <p className="err">{err}</p>}
      </div>

      <div className="panel">
        <h3>Chạy xử lý</h3>
        <div className="row">
          <label>Phạm vi <select value={scope} onChange={(e) => setScope(e.target.value as Job["scope"])}><option value="all">Toàn bộ lô</option><option value="page">Một trang</option></select></label>
          {scope === "page" && <select value={pageId} onChange={(e) => setPageId(e.target.value)}><option value="">— chọn trang —</option>{project.pages.map((p) => <option key={p.id} value={p.id}>{p.position + 1}. {p.display_name}</option>)}</select>}
          <label>Bước <select value={step} onChange={(e) => setStep(e.target.value as Job["step"])}><option value="all">OCR → dịch → dựng</option><option value="ocr">Chỉ OCR</option><option value="translate">Chỉ dịch</option><option value="render">Chỉ dựng ảnh</option></select></label>
          {needsProvider && (
            <label>Nhà cung cấp dịch <select value={providerId} onChange={(e) => setProviderId(e.target.value)}><option value="">— chọn —</option>{providers.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.provider}{p.model_id ? `: ${p.model_id}` : ""})</option>)}</select></label>
          )}
          <button className="primary" disabled={(needsProvider && !providerId) || (scope === "page" && !pageId) || project.pages.length === 0} onClick={run}>Chạy</button>
          {providers.length === 0 && needsProvider && <Link to="/providers">Thêm nhà cung cấp</Link>}
        </div>
        {provider && needsProvider && (
          <p className="warn" style={{ fontSize: 12 }}>
            Dữ liệu văn bản OCR sẽ được gửi tới <b>{provider.provider}</b>{provider.endpoint ? ` (${provider.endpoint})` : ""}. Ảnh không được gửi.
            {estimate && <> Ước lượng: {estimate.regions} vùng, {estimate.characters} ký tự. {estimate.note.split(". ").slice(-1)[0]}</>}
          </p>
        )}
        <JobList jobs={jobs} onChanged={load} providerId={providerId} />
      </div>

      <div className="panel">
        <div className="row">
          <h3 style={{ margin: 0 }}>Trang ({project.pages.length})</h3>
          <input ref={fileRef} type="file" multiple accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={(e) => upload(e.target.files)} />
          {busy && <span className="muted">Đang tải lên…</span>}
          {active && <span className="muted">Đang xử lý…</span>}
        </div>
        {project.pages.length > 0 && (
          <div className="row" style={{ marginTop: 8, fontSize: 13 }}>
            <span className="muted">Tải ảnh:</span>
            <button disabled={rendered.length === 0} onClick={() => exportImages("png")} title="ZIP các ảnh đã dựng bản dịch (chỉ tính ảnh còn hợp lệ; ảnh cũ được dựng lại khi tải)">Ảnh đã dịch ({rendered.length})</button>
            <button onClick={() => exportImages("png", undefined, true)} title="ZIP mọi trang: ảnh đã dịch, trang chưa dịch lấy ảnh gốc (hậu tố _original)">Toàn bộ ảnh ({project.pages.length})</button>
            <button disabled={selectedIds.length === 0} onClick={() => exportImages("png", selectedIds, true)} title="ZIP các trang đã tick">Ảnh đã chọn ({selectedIds.length})</button>
            <span className="muted" style={{ marginLeft: 8 }}>Chọn:</span>
            <button onClick={() => selectAll("all")}>Tất cả</button>
            <button onClick={() => selectAll("rendered")} disabled={rendered.length === 0}>Đã dịch</button>
            <button onClick={() => selectAll("none")} disabled={selectedIds.length === 0}>Bỏ chọn</button>
          </div>
        )}
        <div className="grid" style={{ marginTop: 8 }}>
          {project.pages.map((p) => (
            <div className={`thumb${selected.has(p.id) ? " selected" : ""}`} key={p.id}>
              <Link to={`/projects/${id}/pages/${p.id}`}><img src={p.has_render ? api.pageRenderUrl(p.id, p.updated_at) : api.pageImageUrl(p.id)} alt={p.display_name} /></Link>
              <div className="row" style={{ gap: 4 }}>
                <input type="checkbox" checked={selected.has(p.id)} onChange={() => toggle(p.id)} title="Chọn trang" />
                <b>{p.position + 1}.</b> <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.display_name}</span>
              </div>
              <div className="muted">{p.width}×{p.height} · <span className={p.status === "failed" ? "err" : ""}>{p.status}</span>
                {p.detected_lang && <> · {LANGS[p.source_lang_override ?? p.detected_lang] ?? p.detected_lang}{p.lang_uncertain && !p.source_lang_override && <span className="warn"> (chưa chắc)</span>}</>}
              </div>
              {p.error && <div className="err">{p.error}</div>}
              {p.render_report?.warnings.length ? <div className="warn">{p.render_report.warnings.length} cảnh báo dựng{p.render_report.kept ? ` · ${p.render_report.kept} vùng giữ nguyên` : ""}</div> : null}
              {p.translation_stale && <div className="warn" title="Ngôn ngữ đích, từ điển hoặc văn bản gốc đã đổi sau lần dịch gần nhất; tải ảnh sẽ hỏi trước khi xuất bản dịch cũ">Bản dịch đã cũ — cần dịch lại ({p.translation_stale_count} vùng)</div>}
              {p.render_stale && <div className="warn" title="Chữ hoặc bố cục đã đổi sau lần dựng gần nhất; tải ảnh sẽ dựng lại từ dữ liệu hiện tại">Ảnh dựng đã cũ — cần dựng lại</div>}
              <div className="row" style={{ marginTop: 4 }}>
                <button onClick={() => move(p, -1)} title="Lên">↑</button>
                <button onClick={() => move(p, 1)} title="Xuống">↓</button>
                {p.has_render && <a href={api.pageRenderUrl(p.id, p.updated_at)} download={pngName(p)} title="Tải ảnh đã dịch của trang này" onClick={(e) => { if (confirmStale([p]) === null) e.preventDefault(); }}><button>⤓</button></a>}
                <button className="danger" onClick={async () => { if (confirm("Xóa trang?")) { await api.deletePage(p.id); load(); } }}>×</button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="panel">
        <h3>Từ điển tên riêng / thuật ngữ</h3>
        <GlossaryEditor key={project.version} value={project.glossary} onSave={(g) => saveProject({ glossary: g })} />
      </div>
    </>
  );
}
