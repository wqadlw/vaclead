"use client";

/**
 * 数据质量中心（SPEC-BW）：免费扩池阶梯的可视化账本——覆盖漏斗
 * （总数→坐标→行业→联系人→可拨）+ 来源分层 + 缺口清单。
 * "下一桶金在哪"的运营驾驶舱；admin 专属页。
 */
import { useEffect, useState } from "react";
import { Button, Tag, Toast } from "@douyinfe/semi-ui";
import { Database } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface SourceStat {
  count: number;
  geo: number;
  industry: number;
  province: number;
  contact: number;
  dialable: number;
  contact_pct: number;
  dialable_pct: number;
  industry_pct: number;
  geo_pct: number;
}

interface QualityData {
  funnel: {
    total: number;
    has_geo: number;
    has_industry: number;
    has_contact: number;
    dialable: number;
  };
  gaps: {
    province_missing: number;
    industry_missing: number;
    industry_missing_by_source: Record<string, number>;
    signal_coverage: number;
    signal_coverage_pct: number;
    dup_open: number;
    trash_count: number;
  };
  by_source: Record<string, SourceStat>;
  /** SPEC-DQ：号码共享分析（疑似代理/中介识别） */
  phone_sharing?: {
    shared_numbers: number;
    shared_ent_links: number;
    top: { phone: string; ent_count: number; ent_ids: number[] }[];
  };
}

/** SPEC-DASH：数据新鲜度（OpenMetadata/Snowflake freshness SLA 惯例） */
interface FreshnessRow {
  job: string;
  label: string;
  sla_hours: number;
  last_run_at: string;
  status: string;
  state: "fresh" | "stale" | "overdue" | "unknown";
  hours_late: number | null;
  error: boolean;
  stats_head: string;
}

const FRESH_CN: Record<string, { label: string; color: string }> = {
  fresh: { label: "新鲜", color: "var(--positive)" },
  stale: { label: "超期", color: "#c2801b" },
  overdue: { label: "严重超期", color: "var(--negative)" },
  unknown: { label: "未知", color: "var(--muted)" },
};

