import { useEffect, useState } from "react";
import { api, type ProviderKind, type ProviderProfile, type ProviderTestResult } from "../api";

const KINDS: { v: ProviderKind; label: string; secretLabel: string; hint: string }[] = [
  { v: "google", label: "Google Cloud Translation v3", secretLabel: "Service-account JSON (quyền tối thiểu Cloud Translation)", hint: "Tùy chọn: project_id, location (global), glossary_id" },
  { v: "openai", label: "OpenAI Responses API", secretLabel: "API key", hint: "Model ID ví dụ gpt-5. Gửi store=false. Tùy chọn: timeout_seconds, reasoning_effort." },
  { v: "anthropic", label: "Anthropic Messages API", secretLabel: "API key", hint: "Model ID ví dụ claude-opus-5-5." },
  { v: "responses_compatible", label: "Endpoint tương thích Responses API", secretLabel: "API key (nếu có)", hint: "Chỉ HTTPS; host phải nằm trong allowlist (MT_ENDPOINT_ALLOWLIST). Tùy chọn: timeout_seconds (5–600), structured_output auto|json_schema|none, reasoning_effort." },
];

export default function ProvidersPage() {
  const [list, setList] = useState<ProviderProfile[]>([]);
  const [err, setErr] = useState("");
  const [form, setForm] = useState({ name: "", provider: "openai" as ProviderKind, model_id: "", endpoint: "", secret: "", options: "{}" });
  const [test, setTest] = useState<ProviderTestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.listProviders().then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const kind = KINDS.find((k) => k.v === form.provider)!;
  const parseOptions = () => { try { return JSON.parse(form.options || "{}") as Record<string, unknown>; } catch { throw new Error("Options phải là JSON hợp lệ"); } };
  const doTest = async () => {
    setBusy(true); setErr(""); setTest(null);
    try {
      const res = await api.testProvider({ provider: form.provider, model_id: form.model_id, endpoint: form.endpoint || undefined, options: parseOptions(), secret: form.secret || undefined });
      setTest(res);
      if (!form.model_id && res.models.length) setForm((f) => (f.model_id ? f : { ...f, model_id: res.models[0] }));
    }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true); setErr("");
    try {
      await api.createProvider({ name: form.name || kind.label, provider: form.provider, model_id: form.model_id, endpoint: form.endpoint || null, options: parseOptions(), secret: form.secret || null });
      setForm({ ...form, name: "", secret: "" }); setTest(null); load();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <>
      <div className="panel">
        <h2>Thêm nhà cung cấp dịch</h2>
        <div className="row">
          <select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value as ProviderKind })}>{KINDS.map((k) => <option key={k.v} value={k.v}>{k.label}</option>)}</select>
          <input placeholder="Tên hồ sơ" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input placeholder="Model ID" list="model-options" value={form.model_id} onChange={(e) => setForm({ ...form, model_id: e.target.value })} />
          <datalist id="model-options">{(test?.models ?? []).map((m) => <option key={m} value={m} />)}</datalist>
          {form.provider === "responses_compatible" && <input style={{ minWidth: 320 }} placeholder="https://host/v1" value={form.endpoint} onChange={(e) => setForm({ ...form, endpoint: e.target.value })} />}
        </div>
        <label className="small">{kind.secretLabel} — được mã hóa ở backend; không bao giờ hiển thị lại</label>
        <textarea value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} autoComplete="off" spellCheck={false} />
        <label className="small">Tùy chọn (JSON) — {kind.hint}</label>
        <input style={{ width: "100%" }} value={form.options} onChange={(e) => setForm({ ...form, options: e.target.value })} />
        <div className="row" style={{ marginTop: 8 }}>
          <button disabled={busy} onClick={doTest}>Kiểm tra kết nối</button>
          <button className="primary" disabled={busy} onClick={save}>Lưu hồ sơ</button>
        </div>
        {test && (
          <p className={test.ok ? "ok" : "err"}>
            {test.ok ? "Kết nối OK" : "Thất bại"}: {test.message}. Dữ liệu sẽ gửi tới: {test.data_recipient}.
            {test.models.length > 0 && <span className="muted"> Model khả dụng: {test.models.slice(0, 15).join(", ")}{test.models.length > 15 ? "…" : ""}</span>}
          </p>
        )}
        {err && <p className="err">{err}</p>}
      </div>
      <div className="panel">
        <h2>Hồ sơ đã lưu</h2>
        <table>
          <thead><tr><th>Tên</th><th>Loại</th><th>Model</th><th>Endpoint</th><th>Key</th><th></th></tr></thead>
          <tbody>
            {list.map((p) => <ProviderRow key={p.id} p={p} onChanged={load} onError={setErr} />)}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ProviderRow({ p, onChanged, onError }: { p: ProviderProfile; onChanged: () => void; onError: (m: string) => void }) {
  const [res, setRes] = useState<ProviderTestResult | null>(null);
  const [model, setModel] = useState(p.model_id);
  return (
    <tr>
      <td>{p.name}</td>
      <td>{p.provider}</td>
      <td><input value={model} onChange={(e) => setModel(e.target.value)} onBlur={() => model !== p.model_id && api.updateProvider(p.id, { model_id: model }).then(onChanged).catch((e) => onError(e.message))} /></td>
      <td className="muted">{p.endpoint ?? "—"}</td>
      <td>{p.has_secret ? <span className="ok">đã lưu</span> : <span className="warn">chưa có</span>}
        <button style={{ marginLeft: 6 }} onClick={() => { const s = prompt("Nhập key/credential mới (đổi key):"); if (s) api.setProviderSecret(p.id, s).then(onChanged).catch((e) => onError(e.message)); }}>đổi</button>
        {p.has_secret && <button onClick={() => api.deleteProviderSecret(p.id).then(onChanged)}>xóa key</button>}
      </td>
      <td>
        <button onClick={() => api.testProvider({ profile_id: p.id }).then(setRes).catch((e) => onError(e.message))}>Kiểm tra</button>
        <button className="danger" onClick={() => confirm("Xóa hồ sơ?") && api.deleteProvider(p.id).then(onChanged)}>Xóa</button>
        {res && <div className={res.ok ? "ok" : "err"} style={{ fontSize: 12 }}>{res.message}</div>}
      </td>
    </tr>
  );
}
