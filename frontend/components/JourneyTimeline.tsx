"use client";

/**
 * 客户旅程时间线（SPEC-BT）：八源合流统一渲染——信号/电话/跟进/备注/
 * 报价/合同/回款/装机按 ts 降序，左轨色点+图标（framed-sheet 无阴影）。
 * 参照 HubSpot engagements / twenty timeline（refs/STUDY.md）。
 */
import { Tag } from "@douyinfe/semi-ui";
import {
  FileText,
  Megaphone,
  MessageSquare,
  Phone,
  Receipt,
  ScrollText,
  StickyNote,
  Wrench,
  type LucideIcon,
} from "lucide-react";

export interface JourneyItem {
  id: string;
  type: "signal" | "call" | "followup" | "note" | "quote" | "contract" | "payment" | "unit";
  ts: string;
  title: string;
  detail: string;
  meta: string;
  url: string;
}

type TagColor = "cyan" | "green" | "blue" | "grey" | "orange" | "yellow" | "red" | "teal";

const TL_META: Record<string, { label: string; color: TagColor; icon: LucideIcon }> = {
  signal: { label: "信号", color: "cyan", icon: Megaphone },
  call: { label: "电话", color: "green", icon: Phone },
  followup: { label: "跟进", color: "blue", icon: MessageSquare },
  note: { label: "备注", color: "grey", icon: StickyNote },
  quote: { label: "报价", color: "yellow", icon: FileText },
  contract: { label: "合同", color: "red", icon: ScrollText },
  payment: { label: "回款", color: "orange", icon: Receipt },
  unit: { label: "装机", color: "teal", icon: Wrench },
};

const SIGNAL_CN: Record<string, string> = {
  tender_win: "中标",
  tender_buy: "公开采购",
  tender: "政采公告",
  proposal: "项目备案",
};

export default function JourneyTimeline({ items }: { items: JourneyItem[] }) {
  if (items.length === 0) {
    return (
      <div className="py-8 text-center text-[13px] text-[var(--muted)]">
        暂无旅程记录 —— 信号/电话/跟进/合同都会按时间汇到这里
      </div>
    );
  }
  return (
    <div className="relative space-y-4 border-l border-[var(--border)] pl-5">
      {items.map((it) => {
        const meta = TL_META[it.type] ?? TL_META.note;
        const Icon = meta.icon;
        return (
          <div key={it.id} className="relative">
            <span className="absolute -left-[26px] top-0.5 flex h-[18px] w-[18px] items-center justify-center rounded-full border border-[var(--border)] bg-white text-[var(--muted)]">
              <Icon size={10} />
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <Tag color={meta.color} type="light" size="small">
                {it.type === "signal" ? (SIGNAL_CN[it.meta] ?? meta.label) : meta.label}
              </Tag>
              {it.url ? (
                <a
                  href={it.url}
                  target="_blank"
                  rel="noreferrer"
                  className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-[var(--ink)] hover:text-[var(--accent)]"
                  title={it.title}
                >
                  {it.title}
                </a>
              ) : (
                <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-[var(--ink)]" title={it.title}>
                  {it.title}
                </span>
              )}
              <span className="shrink-0 text-[12px] tabular-nums text-[var(--muted)]">
                {it.ts.replace("T", " ") || "时间未知"}
              </span>
            </div>
            {it.detail && (
              <div className="mt-1 border-l-2 border-[var(--accent)] pl-2 text-[12px] leading-4 text-[var(--muted)]">
                {it.detail}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
