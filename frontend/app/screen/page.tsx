"use client";

/**
 * 数据大屏 /screen（SPEC-SALE S3）：老板驾驶舱，TV 挂载 1920×1080 优先。
 * 全部复用现有端点（零后端改动）：
 * - /api/dashboard/summary        KPI + 30 天趋势 + 省份分布
 * - /api/contacts?limit=1         可拨覆盖（stats.with_phone / total，全局口径）
 * - /api/recommendations?limit=5  今日推荐 Top5（8s 轮播）
 * - /api/competitors/feed         竞对 30 天中标动态
 * - /api/dashboard/radar-strip    晨检三格（SPEC-CC）
 * 60s 自动刷新；数据失败静默降级显示「—」不白屏；?token= 认证回退在 AppShell 统一处理。
 * 设计铁律：framed-sheet 浅色、白底卡片+border、无阴影无渐变、var(--accent)、
 * lucide 图标禁 emoji、大字号版式（KPI 48px / 页面标题 24px）、数字 tabular-nums。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Building2,
  Crosshair,
  MapPin,
  Megaphone,
  PhoneCall,
  Radar,
  Sprout,
  Target,
  Trophy,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import BrandMark from "@/components/BrandMark";
import { api } from "@/lib/api";

/* ---------------- 数据 shape（容错声明：缺键一律降级为 null/[]） ---------------- */

interface SummaryData {
  kpis?: Record<string, { total?: number; delta?: number } | undefined>;
  trend?: { date: string; count: number }[];
  geo?: { province: string; count: number }[];
  funnel?: { status: string; label: string; value: number }[];
}

interface RecoItem {
  ent_id: number;
  name: string;
  city: string | null;
  industry_name: string | null;
  score: number;
  reasons: string[];
  phone?: string | null;
  call_state?: string;
}

interface FeedItem {
  id: number;
  enterprise_name: string;
  type: string;
  title: string;
  pub_date: string;
  amount_wan: number | null;
  stolen_from: string;
}

interface RadarStrip {
  competitor_wins_30d?: number;
  proposals_window_now?: number;
  contact_untouched?: number;
}

/* ---------------- 工具 ---------------- */

const CAROUSEL_SECS = 8; // 今日推荐轮播间隔
const REFRESH_MS = 60_000; // 全量数据刷新周期

/** 数字展示：null/undefined → 「—」（静默降级），其余千分位 */
const fmtNum = (v: number | null | undefined): string =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("zh-CN") : "—";

const pad2 = (n: number) => String(n).padStart(2, "0");

/** 在管线索 = 漏斗 new + following + intent（未含公海/放弃） */
function managedLeads(sum: SummaryData | null): number | null {
  const funnel = sum?.funnel;
  if (!Array.isArray(funnel)) return null;
  const pick = (status: string) =>
    funnel.find((f) => f.status === status)?.value ?? 0;
  return pick("new") + pick("following") + pick("intent");
}

/** 近 7 天新信号 = 30 天趋势序列最后 7 天求和 */
function signals7d(sum: SummaryData | null): number | null {
  const trend = sum?.trend;
  if (!Array.isArray(trend) || trend.length === 0) return null;
  return trend.slice(-7).reduce((a, b) => a + (b.count ?? 0), 0);
}

