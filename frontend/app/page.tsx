"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Modal, Table, Tag, Toast } from "@douyinfe/semi-ui";
import { AlertTriangle, ArrowRight, ChevronLeft, ChevronRight, Phone, SlidersHorizontal } from "lucide-react";
import AppShell from "@/components/AppShell";
import { AreaTrend, BarList, DeltaBadge, FunnelBars, ProvinceBars, SparkBars } from "@/components/charts";
import { api } from "@/lib/api";
import { LEAD_STATUS } from "@/lib/constants";

interface DashboardData {
  total_enterprises: number;
  total_signals: number;
  signals_7d: number;
  my_leads_total?: number;
  my_leads_by_status: Record<string, number>;
  pending_followups: {
    lead_id: number;
    enterprise_name: string;
    next_action: string | null;
    next_time: string | null;
  }[];
  recent_signals: {
    signal_id: number;
    title: string;
    enterprise_id: number;
    enterprise_name: string;
    pub_date: string | null;
    base_score: number;
  }[];
}

interface TodoItem {
  id: number;
  title: string;
  enterprise_name: string | null;
  due_date: string | null;
  done: number;
  source: string;
}

interface SummaryData {
  kpis: Record<string, { total: number; delta: number }>;
  trend: { date: string; count: number }[];
  sources: { name: string; value: number }[];
  geo: { province: string; count: number }[];
  funnel: { status: string; label: string; value: number }[];
  health?: {
    active_leads: number;
    stale_7d: number;
    stale_14d: number;
    ratio: number;
  };
  forecast?: {
    months: { month: string; weighted: number }[];
    current_month_weighted: number;
    target: number | null;
    attain: number | null;
    contract_backlog?: number;
    receivable_overdue?: number;
    receivable_due30?: number;
  };
}

const SOURCE_CN: Record<string, string> = {
  ggzy: "官方中标数据",
  ccgp: "政采网公告",
  csv: "CSV 导入",
  poi: "POI 采集",
  tender: "招标导入",
  manual: "手工",
};

// SPEC-Z Z2 下钻：中文标签 → 跳转目标（图表点击即筛选，BI drill-down 标配）
const FUNNEL_TARGET: Record<string, string> = {
  新线索: "/leads?status=new",
  跟进中: "/leads?status=following",
  意向: "/leads?status=intent",
  已报价: "/quotes",
  成交: "/leads?status=won",
  公海: "/leads?status=pool",
  放弃: "/leads?status=lost",
};
const SOURCE_BY_CN: Record<string, string> = Object.fromEntries(
  Object.entries(SOURCE_CN).map(([k, v]) => [v, k]),
);

/* ---------------- SPEC-N N4 本月业绩榜（工作台卡） ---------------- */

interface LeaderboardItem {
  user_id?: number;
  id?: number;
  display_name?: string | null;
  name?: string | null;
  username?: string | null;
  won_amount_wan?: number | null;
  amount_wan?: number | null;
  won_amount?: number | null;
  new_leads?: number | null;
  leads_new?: number | null;
  new_lead_count?: number | null;
  followups?: number | null;
  followup_count?: number | null;
  follow_up_count?: number | null;
}

