import { useState } from "react";
import type { GlossaryEntry } from "../api";

export function GlossaryEditor({ value, onSave }: { value: GlossaryEntry[]; onSave: (g: GlossaryEntry[]) => Promise<void> }) {
  const [rows, setRows] = useState<GlossaryEntry[]>(value);
  const [busy, setBusy] = useState(false);
  const set = (i: number, k: keyof GlossaryEntry, v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <div>
      <table>
        <thead><tr><th>Tên/thuật ngữ gốc</th><th>Bản dịch cố định</th><th>Ghi chú</th><th></th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td><input value={r.source} onChange={(e) => set(i, "source", e.target.value)} /></td>
              <td><input value={r.target} onChange={(e) => set(i, "target", e.target.value)} /></td>
              <td><input value={r.note} onChange={(e) => set(i, "note", e.target.value)} /></td>
              <td><button onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={() => setRows([...rows, { source: "", target: "", note: "" }])}>+ Thêm</button>
        <button className="primary" disabled={busy} onClick={async () => { setBusy(true); try { await onSave(rows.filter((r) => r.source && r.target)); } finally { setBusy(false); } }}>Lưu từ điển</button>
        <span className="muted">Với AI: đưa vào ngữ cảnh dịch. Với Google: chỉ dùng glossary Cloud Translation Advanced nếu cấu hình; sau dịch sẽ đối chiếu và gắn cờ sai lệch.</span>
      </div>
    </div>
  );
}
