/**
 * 客户地图图例（区域作战工具）：右上角悬浮小卡片。
 * framed-sheet：白底浅框、扁平无阴影、色彩只给数据标记。
 * points 模式三行：高意图 / 有电话 / 无电话；clusters 模式一行：圆面积=企业数。
 */

function LegendDot(props: { size: number; fill: string; stroke?: string; strokeWidth?: number }) {
  return (
    <span
      aria-hidden
      className="inline-block shrink-0 rounded-full"
      style={{
        width: props.size,
        height: props.size,
        background: props.fill,
        border: props.stroke
          ? `${props.strokeWidth ?? 1.5}px solid ${props.stroke}`
          : undefined,
        boxSizing: "border-box",
      }}
    />
  );
}

export default function MapLegend({ mode }: { mode: "points" | "clusters" }) {
  return (
    <div className="pointer-events-none absolute right-3 top-3 z-[1000] select-none rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[12px] leading-6 text-[var(--muted)]">
      {mode === "clusters" ? (
        <div className="flex items-center gap-2">
          <LegendDot size={14} fill="rgba(94,106,210,0.45)" stroke="#5e6ad2" strokeWidth={1} />
          <span>圆面积 = 企业数</span>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <LegendDot size={14} fill="#5e6ad2" stroke="#5e6ad2" strokeWidth={2} />
            <span>
              高意图 <span className="font-medium text-[var(--ink)]">≥80</span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            <LegendDot size={10} fill="#5e6ad2" />
            <span>有电话 · 可拨打</span>
          </div>
          <div className="flex items-center gap-2">
            <LegendDot size={8} fill="#e8ecee" stroke="#9aa5a9" strokeWidth={1} />
            <span>无电话</span>
          </div>
        </>
      )}
    </div>
  );
}
