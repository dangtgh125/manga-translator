import { useRef, useState, type MouseEvent as RMouseEvent } from "react";
import type { Box, Point, Region } from "../api";

interface Props {
  imageUrl: string; width: number; height: number; regions: Region[]; selectedId: string | null; scale: number;
  mode: "select" | "add"; showBoxes: boolean;
  onSelect: (id: string | null) => void;
  onPolygonChange: (id: string, polygon: Point[]) => void;
  onBoxChange: (id: string, kind: "mask_box" | "text_box", box: Box) => void;
  onAddRegion: (polygon: Point[]) => void;
}

type Drag =
  | { kind: "vertex"; id: string; index: number }
  | { kind: "move"; id: string; start: Point; orig: Point[] }
  | { kind: "box-move"; id: string; box: "mask_box" | "text_box"; start: Point; orig: Box }
  | { kind: "box-resize"; id: string; box: "mask_box" | "text_box"; orig: Box }
  | { kind: "draw"; start: Point; cur: Point };

export function RegionCanvas(p: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [local, setLocal] = useState<Record<string, Point[]>>({});
  const [localBox, setLocalBox] = useState<Record<string, Box>>({});

  const toImg = (e: RMouseEvent): Point => {
    const r = svgRef.current!.getBoundingClientRect();
    const x = Math.max(0, Math.min(p.width, (e.clientX - r.left) / p.scale));
    const y = Math.max(0, Math.min(p.height, (e.clientY - r.top) / p.scale));
    return [Math.round(x), Math.round(y)];
  };
  const polyOf = (r: Region) => local[r.id] ?? r.polygon;
  const boxOf = (r: Region, k: "mask_box" | "text_box"): Box => localBox[`${r.id}:${k}`] ?? r[k] ?? bboxOf(polyOf(r));

  const onMouseDown = (e: RMouseEvent) => {
    if (p.mode === "add") { const pt = toImg(e); setDrag({ kind: "draw", start: pt, cur: pt }); e.preventDefault(); return; }
    if (e.target === svgRef.current || (e.target as Element).tagName === "image") p.onSelect(null);
  };
  const onMouseMove = (e: RMouseEvent) => {
    if (!drag) return;
    const pt = toImg(e);
    if (drag.kind === "vertex") {
      const reg = p.regions.find((r) => r.id === drag.id)!; const poly = polyOf(reg).map((q, i) => (i === drag.index ? pt : q));
      setLocal({ ...local, [drag.id]: poly });
    } else if (drag.kind === "move") {
      const dx = pt[0] - drag.start[0], dy = pt[1] - drag.start[1];
      setLocal({ ...local, [drag.id]: drag.orig.map(([x, y]) => [x + dx, y + dy] as Point) });
    } else if (drag.kind === "box-move") {
      const dx = pt[0] - drag.start[0], dy = pt[1] - drag.start[1];
      setLocalBox({ ...localBox, [`${drag.id}:${drag.box}`]: { ...drag.orig, x: drag.orig.x + dx, y: drag.orig.y + dy } });
    } else if (drag.kind === "box-resize") {
      setLocalBox({ ...localBox, [`${drag.id}:${drag.box}`]: { ...drag.orig, w: Math.max(8, pt[0] - drag.orig.x), h: Math.max(8, pt[1] - drag.orig.y) } });
    } else if (drag.kind === "draw") setDrag({ ...drag, cur: pt });
  };
  const onMouseUp = () => {
    if (!drag) return;
    if (drag.kind === "vertex" || drag.kind === "move") { const poly = local[drag.id]; if (poly) p.onPolygonChange(drag.id, poly); }
    else if (drag.kind === "box-move" || drag.kind === "box-resize") { const b = localBox[`${drag.id}:${drag.box}`]; if (b) p.onBoxChange(drag.id, drag.box, b); }
    else if (drag.kind === "draw") {
      const [x0, y0] = drag.start, [x1, y1] = drag.cur;
      if (Math.abs(x1 - x0) > 4 && Math.abs(y1 - y0) > 4) p.onAddRegion([[Math.min(x0, x1), Math.min(y0, y1)], [Math.max(x0, x1), Math.min(y0, y1)], [Math.max(x0, x1), Math.max(y0, y1)], [Math.min(x0, x1), Math.max(y0, y1)]]);
    }
    setDrag(null);
  };

  const W = p.width * p.scale, H = p.height * p.scale;
  return (
    <svg ref={svgRef} width={W} height={H} viewBox={`0 0 ${p.width} ${p.height}`} style={{ cursor: p.mode === "add" ? "crosshair" : "default", userSelect: "none" }}
      onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp}>
      <image href={p.imageUrl} width={p.width} height={p.height} />
      {p.regions.map((r) => {
        const poly = polyOf(r); const sel = r.id === p.selectedId; const [cx, cy] = poly[0];
        const color = r.direction === "vertical" ? "#2dd4bf" : r.direction === "horizontal" ? "#3b82f6" : "#f59e0b";
        return (
          <g key={r.id}>
            <polygon points={poly.map((q) => q.join(",")).join(" ")} fill={sel ? "rgba(74,222,128,.18)" : "rgba(255,255,255,.04)"} stroke={sel ? "#4ade80" : color} strokeWidth={sel ? 3 / p.scale : 2 / p.scale}
              style={{ cursor: "move" }}
              onMouseDown={(e) => { if (p.mode !== "select") return; e.stopPropagation(); p.onSelect(r.id); setDrag({ kind: "move", id: r.id, start: toImg(e), orig: poly }); }} />
            <text x={cx + 3} y={cy + 14 / p.scale} fontSize={14 / p.scale} fill="#fff" stroke="#000" strokeWidth={0.5 / p.scale} style={{ pointerEvents: "none" }}>{r.reading_order + 1}{r.translation_stale ? "*" : ""}</text>
            {sel && poly.map((q, i) => (
              <circle key={i} cx={q[0]} cy={q[1]} r={6 / p.scale} fill="#fff" stroke="#4ade80" strokeWidth={2 / p.scale} style={{ cursor: "pointer" }}
                onMouseDown={(e) => { e.stopPropagation(); setDrag({ kind: "vertex", id: r.id, index: i }); }} />
            ))}
            {sel && p.showBoxes && (["mask_box", "text_box"] as const).map((k) => {
              const b = boxOf(r, k); const c = k === "mask_box" ? "#ef4444" : "#a855f7";
              return (
                <g key={k}>
                  <rect x={b.x} y={b.y} width={b.w} height={b.h} fill="none" stroke={c} strokeDasharray={`${6 / p.scale} ${4 / p.scale}`} strokeWidth={2 / p.scale} style={{ cursor: "move" }}
                    onMouseDown={(e) => { e.stopPropagation(); setDrag({ kind: "box-move", id: r.id, box: k, start: toImg(e), orig: b }); }} />
                  <rect x={b.x + b.w - 8 / p.scale} y={b.y + b.h - 8 / p.scale} width={8 / p.scale} height={8 / p.scale} fill={c} style={{ cursor: "nwse-resize" }}
                    onMouseDown={(e) => { e.stopPropagation(); setDrag({ kind: "box-resize", id: r.id, box: k, orig: b }); }} />
                </g>
              );
            })}
          </g>
        );
      })}
      {drag?.kind === "draw" && (
        <rect x={Math.min(drag.start[0], drag.cur[0])} y={Math.min(drag.start[1], drag.cur[1])} width={Math.abs(drag.cur[0] - drag.start[0])} height={Math.abs(drag.cur[1] - drag.start[1])} fill="rgba(74,222,128,.2)" stroke="#4ade80" strokeWidth={2 / p.scale} />
      )}
    </svg>
  );
}

export function bboxOf(poly: Point[]): Box {
  const xs = poly.map((q) => q[0]), ys = poly.map((q) => q[1]);
  const x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}
