"use client";

/**
 * 报表中心（SPEC-O O1）：经营周报（页面/CSV/邮件三处共用同一 payload）。
 * 周切换 + KPI 环比卡 + 漏斗 + Top 标讯/Top 新客户两表。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Skeleton, Table, Tag } from "@douyinfe/semi-ui";
import { ChevronLeft, ChevronRight, Download } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface Kpi { key: string; label: string; value: number; prev: number; delta: number | null }
interface FunnelRow { status: string; label: string; value: number }
interface TopSignal { title: string; ent_name: string; type: string; amount_wan: number | null; pub_date: string | null; base_score: number }
interface TopCustomer { ent_name: string; industry: string | null; grade: string | null; composite: number }
interface WeeklyReport {
  period: { start: string; end: string; label: string };
  kpis: Kpi[];
  funnel: FunnelRow[];
  top_signals: TopSignal[];
  top_customers: TopCustomer[];
  highlights: string[];
}

const TYPE_CN: Record<string, string> = { tender_win: "中标", tender_buy: "公开采购", tender: "政采公告" };

export default function ReportsPage() {
  const [offset, setOffset] = useState(0);
  const [report, setReport] = useState<WeeklyReport | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    api.get<WeeklyReport>(`/api/reports/weekly?offset=${offset}`)
      .then(setReport)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [offset]);
  useEffect(load, [load]);

  const deltaTag = (d: number | null) => {
    if (d === null) return <span className="text-[12px] text-[var(--muted)]">—</span>;
    const pct = Math.round(d * 100);
    return (
      <Tag color={pct > 0 ? "green" : pct < 0 ? "red" : "grey"} type="light" size="small">
        {pct > 0 ? "↑" : pct < 0 ? "↓" : "—"} {Math.abs(pct)}%
      </Tag>
    );
  };
  const funnelMax = Math.max(1, ...(report?.funnel ?? []).map((f) => f.value));

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button
            icon={<ChevronLeft size={14} />}
            onClick={() => setOffset(offset + 1)}
            disabled={offset >= 12}
          />
          <span className="text-[15px] font-medium text-[var(--ink)]">
            {report?.period.label ?? "…"}
          </span>
          <Button icon={<ChevronRight size={14} />} onClick={() => setOffset(Math.max(0, offset - 1))} disabled={offset <= 0} />
        </div>
        <Button icon={<Download size={14} />} onClick={() => window.open(`/api/reports/weekly/export?offset=${offset}`, "_blank")}>
          导出 CSV
        </Button>
      </div>

      {loading || !report ? (
        <Skeleton placeholder={<Skeleton.Paragraph rows={8} />} loading active />
      ) : (
        <div className="space-y-5">
          {/* 亮点/风险摘要 */}
          <div className="rounded-lg border border-[var(--border)] bg-white px-5 py-3.5">
            {report.highlights.map((h, i) => (
              <div key={i} className="text-[13.5px] leading-6 text-[var(--ink)]">
                · {h}
              </div>
            ))}
          </div>

          {/* KPI 环比卡 */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {report.kpis.map((k) => (
              <div key={k.key} className="rounded-lg border border-[var(--border)] bg-white px-4 py-3">
                <div className="text-[12.5px] text-[var(--muted)]">{k.label}</div>
                <div className="mt-1 flex items-baseline gap-2">
                  <span className="text-[22px] font-semibold tabular-nums leading-none text-[var(--ink)]">
                    {k.value.toLocaleString()}
                  </span>
                  {deltaTag(k.delta)}
                </div>
                <div className="mt-1 text-[11.5px] text-[var(--muted)]">上周 {k.prev.toLocaleString()}</div>
              </div>
            ))}
          </div>

          {/* 漏斗 */}
          <section className="rounded-lg border border-[var(--border)] bg-white p-5">
            <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">线索漏斗（本周动静）</h2>
            <div className="space-y-2">
              {report.funnel.map((f) => (
                <div key={f.status} className="flex items-center gap-3">
                  <span className="w-16 shrink-0 text-right text-[12.5px] text-[var(--muted)]">{f.label}</span>
                  <div className="h-5 flex-1 rounded bg-[var(--border)]">
                    <div
                      className="flex h-5 items-center rounded bg-[var(--accent)] px-2 text-[11.5px] text-white"
                      style={{ width: `${Math.max(6, Math.round((f.value / funnelMax) * 100))}%` }}
                    >
                      {f.value > 0 && f.value}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Top 两表 */}
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <section className="rounded-lg border border-[var(--border)] bg-white p-5">
              <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">Top 标讯</h2>
              <Table
                size="small"
                dataSource={report.top_signals}
                rowKey={(r?: TopSignal) => `${r?.title ?? ""}-${r?.ent_name ?? ""}`}
                pagination={false}
                columns={[
                  { title: "标题", dataIndex: "title", ellipsis: true },
                  { title: "企业", dataIndex: "ent_name", width: 150, ellipsis: true },
                  { title: "类型", dataIndex: "type", width: 80, render: (v: string) => TYPE_CN[v] ?? v },
                  { title: "金额(万)", dataIndex: "amount_wan", width: 90, render: (v: number | null) => (v != null ? <span className="tabular-nums">{v.toLocaleString()}</span> : "—") },
                ]}
                empty="本周无标讯"
              />
            </section>
            <section className="rounded-lg border border-[var(--border)] bg-white p-5">
              <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">Top 新客户（综合分）</h2>
              <Table
                size="small"
                dataSource={report.top_customers}
                rowKey={(r?: TopCustomer) => r?.ent_name ?? ""}
                pagination={false}
                columns={[
                  { title: "企业", dataIndex: "ent_name", ellipsis: true },
                  { title: "行业", dataIndex: "industry", width: 130, ellipsis: true, render: (v: string | null) => v ?? "—" },
                  { title: "等级", dataIndex: "grade", width: 70, render: (v: string | null) => (v ? <Tag color="cyan" type="light" size="small">{v}</Tag> : "—") },
                  { title: "综合分", dataIndex: "composite", width: 80, render: (v: number) => <span className="tabular-nums">{v}</span> },
                ]}
                empty="本周无新线索"
              />
            </section>
          </div>
        </div>
      )}
    </AppShell>
  );
}
