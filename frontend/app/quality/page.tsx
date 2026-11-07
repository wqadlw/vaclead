"use client";

/**
 * 数据质量中心（SPEC-BW）：免费扩池阶梯的可视化账本——覆盖漏斗
 * （总数→坐标→行业→联系人→可拨）+ 来源分层 + 缺口清单。
 * "下一桶金在哪"的运营驾驶舱；admin 专属页。
 */
import { useEffect, useState } from "react";
import { Button } from "@douyinfe/semi-ui";
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
}

const SOURCE_CN: Record<string, string> = {
  poi: "高德 POI",
  ggzy: "公共资源交易",
  ccgp_buyer: "政采买家",
  ggzy_buyer: "公采买家",
  ccgp: "政采网",
  csv: "手工导入",
  proposal_gd: "广东备案",
  proposal_hb: "湖北备案",
  proposal_js: "江苏备案",
  proposal_zj: "浙江办理",
  tender: "历史导入",
};

export default function QualityPage() {
  const [data, setData] = useState<QualityData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get<QualityData>("/api/quality/overview")
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败（需管理员）"));
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
                  <span className="w-44 shrink-0 text-right text-[12.5px] text-[var(--muted)]">
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
                  <span className="w-40 shrink-0 text-[11.5px] text-[var(--muted)]">{s.note}</span>
                </div>
              ))}
            </div>
          );
        })()}
      </div>

      {/* 缺口卡 */}
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          { k: "省份文本缺失", v: g ? g.province_missing.toLocaleString() : "—", note: "POI 地理在坐标列；city→省 确定性回填是下一个动作", href: "/tasks" },
          { k: "行业未分类", v: g ? g.industry_missing.toLocaleString() : "—", note: "backfill-icp 幂等任务可补", href: "/tasks" },
          { k: "查重待复核", v: g ? g.dup_open.toLocaleString() : "—", note: "去「设置 → 数据复核」消化", href: "/settings" },
          { k: "回收站在押", v: g ? g.trash_count.toLocaleString() : "—", note: "30 天保留期后自动清空", href: "/enterprises" },
        ].map((c) => (
          <div key={c.k} className="rounded-lg border border-[var(--border)] bg-white px-4 py-3">
            <div className="text-[12px] text-[var(--muted)]">{c.k}</div>
            <div className="mt-0.5 text-[20px] font-medium tabular-nums text-[var(--ink)]">{c.v}</div>
            <div className="mt-0.5 flex items-center justify-between gap-2">
              <span className="min-w-0 flex-1 text-[11px] leading-3.5 text-[var(--muted)]">{c.note}</span>
              <Button size="small" theme="borderless" onClick={() => { window.location.href = c.href; }}>
                去处理
              </Button>
            </div>
          </div>
        ))}
      </div>

      {/* 信号覆盖 + 来源分层 */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[15px] font-medium text-[var(--ink)]">来源分层</h2>
          {g && (
            <span className="text-[12.5px] text-[var(--muted)]">
              信号覆盖：{g.signal_coverage.toLocaleString()} 家（{g.signal_coverage_pct}%）
            </span>
          )}
        </div>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[12.5px] font-normal text-[var(--muted)]">
                <th className="py-2 pr-4 font-normal">来源</th>
                <th className="py-2 pr-4 text-right font-normal">企业数</th>
                <th className="py-2 pr-4 text-right font-normal">有坐标</th>
                <th className="py-2 pr-4 text-right font-normal">有行业</th>
                <th className="py-2 pr-4 text-right font-normal">有联系人</th>
                <th className="py-2 text-right font-normal">可拨</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data?.by_source ?? {}).map(([src, st]) => (
                <tr key={src} className="border-t border-[var(--rule)] hover:bg-[var(--raised)]">
                  <td className="py-2 pr-4">{SOURCE_CN[src] ?? src}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{st.count.toLocaleString()}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{st.geo_pct}%</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{st.industry_pct}%</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{st.contact_pct}%</td>
                  <td className="py-2 text-right tabular-nums">
                    <span className={st.dialable_pct >= 30 ? "font-medium text-[var(--positive)]" : ""}>
                      {st.dialable_pct}%
                    </span>
                  </td>
                </tr>
              ))}
              {Object.keys(data?.by_source ?? {}).length === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-[13px] text-[var(--muted)]">
                    暂无数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </AppShell>
  );
}
