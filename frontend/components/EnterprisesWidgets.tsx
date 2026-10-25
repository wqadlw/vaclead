"use client";

/** 企业页子组件（SPEC-E E4：从 1005 行巨页提取——QuickPicks/SummaryBar/AdvancedFilterModal）。 */
import { useState } from "react";
import { Button, Input, Modal, Select, Tabs } from "@douyinfe/semi-ui";
import type { Enterprise, PageData } from "@/lib/api";

// ---------------------------------------------------------------------------
// QuickPicks（gmaps-scraper 一键组合筛选模式）
// ---------------------------------------------------------------------------
export interface QuickPickFilters {
  q?: string;
  industry_key?: string;
  province?: string;
  city?: string;
  district?: string;
  capital_min?: number | null;
  capital_max?: number | null;
  signal_days?: number | null;
  score_min?: number | null;
  is_competitor: number;
  source?: string;
  has_contact?: number;
}

const QUICK_PICKS: { label: string; patch: Partial<QuickPickFilters> }[] = [
  { label: "有联系电话", patch: { has_contact: 1 } },
  { label: "近期有采购", patch: { signal_days: 90 } },
  { label: "高意图", patch: { score_min: 40, signal_days: 180 } },
  { label: "官方中标数据", patch: { source: "ggzy" } },
  { label: "政采买家", patch: { source: "ccgp" } },
];

