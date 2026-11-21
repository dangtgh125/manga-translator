import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { api, type Health } from "./api";
import ProjectsPage from "./pages/ProjectsPage";
import ProjectPage from "./pages/ProjectPage";
import EditorPage from "./pages/EditorPage";
import ProvidersPage from "./pages/ProvidersPage";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    const tick = () => api.health().then(setHealth).catch(() => setHealth(null));
    tick();
    const t = setInterval(tick, 15000);
    return () => clearInterval(t);
  }, []);
  return (
    <>
      <header className="top">
        <Link to="/" className="brand" aria-label="Manga Translator">
          <img src="/logo.svg" alt="" width={40} height={40} />
          <span className="brand-text">
            <span className="brand-name">Manga Translator</span>
            <span className="brand-sub">マンガ翻訳 · OCR → dịch → dựng</span>
          </span>
        </Link>
        <nav className="nav">
          <Link to="/">Dự án</Link>
          <Link to="/providers">Nhà cung cấp dịch</Link>
        </nav>
        <span className="muted status" style={{ marginLeft: "auto" }}>
          {health ? (
            <>
              API v{health.version} · OCR: {health.ocr_engine} · worker: {health.worker_alive === null ? "?" : health.worker_alive ? <span className="ok">sống</span> : <span className="err">không phản hồi</span>}
              {!health.secrets_available && <span className="warn"> · chưa có master key (không lưu được API key)</span>}
            </>
          ) : <span className="err">API không phản hồi</span>}
        </span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ProjectsPage />} />
          <Route path="/projects/:id" element={<ProjectPage />} />
          <Route path="/projects/:id/pages/:pageId" element={<EditorPage />} />
          <Route path="/providers" element={<ProvidersPage />} />
        </Routes>
      </main>
    </>
  );
}
