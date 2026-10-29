"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Table } from "@douyinfe/semi-ui";
import { ArrowRight } from "lucide-react";
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
  forecast?: {
    months: { month: string; weighted: number }[];
    current_month_weighted: number;
    target: number | null;
    attain: number | null;
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

/* 分析页版式（吸收 ant-design-pro analysis + tremor）：KPI 环比条 → 30 天趋势 → 漏斗+来源 → 待跟进+最新信号 */
export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [sum, setSum] = useState<SummaryData | null>(null);
  const [health, setHealth] = useState<{ next_action?: { hint?: string } } | null>(null);
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
    try {
      await api.patch(`/api/todos/${id}/done`, {});
      setTodos((prev) => prev.map((t) => (t.id === id ? { ...t, done: t.done ? 0 : 1 } : t)));
    } catch {
      /* 静默 */
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

  const kpis = [
    {
      label: "企业库",
      value: sum?.kpis.enterprises.total ?? data?.total_enterprises ?? 0,
      delta: sum?.kpis.enterprises.delta ?? 0,
      spark: trendWindow,
      caption: "官方中标 + 政采 + 导入累积",
    },
    {
      label: "意图信号",
      value: sum?.kpis.signals.total ?? data?.total_signals ?? 0,
      delta: sum?.kpis.signals.delta ?? 0,
      spark: trendWindow,
      caption: "公告 × 设备词表命中",
    },
    {
      label: "线索",
      value: sum?.kpis.leads.total ?? myTotal,
      delta: sum?.kpis.leads.delta ?? 0,
      spark: [],
      caption: myLeadsCaption,
    },
    {
      label: "可联系联系人",
      value: sum?.kpis.contacts.total ?? 0,
      delta: sum?.kpis.contacts.delta ?? 0,
      spark: [],
      caption: "政采公告法定公开联系方式",
    },
  ];

  return (
    <AppShell>
      {/* 标题行 */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-[22px] font-semibold leading-7 text-[var(--ink)]">工作台</h1>
          <p className="mt-1 text-[13.5px] text-[var(--muted)]">
            {health?.next_action?.hint ?? "近 7 天新增采购信号 —— 优先跟进有新信号的高分客户"}
          </p>
        </div>
        <Button theme="solid" onClick={() => router.push("/enterprises")} className="!h-9">
          筛选客户
        </Button>
      </div>

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
          ].map((s) => (
            <a
              key={s.step}
              href={s.href}
              className="group rounded-xl border border-[var(--border)] bg-white p-5 transition-colors hover:border-[var(--accent)]"
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

      {/* KPI 环比条（tremor 模式：值 + Delta 徽标 + 迷你趋势） */}
      <div className="mt-5 grid grid-cols-2 overflow-hidden rounded-lg border border-[var(--border)] lg:grid-cols-4">
        {kpis.map((k, i) => (
          <div
            key={k.label}
            className={`flex items-center justify-between bg-white px-5 py-4 ${
              i > 0 ? "border-l border-[var(--rule)]" : ""
            } ${i >= 2 ? "max-lg:border-t max-lg:border-[var(--rule)]" : ""} ${
              i === 2 ? "max-lg:border-l-0" : ""
            }`}
          >
            <div className="min-w-0">
              <div className="text-[13px] text-[var(--muted)]">{k.label}</div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="text-[28px] font-normal leading-9 tabular-nums text-[var(--ink)]">
                  {k.value.toLocaleString("zh-CN")}
                </span>
                <DeltaBadge delta={k.delta} />
              </div>
              <div className="mt-0.5 truncate text-[12px] text-[#8a8d8f]">{k.caption}</div>
            </div>
            {k.spark.length > 1 && <SparkBars values={k.spark} />}
          </div>
        ))}
      </div>

      {/* 主趋势 + 右列（漏斗/来源） */}
      <div className="mt-5 grid grid-cols-1 gap-6 xl:grid-cols-[1fr_360px]">
        <section className="rounded-lg border border-[var(--border)] bg-white p-5">
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">信号趋势 · 近 30 天</h2>
            <span className="text-[12px] text-[var(--muted)]">
              累计 {(sum?.trend ?? []).reduce((a, b) => a + b.count, 0)} 条
            </span>
          </div>
          <AreaTrend data={sum?.trend ?? []} />
        </section>

        <div className="space-y-5">
          {/* SPEC-J J6：加权管道预测（espocrm Forecasts 思想） */}
          <section className="rounded-lg border border-[var(--border)] bg-white p-5">
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
              </div>
            ) : (
              <div className="text-[13px] text-[var(--muted)]">
                给跟进中/意向商机填上金额、赢率与预计成交月，即可看到加权预测。
              </div>
            )}
          </section>
          <section className="rounded-lg border border-[var(--border)] bg-white p-5">
            <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">线索漏斗</h2>
            <FunnelBars items={sum?.funnel ?? []} />
          </section>
          <section className="rounded-lg border border-[var(--border)] bg-white p-5">
            <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">客户省份分布</h2>
            <ProvinceBars items={sum?.geo ?? []} />
          </section>
          <section className="rounded-lg border border-[var(--border)] bg-white p-5">
            <h2 className="mb-3 text-[15px] font-medium text-[var(--ink)]">企业来源分布</h2>
            <BarList
              items={(sum?.sources ?? []).map((s) => ({
                name: SOURCE_CN[s.name] ?? s.name,
                value: s.value,
              }))}
            />
          </section>
        </div>
      </div>

      {/* 待跟进 + 最新信号 */}
      <div className="mt-5 grid grid-cols-1 gap-6 xl:grid-cols-[1fr_360px]">
        <div>
          {/* 今日作战清单（SPEC-G G5：四源合并——逾期/到期/久未跟/高分新线索/规则命中） */}
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">今日作战</h2>
            <span className="text-[12px] text-[var(--muted)]">
              {myDay.length > 0 ? `${myDay.length} 项待处理` : "今天没有必须处理的事"}
            </span>
          </div>
          {myDay.length > 0 && (
            <div className="mb-3 space-y-1.5">
              {myDay.slice(0, 6).map((m) => (
                <div
                  key={`${m.kind}-${m.id}`}
                  className="flex items-center gap-2.5 rounded-lg border border-[var(--border)] bg-white px-3 py-2"
                >
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 text-[10.5px] font-medium ${
                      m.priority === 0
                        ? "bg-[color:var(--negative)]/10 text-[var(--negative)]"
                        : m.priority === 1
                          ? "bg-[color:var(--accent)]/10 text-[var(--accent)]"
                          : "bg-[var(--raised)] text-[var(--muted)]"
                    }`}
                  >
                    {{ overdue_todo: "逾期", due_today: "今日", stale_intent: "久未跟", fresh_lead: "新线索", rule_hits: "自动化" }[m.kind] ?? m.kind}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--ink)]" title={m.title}>
                    {m.title}
                  </span>
                  <span className="shrink-0 text-[11.5px] text-[var(--muted)]">{m.hint}</span>
                </div>
              ))}
            </div>
          )}

          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">我的待办</h2>
            <span className="text-[12px] text-[var(--muted)]">
              {overdue.length > 0 && (
                <span className="mr-2 text-[var(--negative)]">逾期 {overdue.length}</span>
              )}
              {dueToday.length > 0 && <span className="mr-2">今日 {dueToday.length}</span>}
              后续 {upcoming.length}
            </span>
          </div>
          <div className="mb-2 flex gap-2">
            <input
              value={newTodo}
              onChange={(e) => setNewTodo(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void addTodo();
              }}
              placeholder="新增待办，如：周三前回访北大设备处…"
              className="h-8 flex-1 rounded-lg border border-[var(--border)] bg-white px-3 text-[13px] outline-none focus:border-[var(--accent)]"
            />
            <Button size="small" theme="outline" onClick={() => void addTodo()}>
              添加
            </Button>
          </div>
          <div className="mb-5 max-h-[300px] space-y-1.5 overflow-auto">
            {openTodos.length === 0 && (
              <div className="rounded-lg border border-dashed border-[var(--border)] px-4 py-6 text-center text-[12.5px] text-[var(--muted)]">
                暂无待办 —— 自动化规则「自动建待办」会在这里出现
              </div>
            )}
            {[...overdue, ...dueToday, ...upcoming].slice(0, 12).map((t) => (
              <div
                key={t.id}
                className="flex items-center gap-2.5 rounded-lg border border-[var(--border)] bg-white px-3 py-2"
              >
                <input
                  type="checkbox"
                  checked={false}
                  onChange={() => void toggleTodo(t.id)}
                  className="h-4 w-4 cursor-pointer accent-[var(--accent)]"
                />
                <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--ink)]" title={t.title}>
                  {t.title}
                </span>
                {t.source === "automation" && (
                  <span className="shrink-0 rounded bg-[color:var(--accent)]/10 px-1.5 py-0.5 text-[10.5px] text-[var(--accent)]">
                    自动
                  </span>
                )}
                {t.due_date && (
                  <span
                    className={`shrink-0 text-[11.5px] tabular-nums ${
                      t.due_date < todayStr ? "font-medium text-[var(--negative)]" : "text-[var(--muted)]"
                    }`}
                  >
                    {t.due_date.slice(5)}
                  </span>
                )}
              </div>
            ))}
          </div>

          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">待跟进事项</h2>
            <button
              onClick={() => router.push("/leads")}
              className="flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-[13px] text-[var(--accent)] hover:underline"
            >
              全部线索 <ArrowRight size={12} />
            </button>
          </div>
          <Table<DashboardData["pending_followups"][number]>
            rowKey="lead_id"
            size="middle"
            loading={loading}
            dataSource={data?.pending_followups ?? []}
            pagination={false}
            empty="暂无待跟进 —— 从企业筛选页把高分客户转为线索"
            columns={[
              {
                title: "企业",
                dataIndex: "enterprise_name",
                render: (v: string) => <span className="font-medium text-[var(--ink)]">{v}</span>,
              },
              {
                title: "下一步",
                dataIndex: "next_action",
                render: (v: string) => v || <span className="text-[#9ca0a3]">待规划</span>,
              },
              {
                title: "约定时间",
                dataIndex: "next_time",
                width: 150,
                render: (v: string) => (v ? String(v).slice(0, 16).replace("T", " ") : "—"),
              },
            ]}
          />
        </div>

        <aside>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-[15px] font-medium text-[var(--ink)]">最新信号</h2>
            <button
              onClick={() => router.push("/enterprises")}
              className="flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-[13px] text-[var(--accent)] hover:underline"
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
      </div>
    </AppShell>
  );
}

type HealthData2 = { next_action?: { hint?: string } };