export default function ScreenPage() {
  const [sum, setSum] = useState<SummaryData | null>(null);
  const [cover, setCover] = useState<{ withPhone: number | null; total: number | null } | null>(null);
  const [recos, setRecos] = useState<RecoItem[]>([]);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [strip, setStrip] = useState<RadarStrip | null>(null);
  // 时钟：SSG 预渲染期先出占位，挂载后每秒走（避免 hydration mismatch）
  const [now, setNow] = useState<Date | null>(null);
  // 轮播游标
  const [idx, setIdx] = useState(0);

  // TV 沉浸：强制浅色（大屏页面不做暗色适配，挂机场景恒定亮色）
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "light");
    document.body.setAttribute("theme-mode", "light");
    document.documentElement.setAttribute("theme-mode", "light");
  }, []);

  // 实时时钟（HH:mm:ss 每秒走）
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // 全量数据拉取：每个端点独立 catch——单点失败只影响自己那一格（显示「—」）
  const loadAll = useCallback(() => {
    api
      .get<SummaryData>("/api/dashboard/summary")
      .then(setSum)
      .catch(() => setSum(null));
    api
      .get<{ stats?: { with_phone?: number }; total?: number }>("/api/contacts?limit=1&has_phone=0")
      .then((d) =>
        setCover({
          withPhone: typeof d?.stats?.with_phone === "number" ? d.stats.with_phone : null,
          total: typeof d?.total === "number" ? d.total : null,
        }),
      )
      .catch(() => setCover(null));
    api
      .get<{ items?: RecoItem[] }>("/api/recommendations?limit=5")
      .then((d) => setRecos(Array.isArray(d?.items) ? d.items : []))
      .catch(() => setRecos([]));
    api
      .get<{ items?: FeedItem[] }>("/api/competitors/feed?days=30&type=tender_win&limit=8")
      .then((d) => setFeed(Array.isArray(d?.items) ? d.items : []))
      .catch(() => setFeed([]));
    api
      .get<RadarStrip>("/api/dashboard/radar-strip")
      .then(setStrip)
      .catch(() => setStrip(null));
  }, []);

  useEffect(() => {
    loadAll();
    const t = setInterval(loadAll, REFRESH_MS);
    return () => clearInterval(t);
  }, [loadAll]);

  // 轮播：8s 自动切换（数据更新后游标越界回 0）
  useEffect(() => {
    if (idx >= recos.length) setIdx(0);
  }, [recos.length, idx]);
  useEffect(() => {
    if (recos.length <= 1) return;
    const t = setInterval(() => setIdx((i) => (i + 1) % recos.length), CAROUSEL_SECS * 1000);
    return () => clearInterval(t);
  }, [recos.length]);

  const kpiEnterprise = typeof sum?.kpis?.enterprises?.total === "number" ? sum.kpis.enterprises.total : null;
  const coverPct =
    cover?.withPhone != null && cover?.total != null && cover.total > 0
      ? Math.round((cover.withPhone / cover.total) * 1000) / 10
      : null;
  const managed = managedLeads(sum);
  const sig7 = signals7d(sum);
  const provinces = (sum?.geo ?? []).slice(0, 8);
  const cur = recos[idx] ?? null;

  const dateStr = now
    ? `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`
    : "----/--/--";
  const weekCn = now ? ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][now.getDay()] : "";
  const clockStr = now ? `${pad2(now.getHours())}:${pad2(now.getMinutes())}:${pad2(now.getSeconds())}` : "--:--:--";

  const feedScroll = feed.length > 4; // 超过一屏才开无缝上滚

  return (
    <AppShell>
      <div className="flex min-h-screen flex-col gap-3 bg-[var(--ground)] p-4 xl:h-screen xl:overflow-hidden">
        {/* 顶部：LOGO + 标题 + 实时时钟 */}
        <header className="flex shrink-0 items-center justify-between border-b border-[var(--border)] pb-3">
          <div className="flex items-center gap-3">
            <BrandMark size={36} className="text-[var(--ink)]" />
            <div>
              <h1 className="text-[24px] font-semibold leading-7 text-[var(--ink)]">昊志销售驾驶舱</h1>
              <div className="text-[12px] leading-4 text-[var(--muted)]">
                HAOZHI SALES COCKPIT · 数据每 60 秒自动刷新
              </div>
            </div>
          </div>
          <div className="text-right">
            <div className="text-[26px] font-medium leading-8 tabular-nums text-[var(--ink)]">{clockStr}</div>
            <div className="text-[13px] leading-4 tabular-nums text-[var(--muted)]">
              {dateStr} {weekCn}
            </div>
          </div>
        </header>

        {/* 三列 grid：左 KPI+省分布 / 中 今日推荐轮播 / 右 竞对动态+晨检 */}
        <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[430px_minmax(0,1fr)_430px]">
          {/* ===== 左列：KPI 大字卡 + 省份分布 ===== */}
          <section className="flex min-h-0 flex-col gap-3">
            <div className="grid shrink-0 grid-cols-2 gap-3">
              {[
                { k: "企业池", v: fmtNum(kpiEnterprise), sub: "在库企业总数", icon: Building2 },
                { k: "可拨覆盖", v: coverPct == null ? "—" : `${coverPct}%`, sub: "有号码联系人占比", icon: PhoneCall },
                { k: "近 7 天新信号", v: fmtNum(sig7), sub: "中标/采购/备案", icon: Radar },
                { k: "在管线索", v: fmtNum(managed), sub: "新线索+跟进+意向", icon: Target },
              ].map((c) => (
                <div key={c.k} className="rounded-xl border border-[var(--border)] bg-white px-4 py-3.5">
                  <div className="flex items-center gap-1.5 text-[12.5px] text-[var(--muted)]">
                    <c.icon size={14} strokeWidth={1.8} />
                    {c.k}
                  </div>
                  <div className="mt-1 text-[48px] font-medium leading-[52px] tabular-nums text-[var(--accent)]">
                    {c.v}
                  </div>
                  <div className="mt-1 text-[11.5px] text-[var(--muted)]">{c.sub}</div>
                </div>
              ))}
            </div>
            <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-[var(--border)] bg-white px-4 py-3.5">
              <div className="flex items-center gap-1.5 text-[14px] font-medium text-[var(--ink)]">
                <MapPin size={15} strokeWidth={1.8} /> 省份分布
                <span className="ml-auto text-[11.5px] font-normal text-[var(--muted)]">客户数 Top 8</span>
              </div>
              <div className="mt-3 min-h-0 flex-1 space-y-2.5 overflow-hidden">
                {provinces.length === 0 && (
                  <div className="flex h-full items-center justify-center text-[13px] text-[var(--muted)]">—</div>
                )}
                {provinces.map((p, i) => {
                  const max = provinces[0]?.count || 1;
                  return (
                    <div key={p.province} className="flex items-center gap-3">
                      <span className="w-9 shrink-0 text-[13px] tabular-nums text-[var(--muted)]">
                        {pad2(i + 1)}
                      </span>
                      <span className="w-20 shrink-0 truncate text-[15px] text-[var(--ink)]" title={p.province}>
                        {p.province}
                      </span>
                      <div className="h-5 min-w-0 flex-1 rounded-sm bg-[var(--raised)]">
                        <div
                          className="h-full rounded-sm bg-[var(--accent)]"
                          style={{
                            width: `${Math.max((p.count / max) * 100, 2)}%`,
                            opacity: 0.4 + (0.6 * p.count) / max,
                          }}
                        />
                      </div>
                      <span className="w-14 shrink-0 text-right text-[15px] tabular-nums text-[var(--ink)]">
                        {fmtNum(p.count)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </section>

          {/* ===== 中列：今日推荐·先打给谁（Top5 轮播，单卡满高） ===== */}
          <section className="flex min-h-0 flex-col rounded-xl border border-[var(--border)] bg-white px-6 py-5 max-xl:min-h-[420px] max-xl:order-first">
            <div className="flex shrink-0 items-center justify-between">
              <div className="flex items-center gap-2 text-[18px] font-semibold text-[var(--ink)]">
                <Megaphone size={19} strokeWidth={1.8} className="text-[var(--accent)]" />
                今日推荐 · 先打给谁
              </div>
              <div className="text-[13px] tabular-nums text-[var(--muted)]">
                {recos.length > 0 ? `${idx + 1} / ${recos.length}` : "—"}
              </div>
            </div>

            {cur ? (
              <div className="flex min-h-0 flex-1 flex-col justify-center py-4" key={cur.ent_id}>
                <div className="flex items-center gap-3">
                  <span className="flex h-11 w-11 items-center justify-center rounded-lg bg-[var(--accent)] text-[20px] font-semibold text-white">
                    {idx + 1}
                  </span>
                  <span className="rounded-full border border-[var(--border)] px-3 py-1 text-[13px] tabular-nums text-[var(--accent)]">
                    推荐分 {cur.score}
                  </span>
                </div>
                <div className="mt-4 break-all text-[34px] font-semibold leading-[42px] text-[var(--ink)]">
                  {cur.name}
                </div>
                <div className="mt-1.5 text-[15px] text-[var(--muted)]">
                  {[cur.industry_name, cur.city].filter(Boolean).join(" · ") || "—"}
                </div>
                <div className="mt-5 space-y-2">
                  {(cur.reasons ?? []).slice(0, 3).map((r, i) => (
                    <div key={i} className="flex items-start gap-2 text-[16px] leading-6 text-[var(--ink)]">
                      <span className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)]" />
                      {r}
                    </div>
                  ))}
                  {(cur.reasons ?? []).length === 0 && (
                    <div className="text-[15px] text-[var(--muted)]">—</div>
                  )}
                </div>
                <div className="mt-6 flex items-center gap-3 border-t border-[var(--rule)] pt-4">
                  <PhoneCall size={18} strokeWidth={1.8} className="text-[var(--accent)]" />
                  <span className="font-mono text-[24px] font-medium tabular-nums tracking-wide text-[var(--accent)]">
                    {cur.phone || "—"}
                  </span>
                </div>
              </div>
            ) : (
              <div className="flex flex-1 items-center justify-center text-[14px] text-[var(--muted)]">
                今日暂无推荐 —— 等待采集任务产生新信号
              </div>
            )}

            {/* 底部：轮播进度条（8s 线性走完，key 换卡重启）+ 圆点指示 */}
            <div className="shrink-0">
              <div className="h-1 overflow-hidden rounded-full bg-[var(--raised)]">
                {recos.length > 1 && (
                  <div key={idx} className="screen-progress h-full bg-[var(--accent)]" />
                )}
              </div>
              {recos.length > 1 && (
                <div className="mt-2 flex items-center justify-center gap-1.5">
                  {recos.map((_, i) => (
                    <button
                      key={i}
                      aria-label={`切换到第 ${i + 1} 个推荐`}
                      onClick={() => setIdx(i)}
                      className={`h-1.5 cursor-pointer rounded-full border-0 p-0 transition-all ${
                        i === idx ? "w-5 bg-[var(--accent)]" : "w-1.5 bg-[var(--control)]"
                      }`}
                    />
                  ))}
                </div>
              )}
            </div>
          </section>

          {/* ===== 右列：竞对 30 天中标动态 + 晨检三格 ===== */}
          <section className="flex min-h-0 flex-col gap-3">
            <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-[var(--border)] bg-white px-4 py-3.5">
              <div className="flex shrink-0 items-center gap-1.5 text-[14px] font-medium text-[var(--ink)]">
                <Crosshair size={15} strokeWidth={1.8} className="text-[var(--negative)]" />
                竞对中标动态
                <span className="ml-auto text-[11.5px] font-normal text-[var(--muted)]">近 30 天 · 最近 8 条</span>
              </div>
              <div className="screen-no-scrollbar mt-2 min-h-0 flex-1 overflow-hidden">
                {feed.length === 0 ? (
                  <div className="flex h-full items-center justify-center text-[13px] text-[var(--muted)]">—</div>
                ) : (
                  <div className={feedScroll ? "screen-feed-scroll" : ""}>
                    {(feedScroll ? [...feed, ...feed] : feed).map((f, i) => (
                      <div
                        key={`${f.id}-${i}`}
                        className="border-b border-[var(--rule)] py-2 last:border-b-0"
                      >
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="truncate text-[14.5px] font-medium text-[var(--ink)]" title={f.enterprise_name}>
                            {f.enterprise_name || "—"}
                          </span>
                          {typeof f.amount_wan === "number" && f.amount_wan > 0 && (
                            <span className="shrink-0 text-[13.5px] tabular-nums text-[var(--accent)]">
                              {Math.round(f.amount_wan).toLocaleString("zh-CN")} 万
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 truncate text-[12.5px] text-[var(--muted)]" title={f.title}>
                          {f.title}
                        </div>
                        <div className="mt-0.5 flex items-center gap-2 text-[11px] tabular-nums text-[var(--muted)]">
                          <span>{(f.pub_date || "").slice(0, 10) || "日期未知"}</span>
                          {f.stolen_from && (
                            <span className="text-[var(--negative)]">抢自 {f.stolen_from}</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* 晨检三格（SPEC-CC：招标窗口 / 未触达 / 竞对中标） */}
            <div className="shrink-0 rounded-xl border border-[var(--border)] bg-white px-4 py-3.5">
              <div className="text-[14px] font-medium text-[var(--ink)]">晨检</div>
              <div className="mt-2.5 grid grid-cols-3 gap-px overflow-hidden rounded-lg bg-[var(--rule)]">
                {[
                  { k: "招标窗口", v: strip?.proposals_window_now, sub: "30-90 天备案", icon: Sprout },
                  { k: "未触达", v: strip?.contact_untouched, sub: "可拨未拨打", icon: PhoneCall },
                  { k: "竞对中标", v: strip?.competitor_wins_30d, sub: "近 30 天笔数", icon: Trophy },
                ].map((c) => (
                  <div key={c.k} className="bg-white px-3 py-3">
                    <div className="flex items-center gap-1.5 text-[12px] text-[var(--muted)]">
                      <c.icon size={13} strokeWidth={1.8} />
                      {c.k}
                    </div>
                    <div className="mt-1 text-[30px] font-medium leading-9 tabular-nums text-[var(--ink)]">
                      {fmtNum(c.v)}
                    </div>
                    <div className="text-[11px] text-[var(--muted)]">{c.sub}</div>
                  </div>
                ))}
              </div>
            </div>
          </section>
        </main>
      </div>
    </AppShell>
  );
}