function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function shiftMonth(ym: string, delta: number): string {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(y, (m || 1) - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** 响应容错：兼容裸数组与 { items: [...] }，异常一律空态 */
function normalizeLeaderboard(d: unknown): LeaderboardItem[] {
  const rows = Array.isArray(d) ? d : (d as { items?: unknown } | null)?.items;
  return Array.isArray(rows) ? (rows as LeaderboardItem[]) : [];
}

function LeaderboardCard() {
  const [month, setMonth] = useState(currentMonth);
  const [rows, setRows] = useState<LeaderboardItem[] | null>(null);

  useEffect(() => {
    let stale = false;
    api
      .get<unknown>(`/api/analytics-leaderboard?month=${month}`)
      .then((d) => {
        if (!stale) setRows(normalizeLeaderboard(d));
      })
      .catch(() => {
        if (!stale) setRows([]);
      });
    return () => {
      stale = true;
    };
  }, [month]);

  const items = rows ?? [];
  const amountOf = (r: LeaderboardItem) =>
    Number(r.won_amount_wan ?? r.amount_wan ?? r.won_amount ?? 0) || 0;
  const nameOf = (r: LeaderboardItem) =>
    r.display_name || r.name || r.username || `用户#${r.user_id ?? r.id ?? "?"}`;
  const newLeadsOf = (r: LeaderboardItem) =>
    Number(r.new_leads ?? r.leads_new ?? r.new_lead_count ?? 0) || 0;
  const followupsOf = (r: LeaderboardItem) =>
    Number(r.followups ?? r.followup_count ?? r.follow_up_count ?? 0) || 0;
  const maxAmount = Math.max(...items.map(amountOf), 0);
  const thisMonth = currentMonth();
  const cardTitle =
    month === thisMonth
      ? "本月业绩榜"
      : `${Number(month.slice(0, 4))}年${Number(month.slice(5, 7))}月业绩榜`;

  return (
    <section className="rounded-lg border border-[var(--border)] bg-white p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="text-[15px] font-medium text-[var(--ink)]">{cardTitle}</h2>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => setMonth(shiftMonth(month, -1))}
            aria-label="上一月"
            className="flex h-8 w-8 cursor-pointer items-center justify-center rounded border-0 bg-transparent p-0 text-[var(--muted)] transition-colors hover:bg-[var(--raised)] hover:text-[var(--ink)]"
          >
            <ChevronLeft size={14} />
          </button>
          <span className="min-w-[52px] text-center text-[12px] tabular-nums text-[var(--muted)]">
            {month}
          </span>
          <button
            onClick={() => setMonth(shiftMonth(month, 1))}
            disabled={month >= thisMonth}
            aria-label="下一月"
            className="flex h-8 w-8 cursor-pointer items-center justify-center rounded border-0 bg-transparent p-0 text-[var(--muted)] transition-colors hover:bg-[var(--raised)] hover:text-[var(--ink)] disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-transparent"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
      {rows === null ? (
        <div className="py-5 text-center text-[12.5px] text-[var(--muted)]">加载中…</div>
      ) : items.length === 0 ? (
        <div className="py-5 text-center text-[12.5px] text-[var(--muted)]">本月暂无业绩数据</div>
      ) : (
        <div>
          {items.map((r, i) => {
            const amt = amountOf(r);
            const pct = maxAmount > 0 ? Math.max((amt / maxAmount) * 100, 2) : 2;
            const badgeCls =
              i === 0
                ? "bg-[var(--ink)] text-white"
                : i < 3
                  ? "bg-[var(--raised)] text-[var(--muted)]"
                  : "";
            return (
              <div
                key={r.user_id ?? r.id ?? `name-${nameOf(r)}`}
                className={`flex items-start gap-2.5 py-2.5 ${i > 0 ? "border-t border-[var(--rule)]" : ""}`}
              >
                <span
                  className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded text-[11px] font-semibold tabular-nums ${badgeCls} ${i >= 3 ? "text-[var(--muted)]" : ""}`}
                >
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span
                      className="min-w-0 truncate text-[13px] font-medium text-[var(--ink)]"
                      title={nameOf(r)}
                    >
                      {nameOf(r)}
                    </span>
                    <span className="shrink-0 text-[13px] tabular-nums text-[var(--ink)]">
                      {amt.toLocaleString("zh-CN")}
                      <span className="ml-0.5 text-[11px] font-normal text-[var(--muted)]">万</span>
                    </span>
                  </div>
                  {/* 金额条：自绘 SVG（BarList 风格，扁平零依赖，禁渐变） */}
                  <div className="mt-1.5 h-1.5 w-full rounded-sm bg-[var(--raised)]">
                    <svg
                      viewBox="0 0 100 6"
                      preserveAspectRatio="none"
                      className="h-1.5 w-full"
                      aria-hidden
                    >
                      <rect x="0" y="0" width={pct} height="6" fill="var(--accent)" />
                    </svg>
                  </div>
                  <div className="mt-1 flex items-center justify-between text-[11px] text-[var(--muted)]">
                    <span className="tabular-nums">新增线索 {newLeadsOf(r)}</span>
                    <span className="tabular-nums">跟进 {followupsOf(r)}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/* 分析页版式（吸收 ant-design-pro analysis + tremor）：KPI 环比条 → 30 天趋势 → 漏斗+来源 → 待跟进+最新信号 */
export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [sum, setSum] = useState<SummaryData | null>(null);
  // SPEC-CC 晨检行：三雷达一数首屏快照
  const [radarStrip, setRadarStrip] = useState<{ competitor_wins_30d: number; proposals_window_now: number; contact_untouched: number } | null>(null);
  const [health, setHealth] = useState<{ next_action?: { action?: string; hint?: string } } | null>(null);
  const [todos, setTodos] = useState<TodoItem[]>([]);
  // 今日作战清单（SPEC-G G5）：四源合并排序
  const [myDay, setMyDay] = useState<
    { priority: number; kind: string; id: number; title: string; hint: string }[]
  >([]);
  const [newTodo, setNewTodo] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<DashboardData>("/api/dashboard")
      .then(setData)
      .catch(() => undefined)
      .finally(() => setLoading(false));
    api.get<SummaryData>("/api/dashboard/summary").then(setSum).catch(() => undefined);
    api.get<{ competitor_wins_30d: number; proposals_window_now: number; contact_untouched: number }>(
      "/api/dashboard/radar-strip"
    ).then(setRadarStrip).catch(() => undefined);
    api.get<HealthData2>("/api/health").then(setHealth).catch(() => undefined);
    api.get<{ items: TodoItem[] }>("/api/todos").then((d) => setTodos(d.items)).catch(() => undefined);
    api
      .get<{ items: { priority: number; kind: string; id: number; title: string; hint: string }[] }>(
        "/api/dashboard/my-day",
      )
      .then((d) => setMyDay(d.items))
      .catch(() => undefined);
  }, []);

  const myLeads = data?.my_leads_by_status ?? {};
  const myTotal = Object.values(myLeads).reduce((a, b) => a + b, 0);
  const myLeadsCaption =
    Object.entries(myLeads)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${LEAD_STATUS[k]?.label ?? k} ${v}`)
      .join(" · ") || "尚未领取线索";

  const trendWindow = (sum?.trend ?? []).slice(-7).map((t) => t.count);
  const toggleTodo = async (id: number) => {
    // SPEC-UX 乐观更新：勾选先翻转 UI（划线即刻反馈），失败静默回滚
    setTodos((prev) => prev.map((t) => (t.id === id ? { ...t, done: t.done ? 0 : 1 } : t)));
    try {
      await api.patch(`/api/todos/${id}/done`, {});
    } catch {
      setTodos((prev) => prev.map((t) => (t.id === id ? { ...t, done: t.done ? 0 : 1 } : t)));
    }
  };

  const addTodo = async () => {
    const title = newTodo.trim();
    if (!title) return;
    try {
      await api.post("/api/todos", { title });
      setNewTodo("");
      const d = await api.get<{ items: TodoItem[] }>("/api/todos");
      setTodos(d.items);
    } catch {
      /* 静默 */
    }
  };

  const todayStr = new Date().toISOString().slice(0, 10);
  const openTodos = todos.filter((t) => !t.done);
  const dueToday = openTodos.filter((t) => t.due_date === todayStr);
  const overdue = openTodos.filter((t) => t.due_date && t.due_date < todayStr);
  const upcoming = openTodos.filter((t) => !t.due_date || t.due_date > todayStr);

  // AF3 工作台布局偏好：区块显隐（服务端存 settings_kv，跨设备一致）
  const DASH_SECTIONS: { key: string; label: string }[] = [
    { key: "kpi", label: "KPI 环比条" },
    { key: "recs", label: "今日推荐" },
    { key: "trend", label: "信号趋势" },
    { key: "analytics", label: "分析列（健康度/预测/漏斗/分布）" },
    { key: "battle", label: "今日行动" },
    { key: "signals", label: "最新信号" },
  ];
  const [dashHidden, setDashHidden] = useState<string[]>([]);
  const [prefOpen, setPrefOpen] = useState(false);
  useEffect(() => {
    api
      .get<{ hidden: string[] }>("/api/prefs/dashboard")
      .then((p) => setDashHidden(p.hidden ?? []))
      .catch(() => {});
  }, []);
  const toggleSection = (k: string) => {
    const next = dashHidden.includes(k) ? dashHidden.filter((x) => x !== k) : [...dashHidden, k];
    setDashHidden(next);
    api.put("/api/prefs/dashboard", { order: [], hidden: next }).catch(() => {});
  };
  const vis = (k: string) => !dashHidden.includes(k);

  // SPEC-AL AL3：今日推荐（可触达性×采购信号的可解释排序）
  interface RecItem {
    ent_id: number;
    name: string;
    city: string | null;
    industry_name: string | null;
    score: number;
    reasons: string[];
    phone?: string | null;
    managed?: boolean;
    call_state?: string;
    contact_id?: number | null;
  }
  const CALL_STATE_CN: Record<string, { label: string; color: string }> = {
    pending: { label: "未拨", color: "grey" },
    no_answer: { label: "无人接", color: "orange" },
    voicemail: { label: "语音信箱", color: "orange" },
    invalid: { label: "空号", color: "red" },
    connected: { label: "已接通", color: "green" },
    interested: { label: "有意向", color: "green" },
    not_interested: { label: "无需求", color: "grey" },
  };
  const [recs, setRecs] = useState<RecItem[]>([]);
  // SPEC-CH CH3：产品线过滤（空=全部）
  const [recProduct, setRecProduct] = useState("");
  const loadRecs = useCallback(() => {
    api
      .get<{ items: RecItem[] }>(`/api/recommendations?limit=8${recProduct ? `&product=${encodeURIComponent(recProduct)}` : ""}`)
      .then((d) => setRecs(d.items.map((x) => ({ ...x, managed: x.reasons.some((r) => r.includes("尚未分配")) === false }))))
      .catch(() => setRecs([]));
  }, [recProduct]);
  useEffect(() => {
    loadRecs();
  }, [loadRecs]);
  const convertRec = useCallback(async (entId: number) => {
    try {
      const r = await api.post<{ lead_id: number; created: boolean }>(
        `/api/recommendations/${entId}/convert`,
      );
      Toast.success(r.created ? "已转线索并建首联待办" : "已有跟进线索");
      loadRecs();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "转换失败");
    }
  }, [loadRecs]);
  // SPEC-AW AW3：推荐卡触达快标（标记主联系人结果）
  const markRec = useCallback(async (entId: number, result: string) => {
    try {
      await api.post(`/api/recommendations/${entId}/mark-call`, { result });
      loadRecs();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "标记失败");
    }
  }, [loadRecs]);

  const kpis = [
    {
      label: "企业库",
      value: sum?.kpis.enterprises.total ?? data?.total_enterprises ?? 0,
      delta: sum?.kpis.enterprises.delta ?? 0,
      spark: trendWindow,
      caption: "官方中标 + 政采 + 导入累积",
      href: "/enterprises",
    },
    {
      label: "意图信号",
      value: sum?.kpis.signals.total ?? data?.total_signals ?? 0,
      delta: sum?.kpis.signals.delta ?? 0,
      spark: trendWindow,
      caption: "公告 × 设备词表命中",
      href: "/signals",
    },
    {
      label: "线索",
      value: sum?.kpis.leads.total ?? myTotal,
      delta: sum?.kpis.leads.delta ?? 0,
      spark: [],
      caption: myLeadsCaption,
      href: "/leads",
    },
    {
      label: "可联系联系人",
      value: sum?.kpis.contacts.total ?? 0,
      delta: sum?.kpis.contacts.delta ?? 0,
      spark: [],
      caption: "政采公告法定公开联系方式",
      href: "/contacts",
    },
    {
      label: "流失预警",
      value: sum?.kpis.at_risk?.total ?? 0,
      delta: 0,
      spark: [],
      caption: "健康分<40 或已降级 · 每日 06:15 刷新",
      href: "/leads?status=at_risk",
    },
  ];

  return (
    <AppShell>
      {/* 标题行：<md 上下堆叠（长提示语独占一行），md+ 保持左右分布 */}
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold leading-7 text-[var(--ink)]">工作台</h1>
          <p className="mt-1 text-[13.5px] text-[var(--muted)]">
            {health?.next_action?.hint ?? "近 7 天新增采购信号 —— 优先跟进有新信号的高分客户"}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button theme="borderless" icon={<SlidersHorizontal size={14} />} onClick={() => setPrefOpen(true)}>
            布局
          </Button>
          <Button theme="solid" onClick={() => router.push("/enterprises")} className="!h-9">
            筛选客户
          </Button>
        </div>
      </div>

      {/* AF3 布局偏好 */}
      <Modal
        title="工作台布局"
        visible={prefOpen}
        onCancel={() => setPrefOpen(false)}
        footer={null}
        width={380}
      >
        <div className="space-y-1">
          {DASH_SECTIONS.map((s) => (
            <label
              key={s.key}
              className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-2 hover:bg-[var(--raised)]"
            >
              <input
                type="checkbox"
                checked={vis(s.key)}
                onChange={() => toggleSection(s.key)}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              <span className="text-[13.5px] text-[var(--ink)]">{s.label}</span>
              <span className="ml-auto text-[11.5px] text-[var(--muted)]">
                {vis(s.key) ? "显示中" : "已隐藏"}
              </span>
            </label>
          ))}
        </div>
        <p className="mt-3 text-[12px] leading-5 text-[var(--muted)]">
          偏好保存到账号，换设备登录同样生效。隐藏的区块随时可以在这里恢复。
        </p>
      </Modal>

      {/* 空库 Onboarding（SPEC-G G3：新环境三步引导直达首批数据） */}
      {(sum?.kpis.enterprises.total ?? data?.total_enterprises ?? 0) < 20 && (
        <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-3">
          {[
            {
              step: 1,
              title: "跑第一批官方数据",
              desc: "免 Key 免配置，直连国家公共资源交易平台——一键采集中标企业 + 采购人",
              cta: "去采集",
              href: "/tasks",
            },
            {
              step: 2,
              title: "看数据长出来",
              desc: "几十秒出结果：企业库/信号流/采购关系图谱自动生成，带公告溯源",
              cta: "看企业库",
              href: "/enterprises",
            },
            {
              step: 3,
              title: "让系统自动干活",
              desc: "建一条自动化规则（如：科研教育中标 → 自动建线索+待办），之后每天自动跑",
              cta: "去设置规则",
              href: "/settings",
            },
          ].concat(
            health?.next_action?.action === "configure_amap"
              ? [{
                  step: 4,
                  title: "解锁地图拓客",
                  desc: "配一个高德 Key，客户地图按城市打点、POI 采集自动跑——设置页两分钟",
                  cta: "去配置",
                  href: "/settings",
                }]
              : [],
          ).map((s) => (
            <a
              key={s.step}
              href={s.href}
              className="group rounded-xl border border-[var(--border)] bg-white p-4 transition-colors hover:border-[var(--accent)]"
            >
              <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--accent)] text-[13px] font-medium text-white">
                {s.step}
              </div>
              <div className="mt-3 text-[14.5px] font-medium text-[var(--ink)]">{s.title}</div>
              <p className="mt-1.5 text-[12.5px] leading-5 text-[var(--muted)]">{s.desc}</p>
              <span className="mt-3 inline-flex items-center gap-1 text-[13px] font-medium text-[var(--accent)]">
                {s.cta} <span className="transition-transform group-hover:translate-x-0.5">→</span>
              </span>
            </a>
          ))}
        </div>
      )}

      {/* SPEC-CC 晨检行：雷达产出收口 */}

      {radarStrip && (

        <div className="mb-4 grid grid-cols-1 gap-px overflow-hidden rounded-lg bg-[var(--border)] sm:grid-cols-3">

        {([

          { k: "竞对中标（30 天）", v: radarStrip.competitor_wins_30d, unit: "笔", sub: "谁在抢单", href: "/competitors", cls: "text-[var(--negative)]" },

          { k: "拟建正处招标窗口", v: radarStrip.proposals_window_now, unit: "个", sub: "备案后 1-3 个月", href: "/proposals", cls: "text-[#c2801b]" },

          { k: "未触达联系人", v: radarStrip.contact_untouched, unit: "人", sub: "可拨未拨", href: "/contacts", cls: "text-[var(--ink)]" },

        ] as { k: string; v: number; unit: string; sub: string; href: string; cls: string }[]).map((c) => (

          <a key={c.k} href={c.href} className="flex items-center justify-between bg-white px-4 py-3 transition-colors hover:bg-[var(--raised)]">

            <div>

              <div className="text-[11.5px] text-[var(--muted)]">{c.k}</div>

              <div className={"mt-0.5 text-[20px] font-medium tabular-nums " + c.cls}>

                {c.v} <span className="text-[12px] font-normal text-[var(--muted)]">{c.unit}</span>

              </div>

            </div>

            <span className="text-[11.5px] text-[var(--muted)]">{c.sub} →</span>

          </a>

        ))}

        </div>

      )}

      {/* KPI 环比条（tremor 模式：值 + Delta 徽标 + 迷你趋势） */}
      {vis("kpi") && (
      <div className="mt-5 grid grid-cols-2 overflow-hidden rounded-lg border border-[var(--border)] lg:grid-cols-5">
        {kpis.map((k, i) => (
          <div
            key={k.label}
            onClick={() => k.href && router.push(k.href)}
            title={k.href ? `查看${k.label}` : undefined}
            className={`flex items-center justify-between gap-2 bg-white px-4 py-4 sm:px-5 ${
              i > 0 ? "border-l border-[var(--rule)]" : ""
            } ${i >= 2 ? "max-lg:border-t max-lg:border-[var(--rule)]" : ""} ${
              i === 2 || i === 4 ? "max-lg:border-l-0" : ""
            } ${k.href ? "cursor-pointer transition-colors hover:bg-[var(--raised)]" : ""}`}
          >
            <div className="min-w-0">
              <div className="text-[13px] text-[var(--muted)]">{k.label}</div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="text-[22px] font-normal leading-8 tabular-nums text-[var(--ink)] sm:text-[28px] sm:leading-9">
                  {k.value.toLocaleString("zh-CN")}
                </span>
                <DeltaBadge delta={k.delta} />
              </div>
              <div className="mt-0.5 truncate text-[12px] text-[#8a8d8f]">{k.caption}</div>
            </div>
            {k.spark.length > 1 && (
              <span className="hidden shrink-0 sm:block">
                <SparkBars values={k.spark} />
              </span>
            )}
          </div>
        ))}
      </div>
      )}

      {/* SPEC-AL 今日推荐：可触达性×采购信号的可解释排序 */}
      {vis("recs") && (
      <section className="mt-5 rounded-lg border border-[var(--border)] bg-white p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5 md:items-baseline">
          <h2 className="shrink-0 text-[15px] font-medium text-[var(--ink)]">今日推荐 · 先打给谁</h2>
          <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden md:flex-none">
            {["", "EVS", "干式", "压缩机", "鼓风机"].map((p) => (
              <button
                key={p || "all"}
                onClick={() => setRecProduct(p)}
                className={`shrink-0 rounded-full border px-3 py-1.5 text-[12.5px] md:px-2 md:py-0.5 md:text-[11px] ${
                  recProduct === p
                    ? "border-[var(--accent)] bg-[var(--accent-weak,rgba(94,106,210,0.1))] text-[var(--accent)]"
                    : "border-[var(--border)] bg-white text-[var(--muted)] hover:border-[var(--accent)]"
                }`}
              >
                {p || "全部"}
              </button>
            ))}
          </div>
          <span className="shrink-0 text-[12px] text-[var(--muted)] max-md:order-last max-md:basis-full">
            可触达性×采购信号排序 · 随每日采集自动更新
          </span>
        </div>
        {recs.length === 0 ? (
          <div className="py-6 text-center text-[13px] text-[var(--muted)]">
            暂无推荐 —— 采集与联系方式回填跑几天后，这里会给出"先打给谁"
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {recs.map((r) => (
              <a
                key={r.ent_id}
                href={`/enterprises/view?id=${r.ent_id}`}
                className="flex flex-col gap-2 rounded-lg border border-[var(--border)] px-3 py-2 transition-colors hover:border-[var(--accent)] md:flex-row md:items-start md:justify-between md:gap-3"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-[13.5px] font-medium text-[var(--ink)]">{r.name}</span>
                    {r.phone && (
                      <span className="inline-flex shrink-0 items-center gap-0.5">
                        <span className="rounded bg-[color:var(--accent)]/10 px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-[var(--accent)]">
                          {r.phone}
                        </span>
                        {/* SPEC-BO：tel: 拨打（stopPropagation 防卡片跳转） */}
                        <a
                          href={`tel:${r.phone}`}
                          title="点击拨打"
                          className="inline-flex h-8 w-8 items-center justify-center rounded text-[var(--muted)] transition-colors hover:bg-[var(--raised)] hover:text-[var(--accent)]"
                          onClick={(ev) => ev.stopPropagation()}
                        >
                          <Phone size={14} />
                        </a>
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 truncate text-[12px] text-[var(--muted)]">
                    {r.city ?? "—"} · {r.industry_name ?? "未分类"} · {r.reasons[0] ?? ""}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 md:shrink-0">
                  <span className="text-[15px] font-semibold tabular-nums text-[var(--accent)]">
                    {r.score}
                  </span>
                  {/* SPEC-AW AW3：触达快标（未拨时三快标；已拨显示状态） */}
                  {(r.call_state ?? "pending") === "pending" && r.contact_id ? (
                    <div className="flex items-center gap-1">
                      <button
                        className="cursor-pointer rounded border border-[var(--border)] bg-white px-2.5 py-1.5 text-[12.5px] md:px-1.5 md:py-0.5 md:text-[11px] text-[var(--ink)] hover:border-[var(--accent)]"
                        onClick={async (ev) => {
                          ev.preventDefault();
                          await markRec(r.ent_id, "connected");
                        }}
                      >接通</button>
                      <button
                        className="cursor-pointer rounded border border-[var(--border)] bg-white px-2.5 py-1.5 text-[12.5px] md:px-1.5 md:py-0.5 md:text-[11px] text-[var(--muted)] hover:border-[var(--accent)]"
                        onClick={async (ev) => {
                          ev.preventDefault();
                          await markRec(r.ent_id, "no_answer");
                        }}
                      >未接</button>
                      <button
                        className="cursor-pointer rounded border border-[var(--border)] bg-white px-2.5 py-1.5 text-[12.5px] md:px-1.5 md:py-0.5 md:text-[11px] text-[var(--negative)] hover:border-[var(--negative)]"
                        onClick={async (ev) => {
                          ev.preventDefault();
                          await markRec(r.ent_id, "invalid");
                        }}
                      >空号</button>
                    </div>
                  ) : (
                    <Tag
                      color={(CALL_STATE_CN[r.call_state ?? "pending"] ?? { color: "grey" }).color as "green" | "orange" | "red" | "grey"}
                      type="light"
                      size="small"
                    >
                      {CALL_STATE_CN[r.call_state ?? "pending"]?.label ?? "未拨"}
                    </Tag>
                  )}
                  <Button size="small" theme="outline" onClick={(ev) => { ev.preventDefault(); void convertRec(r.ent_id); }}>
                    转线索
                  </Button>
                </div>
              </a>
            ))}
          </div>
        )}
      </section>
      )}


      {/* 今日行动（SPEC-CG CG1：my-day 优先级清单 + 待办勾选 + 快速添加——三清单合一） */}
          {vis("battle") && (<>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">今日行动</h2>
            <span className="text-[12px] text-[var(--muted)]">
              {myDay.length > 0 ? `${myDay.length} 项待处理` : "今天没有必须处理的事"}
            </span>
          </div>
          <div className="mb-2 flex gap-2">
            <input
              value={newTodo}
              onChange={(e) => setNewTodo(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void addTodo();
              }}
              placeholder="记一件事，如：周三前回访北大设备处…"
              className="h-8 flex-1 rounded-lg border border-[var(--border)] bg-white px-3 text-[13px] outline-none focus:border-[var(--accent)]"
            />
            <Button size="small" theme="outline" onClick={() => void addTodo()}>
              添加
            </Button>
          </div>
          {myDay.length > 0 ? (
            <div className="mb-3 space-y-1.5">
              {myDay.slice(0, 12).map((m) => (
                <div
                  key={`${m.kind}-${m.id}`}
                  className="flex items-center gap-2.5 rounded-lg border border-[var(--border)] bg-white px-3 py-2"
                >
                  {(m.kind === "overdue_todo" || m.kind === "due_today") && (
                    <input
                      type="checkbox"
                      checked={false}
                      onChange={() => void toggleTodo(m.id)}
                      className="h-4 w-4 shrink-0 cursor-pointer accent-[var(--accent)]"
                    />
                  )}
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 text-[10.5px] font-medium ${
                      m.priority === 0
                        ? "bg-[color:var(--negative)]/10 text-[var(--negative)]"
                        : m.priority === 1
                          ? "bg-[color:var(--accent)]/10 text-[var(--accent)]"
                          : "bg-[var(--raised)] text-[var(--muted)]"
                    }`}
                  >
                    {{ overdue_todo: "逾期", due_today: "今日", upcoming_todo: "后续", stale_intent: "久未跟", fresh_lead: "新线索", rule_hits: "自动化", due_soon_followup: "该回访" }[m.kind] ?? m.kind}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--ink)]" title={m.title}>
                    {m.title}
                  </span>
                  <span className="shrink-0 text-[11.5px] text-[var(--muted)]">{m.hint}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="mb-3 rounded-lg border border-dashed border-[var(--border)] px-4 py-6 text-center text-[12.5px] text-[var(--muted)]">
              今天没有必须处理的事 —— 打几个「今日推荐」里的电话，或在上面记一件事
            </div>
          )}
          </>)}

      {/* 主趋势 + 右列（漏斗/来源） */}
      <div className="mt-5 grid grid-cols-1 items-start gap-6 xl:grid-cols-[1fr_360px]">
        {vis("trend") && (
        <section className="rounded-lg border border-[var(--border)] bg-white p-4">
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">信号趋势 · 近 30 天</h2>
            <span className="text-[12px] text-[var(--muted)]">
              累计 {(sum?.trend ?? []).reduce((a, b) => a + b.count, 0)} 条
            </span>
          </div>
          <AreaTrend data={sum?.trend ?? []} />
        </section>
        )}

        <div className={`space-y-5 ${vis("trend") ? "" : "xl:col-start-2"}`}>
        {vis("analytics") && (<>
          {/* SPEC-X X3：跟进健康度 */}
          <section className="rounded-lg border border-[var(--border)] bg-white p-4">
            <div className="mb-3 flex items-baseline justify-between">
              <h2 className="text-[15px] font-medium text-[var(--ink)]">跟进健康度</h2>
              <span className="text-[12px] text-[var(--muted)]">活跃线索超期未跟进</span>
            </div>
            {sum?.health ? (
              <div>
                <div className="flex items-baseline gap-2">
                  <span
                    className={`text-[26px] font-semibold leading-none tabular-nums ${
                      sum.health.stale_14d > 0 ? "text-[#c0392b]" : sum.health.stale_7d > 0 ? "text-[#c2801b]" : "text-[var(--ink)]"
                    }`}
                  >
                    {sum.health.stale_7d}
                  </span>
                  <span className="text-[12px] text-[var(--muted)]">
                    条超 7 天（其中 {sum.health.stale_14d} 条超 14 天）
                  </span>
                </div>
                <div className="mt-3 h-1.5 w-full rounded-full bg-[var(--border)]">
                  <div
                    className={`h-1.5 rounded-full ${sum.health.stale_14d > 0 ? "bg-[#c0392b]" : "bg-[var(--accent)]"}`}
                    style={{
                      width: `${Math.min(100, Math.round(sum.health.ratio * 100))}%`,
                    }}
                  />
                </div>
                <div className="mt-2 text-[12px] text-[var(--muted)]">
                  活跃线索 {sum.health.active_leads} 条 · 超期占比{" "}
                  {Math.round(sum.health.ratio * 100)}%
                </div>
              </div>
            ) : (
              <div className="text-[13px] text-[var(--muted)]">—</div>
            )}
          </section>
          {/* SPEC-J J6：加权管道预测（espocrm Forecasts 思想） */}
          <section className="rounded-lg border border-[var(--border)] bg-white p-4">
            <div className="mb-3 flex items-baseline justify-between">
              <h2 className="text-[15px] font-medium text-[var(--ink)]">本月加权预测</h2>
              <span className="text-[12px] text-[var(--muted)]">Σ 金额×赢率</span>
            </div>
            {sum?.forecast ? (
              <div>
                <div className="flex items-baseline gap-2">
                  <span className="text-[26px] font-semibold leading-none text-[var(--ink)]">
                    {sum.forecast.current_month_weighted.toLocaleString()}
                  </span>
                  <span className="text-[12px] text-[var(--muted)]">万元</span>
                  {sum.forecast.target != null && (
                    <span className="ml-auto text-[12px] text-[var(--muted)]">
                      目标 {sum.forecast.target.toLocaleString()} 万
                      {sum.forecast.attain != null && (
                        <> · 达成 {Math.round(sum.forecast.attain * 100)}%</>
                      )}
                    </span>
                  )}
                </div>
                {sum.forecast.target != null && sum.forecast.target > 0 && (
                  <div className="mt-3 h-1.5 w-full rounded-full bg-[var(--border)]">
                    <div
                      className="h-1.5 rounded-full bg-[var(--accent)]"
                      style={{
                        width: `${Math.min(100, Math.round((sum.forecast.attain ?? 0) * 100))}%`,
                      }}
                    />
                  </div>
                )}
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-[var(--muted)]">
                  {(sum.forecast.months ?? []).slice(0, 4).map((m) => (
                    <span key={m.month}>
                      {m.month}：<span className="text-[var(--ink)]">{m.weighted.toLocaleString()}</span> 万
                    </span>
                  ))}
                </div>
                {/* AF4 在手合同余款：赢单后金额留在预测视野 */}
                {(sum.forecast.contract_backlog ?? 0) > 0 && (
                  <div className="mt-2 text-[12.5px] text-[var(--muted)]">
                    在手合同余款{" "}
                    <span className="font-medium text-[var(--ink)]">
                      {(sum.forecast.contract_backlog ?? 0).toLocaleString()} 万
                    </span>
                    （active 合同 − 已回款）
                  </div>
                )}
                {/* SPEC-AX AX2 应收视角：现金流风险一眼可见 */}
                {((sum.forecast.receivable_overdue ?? 0) > 0 ||
                  (sum.forecast.receivable_due30 ?? 0) > 0) && (
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-0.5 text-[12.5px]">
                    {(sum.forecast.receivable_overdue ?? 0) > 0 && (
                      <span className="inline-flex items-center gap-1 text-[var(--negative)]">
                        <AlertTriangle size={12} className="shrink-0" />
                        回款逾期{" "}
                        <span className="font-semibold tabular-nums">
                          {sum.forecast.receivable_overdue!.toLocaleString()} 万
                        </span>
                      </span>
                    )}
                    {(sum.forecast.receivable_due30 ?? 0) > 0 && (
                      <span className="text-[var(--muted)]">
                        30 天内到期{" "}
                        <span className="font-medium tabular-nums text-[var(--ink)]">
                          {sum.forecast.receivable_due30!.toLocaleString()} 万
                        </span>
                      </span>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="text-[13px] text-[var(--muted)]">
                给跟进中/意向商机填上金额、赢率与预计成交月，即可看到加权预测。
              </div>
            )}
          </section>
        </>)}
        </div>
      </div>

      {/* 分析卡阵（两列）+ 最新信号侧栏：PC 端左右平衡（旧版六卡全挤 360px 侧栏、
          左列仅一张趋势卡 → 左侧大片空白实锤 2026-11-10） */}
      <div className="mt-5 grid grid-cols-1 items-start gap-6 xl:grid-cols-[1fr_360px]">
        {vis("analytics") && (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {/* SPEC-N N4 本月业绩榜 */}
          <LeaderboardCard />
          <section className="rounded-lg border border-[var(--border)] bg-white p-4">
            <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">线索漏斗</h2>
            <FunnelBars
              items={sum?.funnel ?? []}
              onPick={(label) => {
                const target = FUNNEL_TARGET[label];
                if (target) router.push(target);
              }}
            />
          </section>
          <section className="rounded-lg border border-[var(--border)] bg-white p-4">
            <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">客户省份分布</h2>
            <ProvinceBars
              items={sum?.geo ?? []}
              onPick={(province) => router.push(`/enterprises?province=${encodeURIComponent(province)}`)}
            />
          </section>
          <section className="rounded-lg border border-[var(--border)] bg-white p-4">
            <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">企业来源分布</h2>
            <BarList
              items={(sum?.sources ?? []).map((s) => ({
                name: SOURCE_CN[s.name] ?? s.name,
                value: s.value,
              }))}
              onPick={(name) => {
                const key = SOURCE_BY_CN[name];
                if (key) router.push(`/enterprises?source=${key}`);
              }}
            />
          </section>
        </div>
        )}

        {vis("signals") && (
        <aside>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">最新信号</h2>
            <button
              onClick={() => router.push("/enterprises")}
              className="-my-2 flex cursor-pointer items-center gap-1 rounded border-0 bg-transparent px-2 py-3 text-[13px] text-[var(--accent)] hover:underline"
            >
              按信号筛选 <ArrowRight size={12} />
            </button>
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-white">
            {(data?.recent_signals ?? []).length === 0 ? (
              <div className="p-4 text-[13px] text-[var(--muted)]">
                暂无信号 —— 采集任务与导入的命中结果会在这里按时间倒序展示
              </div>
            ) : (
              data!.recent_signals.map((s, i) => (
                <div
                  key={s.signal_id}
                  className={`px-4 py-3 ${i > 0 ? "border-t border-[var(--rule)]" : ""}`}
                >
                  <div className="truncate text-[13px] font-medium text-[var(--ink)]" title={s.title}>
                    {s.title}
                  </div>
                  <div className="mt-0.5 flex items-center justify-between text-[12px] text-[var(--muted)]">
                    <span className="truncate">{s.enterprise_name}</span>
                    <span className="tabular-nums">{s.pub_date || "—"}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </aside>
        )}
      </div>
    </AppShell>
  );
}

type HealthData2 = { next_action?: { action?: string; hint?: string } };