export default function QualityPage() {
  const [data, setData] = useState<QualityData | null>(null);
  const [error, setError] = useState("");
  const [dupScanning, setDupScanning] = useState(false);
  const [fresh, setFresh] = useState<FreshnessRow[] | null>(null);

  useEffect(() => {
    api
      .get<QualityData>("/api/quality/overview")
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败（需管理员）"));
    api
      .get<{ items: FreshnessRow[] }>("/api/quality/freshness")
      .then((d) => setFresh(d.items))
      .catch(() => undefined);
  }, []);

  if (error) {
    return (
      <AppShell>
        <div className="py-16 text-center text-[13px] text-[var(--muted)]">{error}</div>
      </AppShell>
    );
  }

  const f = data?.funnel;
  const steps = f
    ? [
        { label: "活跃企业总数", value: f.total, note: "去重后的在池基数" },
        { label: "有坐标（可上地图）", value: f.has_geo, note: "POI/采集回填" },
        { label: "有行业（可评分推荐）", value: f.has_industry, note: "词表自动分类" },
        { label: "有联系人", value: f.has_contact, note: "公告/POI 回填" },
        { label: "可拨（号码库）", value: f.dialable, note: "打电话的金矿" },
      ]
    : [];
  const g = data?.gaps;

  return (
    <AppShell>
      <div className="flex items-center gap-2">
        <span className="flex items-center gap-1.5 text-[15px] font-medium text-[var(--ink)]">
          <Database size={16} /> 数据质量与覆盖
        </span>
        <span className="text-[12.5px] text-[var(--muted)]">全局口径 · 未删企业</span>
      </div>

      {/* 覆盖漏斗 */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4">
        <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">覆盖漏斗</h2>
        {(() => {
          const max = Math.max(1, ...(f ? [f.total] : [1]));
          return (
            <div className="space-y-2">
              {steps.map((s) => (
                <div key={s.label} className="flex items-center gap-3">
                  <span className="w-32 shrink-0 text-right text-[12.5px] text-[var(--muted)] sm:w-44">
                    {s.label}
                  </span>
                  <div className="h-5 flex-1 rounded bg-[var(--border)]">
                    <div
                      className="flex h-5 items-center rounded bg-[var(--accent)] px-2 text-[11.5px] text-white"
                      style={{ width: `${Math.max(4, Math.round((s.value / max) * 100))}%` }}
                    >
                      {s.value.toLocaleString()}
                    </div>
                  </div>
                  <span className="hidden shrink-0 text-[11.5px] text-[var(--muted)] sm:block sm:w-40">{s.note}</span>
                </div>
              ))}
            </div>
          );
        })()}
      </div>

      {/* 缺口卡 */}
      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { k: "省份文本缺失", v: g ? g.province_missing.toLocaleString() : "—", note: "多为无 city 来源——city→省 映射任务每日凌晨自动跑", href: "/tasks" },
          { k: "行业未分类", v: g ? g.industry_missing.toLocaleString() : "—", note: "词表校准周期跑；导入含行业列的数据可即时补", href: "/tasks" },
          { k: "查重待复核", v: g ? g.dup_open.toLocaleString() : "—", note: "去「设置 → 数据复核」消化", href: "/settings?tab=review", dupScan: true },
          { k: "回收站在押", v: g ? g.trash_count.toLocaleString() : "—", note: "30 天保留期后自动清空", href: "/enterprises?trash=1" },
        ].map((c) => (
          <div key={c.k} className="rounded-lg border border-[var(--border)] bg-white px-4 py-3">
            <div className="text-[12px] text-[var(--muted)]">{c.k}</div>
            <div className="mt-0.5 text-[20px] font-medium tabular-nums text-[var(--ink)]">{c.v}</div>
            <div className="mt-0.5 flex items-center justify-between gap-2">
              <span className="min-w-0 flex-1 text-[11px] leading-3.5 text-[var(--muted)]">{c.note}</span>
              {(() => {
                const zero = c.k === "回收站在押" && g && g.trash_count === 0;
                if (zero) return <span className="shrink-0 text-[11px] text-[var(--positive)]">已清空</span>;
                // SPEC-DQ：查重卡"立即扫描"直触发动作（POST dup-scan），其余跳精准处理位置
                if (c.dupScan) {
                  return (
                    <Button
                      size="small"
                      theme="borderless"
                      loading={dupScanning}
                      onClick={async () => {
                        setDupScanning(true);
                        try {
                          await api.post("/api/tasks/dup-scan", {});
                          Toast.success("查重扫描已启动——完成后去「设置 → 数据复核」消化");
                        } catch (e) {
                          Toast.error(e instanceof Error ? e.message : "启动失败");
                        } finally {
                          setDupScanning(false);
                        }
                      }}
                    >
                      立即扫描
                    </Button>
                  );
                }
                return (
                  <Button size="small" theme="borderless" onClick={() => { window.location.href = c.href; }}>
                    去处理
                  </Button>
                );
              })()}
            </div>
          </div>
        ))}
      </div>

      {/* 信号覆盖（来源矩阵的权威视图在信息中心 /data） */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-[15px] font-medium text-[var(--ink)]">信号覆盖</h2>
          <a href="/data" className="text-[12px] text-[var(--accent)] hover:underline">
            来源矩阵见「信息中心」→
          </a>
        </div>
        {g && (
          <div className="mt-2 text-[12.5px] text-[var(--muted)]">
            信号覆盖：{g.signal_coverage.toLocaleString()} 家（{g.signal_coverage_pct}%）
          </div>
        )}
      </div>

      {/* SPEC-DQ：号码共享分析——同一号码挂 ≥3 家企业 = 疑似招标代理/中介
          （销售打了发现是中介的根因；不删除，标注供人工判断） */}
      {data?.phone_sharing && data.phone_sharing.shared_numbers > 0 && (
        <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">号码共享分析（疑似代理/中介）</h2>
            <span className="text-[12.5px] text-[var(--muted)]">
              {data.phone_sharing.shared_numbers} 个号码被 ≥3 家企业共用（涉及{" "}
              {data.phone_sharing.shared_ent_links.toLocaleString()} 条企业关联）——拨打前留意
            </span>
          </div>
          <div className="mt-2.5 overflow-x-auto">
            <div className="hscroll-table-wrap">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-[12.5px] font-normal text-[var(--muted)]">
                    <th className="py-2 pr-4 font-normal">号码</th>
                    <th className="py-2 pr-4 text-right font-normal">挂靠企业数</th>
                    <th className="py-2 font-normal">企业（点进详情）</th>
                  </tr>
                </thead>
                <tbody>
                  {data.phone_sharing.top.map((s) => (
                    <tr key={s.phone} className="border-t border-[var(--rule)] hover:bg-[var(--raised)]">
                      <td className="py-2 pr-4 font-mono tabular-nums text-[var(--ink)]">{s.phone}</td>
                      <td className="py-2 pr-4 text-right tabular-nums font-medium text-[#c2801b]">{s.ent_count}</td>
                      <td className="py-2">
                        <span className="flex flex-wrap gap-1.5">
                          {s.ent_ids.map((id) => (
                            <a
                              key={id}
                              href={`/enterprises/view?id=${id}`}
                              className="text-[var(--accent)] hover:underline"
                            >
                              #{id}
                            </a>
                          ))}
                          {s.ent_count > s.ent_ids.length && (
                            <span className="text-[var(--muted)]">+{s.ent_count - s.ent_ids.length}</span>
                          )}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* SPEC-DASH：采集水位/数据新鲜度仪表（OpenMetadata/Snowflake 惯例）——
          每个采集/定时任务的 SLA 判定：新鲜/超期/严重超期，超期红标 */}
      {fresh && fresh.length > 0 && (
        <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">采集水位 · 数据新鲜度</h2>
            <span className="text-[12px] text-[var(--muted)]">
              每个数据源都有 SLA 预期——超期说明采集链路需要检查
            </span>
          </div>
          <div className="mt-2.5 overflow-x-auto">
            <div className="hscroll-table-wrap">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-[12.5px] font-normal text-[var(--muted)]">
                    <th className="py-2 pr-4 font-normal">数据源任务</th>
                    <th className="py-2 pr-4 font-normal">最后运行</th>
                    <th className="py-2 pr-4 font-normal">状态</th>
                    <th className="py-2 pr-4 font-normal">新鲜度</th>
                    <th className="py-2 font-normal">关键统计</th>
                  </tr>
                </thead>
                <tbody>
                  {fresh.map((f) => {
                    const fc = FRESH_CN[f.state] ?? FRESH_CN.unknown;
                    return (
                      <tr key={f.job} className="border-t border-[var(--rule)] hover:bg-[var(--raised)]">
                        <td className="py-2 pr-4 font-medium text-[var(--ink)]">{f.label}</td>
                        <td className="py-2 pr-4 tabular-nums text-[var(--muted)]">
                          {(f.last_run_at || "—").replace("T", " ")}
                        </td>
                        <td className="py-2 pr-4">
                          {f.error ? (
                            <Tag color="red" type="light" size="small">运行失败</Tag>
                          ) : (
                            <Tag color="green" type="light" size="small">正常</Tag>
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          <span className="inline-flex items-center gap-1.5">
                            <span
                              className="h-2 w-2 rounded-full"
                              style={{ background: fc.color }}
                              title={f.hours_late ? `超 SLA ${f.hours_late} 小时` : ""}
                            />
                            <span style={{ color: fc.color }}>{fc.label}</span>
                            {f.hours_late ? (
                              <span className="text-[11px] text-[var(--muted)]">(+{f.hours_late}h)</span>
                            ) : null}
                          </span>
                        </td>
                        <td className="py-2 text-[12px] text-[var(--muted)]">{f.stats_head || "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
