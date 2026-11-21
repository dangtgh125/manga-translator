import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type Box, type Job, type Page, type Point, type ProviderProfile, type Region } from "../api";
import { RegionCanvas } from "../components/RegionCanvas";
import { useJobs } from "../components/JobPanel";

const LANGS = [["ja", "Nhật"], ["zh-Hans", "Trung Giản thể"], ["zh-Hant", "Trung Phồn thể"], ["unknown", "Chưa rõ"]] as const;

export default function EditorPage() {
  const { id = "", pageId = "" } = useParams();
  const [page, setPage] = useState<Page | null>(null);
  const [regions, setRegions] = useState<Region[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<"select" | "add">("select");
  const [showRender, setShowRender] = useState(false);
  const [showBoxes, setShowBoxes] = useState(true);
  const [scale, setScale] = useState(0.6);
  const [providers, setProviders] = useState<ProviderProfile[]>([]);
  const [providerId, setProviderId] = useState("");
  const [targetLang, setTargetLang] = useState("");
  const [err, setErr] = useState("");
  const jobs = useJobs(id, true);
  const pageJobs = useMemo(() => jobs.filter((j) => j.scope === "all" || j.page_id === pageId), [jobs, pageId]);
  const active = pageJobs.some((j) => j.status === "running" || j.status === "queued");

  const load = useCallback(async () => {
    try { const [pg, rg] = await Promise.all([api.getPage(pageId), api.listRegions(pageId)]); setPage(pg); setRegions(rg); }
    catch (e) { setErr((e as Error).message); }
  }, [pageId]);
  useEffect(() => { load(); api.listProviders().then(setProviders).catch(() => undefined); }, [load]);
  useEffect(() => { api.getProject(id).then((p) => setTargetLang(p.target_lang)).catch(() => undefined); }, [id, active]);
  useEffect(() => { if (!providerId && providers.length) setProviderId(providers[0].id); }, [providers, providerId]);
  useEffect(() => { if (!active) load(); }, [active, load]);

  // Region edits may outdate the page render; refresh the stale hint without reloading everything.
  const refreshPage = () => { api.getPage(pageId).then(setPage).catch(() => undefined); };
  const patchRegion = async (r: Region, body: Partial<Region>) => {
    try {
      const updated = await api.updateRegion(pageId, r.id, { ...body, version: r.version });
      setRegions((rs) => rs.map((x) => (x.id === r.id ? updated : x))); setErr("");
      refreshPage();
    }
    catch (e) { if (e instanceof ApiError && e.status === 409) { setErr("Vùng đã bị sửa ở nơi khác; đã tải lại."); load(); } else setErr((e as Error).message); }
  };
  const byId = (rid: string) => regions.find((r) => r.id === rid);
  const reorder = async (r: Region, delta: number) => {
    const ids = regions.map((x) => x.id); const i = ids.indexOf(r.id); const j = i + delta; if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    try { setRegions(await api.reorderRegions(pageId, ids)); refreshPage(); } catch (e) { setErr((e as Error).message); }
  };
  const retry = async (step: Job["step"], extra: Record<string, unknown> = {}) => {
    setErr("");
    try { await api.retryPage(pageId, { step, provider_profile_id: providerId || undefined, options: extra }); } catch (e) { setErr((e as Error).message); }
  };
  const setLang = async (lang: string) => {
    if (!page) return;
    try { setPage(await api.updatePage(pageId, lang === "" ? { version: page.version, clear_source_lang_override: true } : { version: page.version, source_lang_override: lang })); await load(); }
    catch (e) { setErr((e as Error).message); }
  };
  if (!page) return <p className={err ? "err" : "muted"}>{err || "Đang tải…"}</p>;
  const sel = selected ? byId(selected) : undefined;
  const imageUrl = showRender && page.has_render ? api.pageRenderUrl(pageId, page.updated_at) : api.pageImageUrl(pageId);
  const warnings = page.render_report?.warnings ?? [];

  return (
    <>
      <div className="panel">
        <div className="row">
          <Link to={`/projects/${id}`}>← Dự án</Link>
          <b>{page.position + 1}. {page.display_name}</b>
          <span className="muted">{page.width}×{page.height} · {page.status}</span>
          <label>Ngôn ngữ nguồn trang
            <select value={page.source_lang_override ?? ""} onChange={(e) => setLang(e.target.value)}>
              <option value="">Tự nhận: {page.detected_lang ?? "?"}{page.lang_uncertain ? " (chưa chắc)" : ""}</option>
              {LANGS.map(([v, l]) => <option key={v} value={v}>{l} (ép)</option>)}
            </select>
          </label>
          <span style={{ marginLeft: "auto" }} className="row">
            <button disabled={active} onClick={() => retry("ocr")}>OCR lại trang</button>
            <button disabled={active} onClick={() => retry("ocr", { force_manga_ocr: true })} title="Dùng manga-ocr cho mọi vùng">OCR (manga-ocr)</button>
            <select value={providerId} onChange={(e) => setProviderId(e.target.value)}><option value="">— nhà cung cấp —</option>{providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
            <button disabled={active || !providerId} onClick={() => retry("translate")}>Dịch lại trang</button>
            <button disabled={active || !providerId} onClick={() => retry("translate", { only_stale: true })}>Dịch vùng đã sửa</button>
            <button disabled={active} className="primary" onClick={() => retry("render")}>Dựng ảnh</button>
          </span>
        </div>
        <div className="row" style={{ marginTop: 6 }}>
          <button className={mode === "select" ? "primary" : ""} onClick={() => setMode("select")}>Chọn/sửa</button>
          <button className={mode === "add" ? "primary" : ""} onClick={() => setMode("add")}>+ Vẽ vùng mới</button>
          <label><input type="checkbox" checked={showRender} disabled={!page.has_render} onChange={(e) => setShowRender(e.target.checked)} /> Xem ảnh đã dựng</label>
          <label><input type="checkbox" checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} /> Hiện hộp nền/chữ</label>
          <label>Zoom <input type="range" min={0.2} max={2} step={0.1} value={scale} onChange={(e) => setScale(Number(e.target.value))} /></label>
          {active && <span className="muted">Đang xử lý trang…</span>}
          {page.render_stale && <span className="warn" title="Chữ hoặc bố cục đã đổi sau lần dựng gần nhất">Ảnh dựng đã cũ — hãy dựng lại</span>}
          {page.error && <span className="err">Lỗi: {page.error}</span>}
          {err && <span className="err">{err}</span>}
        </div>
        {warnings.length > 0 && <div className="warn" style={{ fontSize: 12, marginTop: 4 }}>Cảnh báo dựng: {warnings.map((w) => `#${(byId(w.region_id)?.reading_order ?? -1) + 1} ${w.message}`).join(" · ")}</div>}
      </div>

      <div className="editor">
        <div className="canvas-wrap">
          <RegionCanvas imageUrl={imageUrl} width={page.width} height={page.height} regions={regions} selectedId={selected} scale={scale} mode={mode} showBoxes={showBoxes}
            onSelect={setSelected}
            onPolygonChange={(rid, polygon: Point[]) => { const r = byId(rid); if (r) patchRegion(r, { polygon }); }}
            onBoxChange={(rid, kind, box: Box) => { const r = byId(rid); if (r) patchRegion(r, { [kind]: box } as Partial<Region>); }}
            onAddRegion={async (polygon) => { try { const r = await api.createRegion(pageId, { polygon }); setRegions((rs) => [...rs, r]); setSelected(r.id); setMode("select"); } catch (e) { setErr((e as Error).message); } }} />
        </div>
        <div className="region-list">
          {regions.length === 0 && <p className="muted">Chưa có vùng chữ. Chạy OCR hoặc vẽ vùng thủ công.</p>}
          {regions.map((r) => (
            <RegionItem targetLang={targetLang} key={r.id} r={r} selected={r.id === selected} onSelect={() => setSelected(r.id)} onPatch={(b) => patchRegion(r, b)}
              onMove={(d) => reorder(r, d)} onDelete={async () => { await api.deleteRegion(pageId, r.id); setRegions((rs) => rs.filter((x) => x.id !== r.id)); refreshPage(); }} />
          ))}
          {sel && <p className="muted" style={{ fontSize: 12 }}>Kéo đỉnh để sửa đa giác; kéo hộp đỏ (nền) / tím (chữ) để định vị lớp chữ dịch.</p>}
        </div>
      </div>
    </>
  );
}

function RegionItem({ r, selected, targetLang, onSelect, onPatch, onMove, onDelete }: { r: Region; selected: boolean; targetLang: string; onSelect: () => void; onPatch: (b: Partial<Region>) => void; onMove: (d: number) => void; onDelete: () => void }) {
  // A hand-written translation belongs to the language the project targeted at the time.
  const finalLangMismatch = r.final_edited && !!r.final_text && !!r.final_lang && !!targetLang && r.final_lang !== targetLang;
  const [src, setSrc] = useState(r.source_text);
  const [fin, setFin] = useState(r.final_text);
  useEffect(() => { setSrc(r.source_text); setFin(r.final_text); }, [r.source_text, r.final_text, r.version]);
  return (
    <div className={`region-item ${selected ? "selected" : ""}`} onClick={onSelect}>
      <div className="row">
        <b>#{r.reading_order + 1}</b>
        <button onClick={(e) => { e.stopPropagation(); onMove(-1); }}>↑</button>
        <button onClick={(e) => { e.stopPropagation(); onMove(1); }}>↓</button>
        <select value={r.direction} onChange={(e) => onPatch({ direction: e.target.value as Region["direction"] })}><option value="vertical">Dọc</option><option value="horizontal">Ngang</option><option value="unknown">?</option></select>
        <select value={r.source_lang} onChange={(e) => onPatch({ source_lang: e.target.value })}>{LANGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        {r.lang_uncertain && <span className="warn badge">ngôn ngữ chưa chắc</span>}
        <span className="badge" title={r.ocr_engine}>OCR {Math.round(r.ocr_confidence * 100)}%</span>
        {r.translation_stale && <span className="warn badge">cần dịch lại</span>}
        {r.translation_flags.map((f) => <span key={f} className="warn badge">{f}</span>)}
        <button className="danger" style={{ marginLeft: "auto" }} onClick={(e) => { e.stopPropagation(); if (confirm("Xóa vùng?")) onDelete(); }}>×</button>
      </div>
      {r.ocr_text !== r.source_text && <div className="muted" style={{ fontSize: 12 }}>OCR gốc: {r.ocr_text}</div>}
      <label className="small">Văn bản gốc (sửa được)</label>
      <textarea value={src} onChange={(e) => setSrc(e.target.value)} onBlur={() => src !== r.source_text && onPatch({ source_text: src })} />
      {r.translated_text && r.translated_text !== r.final_text && <div className="muted" style={{ fontSize: 12 }}>Máy dịch ({r.translation_provider}): {r.translated_text}</div>}
      <label className="small">Bản dịch (sửa được){r.final_edited && r.final_lang && <span className="muted"> · bản sửa tay [{r.final_lang}]</span>}</label>
      <textarea value={fin} onChange={(e) => setFin(e.target.value)} onBlur={() => fin !== r.final_text && onPatch({ final_text: fin })} />
      {finalLangMismatch && (
        <div className="warn" style={{ fontSize: 12 }}>
          Bản sửa tay viết cho ngôn ngữ [{r.final_lang}], dự án đang dịch sang [{targetLang}].
          {r.translated_text && <> <button onClick={(e) => { e.stopPropagation(); onPatch({ final_text: r.translated_text }); }}>Dùng bản máy dịch</button></>}
        </div>
      )}
      <div className="row" style={{ marginTop: 4 }}>
        <select value={r.review_status} onChange={(e) => onPatch({ review_status: e.target.value as Region["review_status"] })}><option value="pending">Chưa duyệt</option><option value="approved">Đã duyệt</option><option value="flagged">Cần xem</option></select>
        <select value={r.mask_mode} onChange={(e) => onPatch({ mask_mode: e.target.value as Region["mask_mode"] })}><option value="auto">Nền: tự động</option><option value="box">Nền: hộp trắng</option><option value="none">Nền: không che</option></select>
        <label>Cỡ chữ <input type="number" style={{ width: 64 }} min={4} max={200} value={r.font_size ?? ""} placeholder="auto" onChange={(e) => onPatch({ font_size: e.target.value ? Number(e.target.value) : null })} /></label>
      </div>
    </div>
  );
}
