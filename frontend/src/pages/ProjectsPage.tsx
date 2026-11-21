import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ProjectSummary } from "../api";
import { TARGET_LANGS } from "../langs";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("vi");
  const [dir, setDir] = useState<"rtl" | "ltr">("rtl");
  const [err, setErr] = useState("");
  const load = () => api.listProjects().then(setProjects).catch((e) => setErr(String(e.message)));
  useEffect(() => { load(); }, []);
  const create = async () => {
    setErr("");
    try { await api.createProject({ name: name.trim() || "Dự án mới", target_lang: target, reading_direction: dir }); setName(""); load(); }
    catch (e) { setErr((e as Error).message); }
  };
  const remove = async (p: ProjectSummary) => {
    if (!confirm(`Xóa dự án "${p.name}" và toàn bộ ảnh?`)) return;
    try { await api.deleteProject(p.id); load(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <>
      <div className="panel">
        <h2>Dự án mới</h2>
        <div className="row">
          <input placeholder="Tên dự án" value={name} onChange={(e) => setName(e.target.value)} />
          <label>Ngôn ngữ đích <select value={target} onChange={(e) => setTarget(e.target.value)}>{TARGET_LANGS.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}</select></label>
          <label>Hướng đọc <select value={dir} onChange={(e) => setDir(e.target.value as "rtl" | "ltr")}><option value="rtl">Phải → trái (manga)</option><option value="ltr">Trái → phải</option></select></label>
          <button className="primary" onClick={create}>Tạo</button>
        </div>
        {err && <p className="err">{err}</p>}
      </div>
      <div className="panel">
        <h2>Dự án</h2>
        {projects.length === 0 && <p className="muted">Chưa có dự án.</p>}
        <table><tbody>
          {projects.map((p) => (
            <tr key={p.id}>
              <td><Link to={`/projects/${p.id}`}>{p.name}</Link></td>
              <td className="muted">{p.page_count} trang · đích: {p.target_lang}</td>
              <td className="muted">{new Date(p.updated_at).toLocaleString()}</td>
              <td><button className="danger" onClick={() => remove(p)}>Xóa</button></td>
            </tr>
          ))}
        </tbody></table>
      </div>
    </>
  );
}