export function QuickPicks({
  filters,
  applied,
  onApply,
}: {
  filters: QuickPickFilters;
  applied: QuickPickFilters;
  onApply: (next: QuickPickFilters) => void;
}) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5">
      <span className="text-[12px] text-[var(--muted)]">快捷筛选</span>
      {QUICK_PICKS.map((pick) => {
        const active = Object.entries(pick.patch).every(
          ([k, v]) => applied[k as keyof QuickPickFilters] === v,
        );
        return (
          <button
            key={pick.label}
            onClick={() => {
              const next = { ...filters, ...pick.patch };
              if (active) {
                for (const k of Object.keys(pick.patch)) {
                  delete (next as Record<string, unknown>)[k];
                }
              }
              onApply(next);
            }}
            className={`h-7 cursor-pointer rounded-full border px-3 text-[12px] transition-colors ${
              active
                ? "border-[var(--accent)] bg-[color:var(--accent)]/5 text-[var(--accent)]"
                : "border-[var(--border)] bg-white text-[var(--ink)] hover:border-[var(--accent)]"
            }`}
          >
            {pick.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SummaryBar（twenty record-aggregate 模式）
// ---------------------------------------------------------------------------
export const AGG_OPS = [
  { value: "COUNT", label: "计数" },
  { value: "SUM", label: "求和" },
  { value: "AVG", label: "平均" },
  { value: "MIN", label: "最小" },
  { value: "MAX", label: "最大" },
  { value: "NONE", label: "无" },
];

export function aggValue(col: string, op: string, items: Enterprise[]): string {
  const vals = items
    .map((it) => {
      if (col === "signal_count") return it.signal_count ?? 0;
      if (col === "intent_score") return it.intent_score ?? 0;
      return it.registered_capital_wan ?? 0;
    })
    .filter((v) => v > 0);
  if (op === "COUNT") return String(items.length);
  if (vals.length === 0) return "—";
  switch (op) {
    case "SUM":
      return Math.round(vals.reduce((a, b) => a + b, 0)).toLocaleString("zh-CN");
    case "AVG":
      return String(Math.round(vals.reduce((a, b) => a + b, 0) / vals.length));
    case "MIN":
      return String(Math.round(Math.min(...vals)));
    case "MAX":
      return String(Math.round(Math.max(...vals)));
    default:
      return "—";
  }
}

export function SummaryBar({
  data,
  colAggs,
}: {
  data: PageData<Enterprise> | null;
  colAggs: Record<string, string>;
}) {
  const items = data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 rounded-lg border border-[var(--border)] bg-white px-4 py-2">
      <span className="text-[12.5px] text-[var(--muted)]">
        本页 {items.length} 家 · 筛选共 {data?.total ?? 0} 家
      </span>
      <span className="text-[12.5px] tabular-nums text-[var(--ink)]">
        注册资本合计{" "}
        {Math.round(
          items.reduce((a, b) => a + (b.registered_capital_wan ?? 0), 0),
        ).toLocaleString("zh-CN")}{" "}
        万
      </span>
      {Object.entries(colAggs)
        .filter(([, op]) => op !== "NONE")
        .map(([col, op]) => {
          const label =
            col === "registered_capital_wan" ? "注册资本" :
            col === "intent_score" ? "意图分" :
            col === "signal_count" ? "信号" : col;
          return (
            <span key={col} className="text-[12.5px] tabular-nums text-[var(--accent)]">
              {label}·{AGG_OPS.find((o) => o.value === op)?.label ?? op}{" "}
              {aggValue(col, op, items)}
            </span>
          );
        })}
      <span className="text-[12.5px] text-[var(--muted)]">
        （点击数值列表头可切换聚合）
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AdvancedFilterModal（twenty advanced-filter AND/OR 构建器）
// ---------------------------------------------------------------------------
export function AdvancedFilterModal({
  open,
  onClose,
  onSendToTable,
}: {
  open: boolean;
  onClose: () => void;
  onSendToTable: (name: string) => void;
}) {
  const [logic, setLogic] = useState<"and" | "or">("and");
  const [rows, setRows] = useState<{ field: string; op: string; value: string }[]>([
    { field: "icp_score", op: "gte", value: "40" },
  ]);
  const [results, setResults] = useState<{
    total: number;
    items: { id: number; name: string; icp_score: number }[];
  } | null>(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const { api } = await import("@/lib/api");
      const res = await api.post<{
        total: number;
        items: { id: number; name: string; icp_score: number }[];
      }>("/api/enterprises/advanced-filter", {
        logic,
        conditions: rows
          .filter((r) => r.field)
          .map((r) => ({
            field: r.field,
            operator: r.op,
            value: r.value === "" ? null : r.value,
          })),
      });
      setResults(res);
    } finally {
      setLoading(false);
    }
  };

  const isNum = (f: string) =>
    ["icp_score", "intent_score", "registered_capital_wan"].includes(f);
  const opsFor = (f: string) =>
    isNum(f)
      ? [
          { value: "gte", label: "≥" },
          { value: "lte", label: "≤" },
          { value: "gt", label: ">" },
          { value: "lt", label: "<" },
          { value: "eq", label: "=" },
          { value: "is_not_empty", label: "有值" },
        ]
      : [
          { value: "contains", label: "包含" },
          { value: "eq", label: "等于" },
          { value: "is_not_empty", label: "有值" },
          { value: "is_empty", label: "为空" },
        ];

  return (
    <Modal title="高级筛选" visible={open} onCancel={onClose} footer={null} width={560}>
      <div className="space-y-2.5 pt-1">
        <div className="flex items-center gap-2">
          <span className="text-[12.5px] text-[var(--muted)]">条件组合</span>
          <Tabs
            type="button"
            size="small"
            activeKey={logic}
            onChange={(k) => setLogic(k as "and" | "or")}
            tabList={[
              { tab: "全部满足 AND", itemKey: "and" },
              { tab: "任一满足 OR", itemKey: "or" },
            ]}
          />
        </div>
        {rows.map((row, idx) => (
          <div key={idx} className="flex items-center gap-1.5">
            <Select
              style={{ width: 140 }}
              size="small"
              value={row.field}
              optionList={[
                { value: "icp_score", label: "ICP 分" },
                { value: "intent_score", label: "意图分" },
                { value: "registered_capital_wan", label: "注册资本" },
                { value: "name", label: "企业名" },
                { value: "province", label: "省份" },
                { value: "city", label: "城市" },
                { value: "industry_key", label: "行业键" },
                { value: "source", label: "来源" },
              ]}
              onChange={(v) => {
                const next = [...rows];
                const num = ["icp_score", "intent_score", "registered_capital_wan"].includes(v as string);
                next[idx] = { field: v as string, op: num ? "gte" : "contains", value: "" };
                setRows(next);
              }}
            />
            <Select
              style={{ width: 88 }}
              size="small"
              value={row.op}
              optionList={opsFor(row.field).map((o) => ({ value: o.value, label: o.label }))}
              onChange={(v) => {
                const next = [...rows];
                next[idx] = { ...row, op: v as string };
                setRows(next);
              }}
            />
            {row.op !== "is_empty" && row.op !== "is_not_empty" && (
              <Input
                size="small"
                style={{ flex: 1 }}
                value={row.value}
                onChange={(v) => {
                  const next = [...rows];
                  next[idx] = { ...row, value: v };
                  setRows(next);
                }}
                placeholder={isNum(row.field) ? "数值" : "关键词"}
              />
            )}
            {rows.length > 1 && (
              <Button
                size="small"
                theme="borderless"
                type="danger"
                onClick={() => setRows(rows.filter((_, i) => i !== idx))}
              >
                删
              </Button>
            )}
          </div>
        ))}
        <div className="flex items-center justify-between">
          <Button
            size="small"
            theme="borderless"
            disabled={rows.length >= 8}
            onClick={() => setRows([...rows, { field: "icp_score", op: "gte", value: "" }])}
          >
            + 加条件（最多 8 行）
          </Button>
          <Button theme="solid" size="small" loading={loading} onClick={() => void run()}>
            执行筛选
          </Button>
        </div>
        {results && (
          <div className="max-h-[260px] overflow-auto rounded-lg border border-[var(--border)]">
            <div className="border-b border-[var(--rule)] px-3 py-1.5 text-[12px] text-[var(--muted)]">
              命中 {results.total} 家 —— 点击企业名送入主表格
            </div>
            {results.items.map((it) => (
              <div
                key={it.id}
                onClick={() => {
                  onClose();
                  onSendToTable(it.name);
                }}
                className="flex cursor-pointer items-center justify-between px-3 py-1.5 text-[13px] text-[var(--ink)] hover:bg-[var(--raised)]"
              >
                <span className="max-w-[300px] truncate">{it.name}</span>
                <span className="tabular-nums text-[12px] text-[var(--muted)]">
                  ICP {it.icp_score}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}
