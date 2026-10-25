"use client";

/** 商机卡片（从 leads/page.tsx 提取——修内嵌组件每次渲染重建问题，SPEC-E E4）。 */
import { Tag } from "@douyinfe/semi-ui";
import type { Lead } from "@/lib/api";

const GRADE_TAG: Record<string, "red" | "orange" | "blue"> = { S: "red", A: "orange", B: "blue" };

export default function LeadCard({
  lead,
  dragging,
  onDragStart,
  onDragEnd,
  onOpen,
}: {
  lead: Lead;
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onOpen: () => void;
}) {
  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={`mb-2 cursor-grab rounded-lg border bg-white px-3 py-2.5 transition-shadow hover:border-[var(--accent)] ${
        dragging ? "opacity-40" : "border-[var(--border)]"
      }`}
      onClick={onOpen}
    >
      <div className="flex items-start justify-between gap-2">
        <div
          className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--ink)]"
          title={lead.enterprise?.name}
        >
          {lead.enterprise?.name}
        </div>
        {lead.grade && (
          <Tag color={GRADE_TAG[lead.grade]} type="solid" size="small">
            {lead.grade}
          </Tag>
        )}
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[11.5px] text-[var(--muted)]">
        <span className="tabular-nums">
          {lead.amount_wan != null ? `${Math.round(lead.amount_wan)} 万` : "金额待估"}
          {lead.expected_close ? ` · ${lead.expected_close.slice(5)}` : ""}
        </span>
        <span className="tabular-nums">{lead.probability ?? 0}%</span>
      </div>
      {/* 赢率条（twenty probability 可视化） */}
      <div className="mt-1 h-1 rounded-full bg-[var(--raised)]">
        <div
          className="h-full rounded-full bg-[var(--accent)]"
          style={{ width: `${Math.min(lead.probability ?? 0, 100)}%` }}
        />
      </div>
      {lead.next_action && (
        <div
          className="mt-1.5 truncate text-[11px] leading-4 text-[var(--muted)]"
          title={lead.next_action}
        >
          下一步：{lead.next_action}
        </div>
      )}
    </div>
  );
}
