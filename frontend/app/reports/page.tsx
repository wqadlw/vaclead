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
interface RoiRow { source: string; leads: number; won: number; won_amount_wan: number; win_rate: number | null }

const SOURCE_CN_ROI: Record<string, string> = {
  ggzy: "官方中标数据", ccgp: "政采网公告", ccgp_buyer: "政采买家",
  ggzy_buyer: "官方买家", csv: "CSV 导入", poi: "POI 采集",
  tender: "招标导入", manual: "手工", webform: "留资表单",
};

/** SPEC-X X2：来源转化对比（first-touch 快照） */
function SourceRoiTable() {
  const [items, setItems] = useState<RoiRow[]>([]);
  useEffect(() => {
    api.get<{ source_roi: RoiRow[] }>("/api/analytics/conversion")
      .then((d) => setItems(d.source_roi ?? []))
      .catch(() => undefined);
  }, []);
  return (
    <Table
      size="small"
      dataSource={items}
      rowKey="source"
      pagination={false}
      columns={[
        { title: "来源", dataIndex: "source", render: (v: string) => SOURCE_CN_ROI[v] ?? v },
        { title: "线索数", dataIndex: "leads", width: 90, render: (v: number) => <span className="tabular-nums">{v}</span> },
        { title: "赢单数", dataIndex: "won", width: 90, render: (v: number) => <span className="tabular-nums">{v}</span> },
        { title: "赢单金额(万)", dataIndex: "won_amount_wan", width: 110, render: (v: number) => <span className="tabular-nums">{v.toLocaleString()}</span> },
        { title: "赢单率", dataIndex: "win_rate", width: 90, render: (v: number | null) => (v != null ? <span className="tabular-nums">{Math.round(v * 100)}%</span> : "—") },
      ]}
      empty="暂无线索数据"
    />
  );
}
interface WeeklyReport {
  period: { start: string; end: string; label: string };
  kpis: Kpi[];
  funnel: FunnelRow[];
  top_signals: TopSignal[];
  top_customers: TopCustomer[];
  highlights: string[];
}
interface MyWeekReport {
  period: { start: string; end: string; label: string };
  owner: string;
  kpis: { key: string; label: string; value: number; prev: number; delta: number | null }[];
  stale_clients: number;
  highlights: string[];
}

const TYPE_CN: Record<string, string> = { tender_win: "中标", tender_buy: "公开采购", tender: "政采公告" };

/** SPEC-AC：我的周报（owner 维度） */
function MyWeekSection() {
  const [data, setData] = useState<MyWeekReport | null>(null);
  useEffect(() => {
    api.get<MyWeekReport>("/api/reports/my-week?offset=0")
      .then(setData)
      .catch(() => undefined);
  }, []);
  if (!data) return null;
  return (
    <section className="mt-5 rounded-lg border border-[var(--border)] bg-white p-4">
      <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">
        我的周报（{data.owner}）
      </h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {data.kpis.map((k) => (
          <div key={k.key} className="rounded-lg border border-[var(--rule)] px-3 py-2.5">
            <div className="text-[12px] text-[var(--muted)]">{k.label}</div>
            <div className="mt-1 text-[20px] font-semibold tabular-nums leading-none text-[var(--ink)]">
              {k.value.toLocaleString()}
            </div>
            {k.delta != null && (
              <div className={`mt-1 text-[11.5px] ${k.delta > 0 ? "text-green-600" : k.delta < 0 ? "text-red-500" : "text-[var(--muted)]"}`}>
                {k.delta > 0 ? "↑" : k.delta < 0 ? "↓" : "—"} {Math.abs(Math.round(k.delta * 100))}%
              </div>
            )}
          </div>
        ))}
        <div className="rounded-lg border border-[var(--rule)] px-3 py-2.5">
          <div className="text-[12px] text-[var(--muted)]">停滞客户(7d)</div>
          <div className={`mt-1 text-[20px] font-semibold tabular-nums leading-none ${data.stale_clients > 0 ? "text-[#c2801b]" : "text-[var(--ink)]"}`}>
            {data.stale_clients}
          </div>
        </div>
      </div>
    </section>
  );
}

export default function ReportsPage() {
  const [offset, setOffset] = useState(0);
  const [report, setReport] = useState<WeeklyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [myWeek, setMyWeek] = useState<MyWeekReport | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api.get<WeeklyReport>(`/api/reports/weekly?offset=${offset}`)
      .then(setReport)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [offset]);
  useEffect(load, [load]);

  useEffect(() => {
    api.get<MyWeekReport>("/api/reports/my-week?offset=0")
      .then(setMyWeek)
      .catch(() => undefined);
  }, []);

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

      {/* SPEC-AC AC3：我的周报（owner 维度） */}
      {myWeek && <MyWeekSection />}

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
          <section className="rounded-lg border border-[var(--border)] bg-white p-4">
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
            <section className="rounded-lg border border-[var(--border)] bg-white p-4">
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
            {/* SPEC-X X2：来源转化对比（first-touch 快照） */}
            <section className="rounded-lg border border-[var(--border)] bg-white p-4 xl:col-span-2">
              <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">来源转化对比</h2>
              <SourceRoiTable />
            </section>
            <section className="rounded-lg border border-[var(--border)] bg-white p-4">
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
