"use client";

/**
 * framed-sheet 迷你图表库（吸收 ant-design-pro 分析页布局 + tremor Delta 徽标/BarList 模式）。
 * 全部自绘 SVG：扁平无阴影无渐变（设计红线），零第三方依赖，静态导出安全。
 */

export function DeltaBadge({ delta, suffix = "" }: { delta: number; suffix?: string }) {
  if (!delta) return null;
  const up = delta > 0;
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[11px] font-medium tabular-nums ${
        up ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"
      }`}
    >
      {up ? "↑" : "↓"}
      {Math.abs(delta)}
      {suffix}
    </span>
  );
}

/** 面积趋势图：单序列，青蓝描边 + 8% 填充，自适应折点；空数据渲染占位 */
export function AreaTrend({
  data,
  height = 180,
  stroke = "var(--accent)",
}: {
  data: { date: string; count: number }[];
  height?: number;
  stroke?: string;
}) {
  const W = 720;
  const H = height;
  const pad = { l: 8, r: 8, t: 12, b: 18 };
  if (data.length < 2) {
    return (
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="趋势图">
        <line x1={pad.l} y1={H - pad.b} x2={W - pad.r} y2={H - pad.b} stroke="var(--frame)" />
        <text x={W / 2} y={H / 2} textAnchor="middle" fontSize="12" fill="var(--muted)">
          暂无趋势数据
        </text>
      </svg>
    );
  }
  const max = Math.max(...data.map((d) => d.count), 1);
  const n = data.length;
  const x = (i: number) => pad.l + (i / Math.max(n - 1, 1)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / max) * (H - pad.t - pad.b);
  const pts = data.map((d, i) => `${x(i)},${y(d.count)}`).join(" ");
  const area = `${pad.l},${H - pad.b} ${pts} ${x(n - 1)},${H - pad.b}`;
  const last = data[n - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="趋势图">
      <polygon points={area} fill={stroke} opacity="0.08" />
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth="1.8" />
      <line x1={pad.l} y1={H - pad.b} x2={W - pad.r} y2={H - pad.b} stroke="var(--frame)" />
      <circle cx={x(n - 1)} cy={y(last.count)} r="3" fill={stroke} />
      <text x={W - pad.r} y={pad.t + 2} textAnchor="end" fontSize="10" fill="var(--muted)">
        {last.date.slice(5)} · {last.count}
      </text>
    </svg>
  );
}

/** KPI 迷你趋势条（tremor spark 风格）：细柱序列 */
export function SparkBars({ values, width = 96, height = 28 }: { values: number[]; width?: number; height?: number }) {
  const max = Math.max(...values, 1);
  const bw = width / Math.max(values.length, 1);
  return (
    <svg width={width} height={height} aria-hidden>
      {values.map((v, i) => (
        <rect
          key={i}
          x={i * bw + 0.5}
          y={height - (v / max) * (height - 2) - 1}
          width={Math.max(bw - 1.5, 1)}
          height={(v / max) * (height - 2) + 1}
          fill="var(--accent)"
          opacity={0.25 + (0.75 * i) / Math.max(values.length - 1, 1)}
        />
      ))}
    </svg>
  );
}

/** 漏斗条（ant-design-pro 漏斗模式的扁平化）：按值宽度递减 */
export function FunnelBars({ items }: { items: { label: string; value: number }[] }) {
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <div className="space-y-2">
      {items.map((it) => (
        <div key={it.label} className="flex items-center gap-3">
          <span className="w-14 shrink-0 text-right text-[12px] text-[var(--muted)]">{it.label}</span>
          <div className="h-6 flex-1 rounded-sm bg-[var(--raised)]">
            <div
              className="flex h-full items-center justify-end rounded-sm bg-[var(--accent)] px-2"
              style={{ width: `${Math.max((it.value / max) * 100, 2)}%`, opacity: 0.85 }}
            >
              <span className="text-[11px] font-medium tabular-nums text-white">{it.value}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/** BarList（tremor 模式）：横条 + 名称 + 值，行尾色块 */
export function BarList({ items }: { items: { name: string; value: number }[] }) {
  const max = Math.max(...items.map((i) => i.value), 1);
  const palette = ["var(--accent)", "#5a8fa3", "#89aab6", "#b8c9d1", "#d8e1e5", "#e8edef"];
  return (
    <div className="space-y-1.5">
      {items.map((it, i) => (
        <div key={it.name} className="flex items-center gap-2.5">
          <span className="w-20 shrink-0 truncate text-[12px] text-[var(--ink)]" title={it.name}>
            {it.name}
          </span>
          <div className="h-4 flex-1 rounded-sm bg-[var(--raised)]">
            <div
              className="h-full rounded-sm"
              style={{
                width: `${Math.max((it.value / max) * 100, 2)}%`,
                background: palette[i % palette.length],
              }}
            />
          </div>
          <span className="w-10 shrink-0 text-right text-[12px] tabular-nums text-[var(--muted)]">
            {it.value}
          </span>
        </div>
      ))}
    </div>
  );
}

/** 省份分布条（SPEC-F F4 地图的数据可得版：SVG 中国地图需省界路径，
    先以横向量条呈现，色深=数量；P3 配高德后换真地图） */
export function ProvinceBars({ items }: { items: { province: string; count: number }[] }) {
  const max = Math.max(...items.map((i) => i.count), 1);
  return (
    <div className="space-y-1">
      {items.slice(0, 10).map((it) => (
        <div key={it.province} className="flex items-center gap-2.5">
          <span className="w-14 shrink-0 truncate text-right text-[12px] text-[var(--ink)]">
            {it.province}
          </span>
          <div className="h-4 flex-1 rounded-sm bg-[var(--raised)]">
            <div
              className="h-full rounded-sm"
              style={{
                width: `${Math.max((it.count / max) * 100, 2)}%`,
                background: "var(--accent)",
                opacity: 0.35 + (0.65 * it.count) / max,
              }}
            />
          </div>
          <span className="w-8 shrink-0 text-right text-[12px] tabular-nums text-[var(--muted)]">
            {it.count}
          </span>
        </div>
      ))}
    </div>
  );
}
