"use client";

/**
 * 线索页（吸收 twenty record-board：拖拽看板 + 商机字段）。
 * 双视图：看板（默认，HTML5 拖拽流转阶段）/ 列表（原表格视图保留）。
 * 商机三要素（金额/赢率/预计成交）在卡片上直接编辑（twenty opportunity 模式）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button, DatePicker, Form, Input, InputNumber, Modal, Select, SideSheet, Table, Tabs, Tag, Timeline, Toast,
} from "@douyinfe/semi-ui";
import { KanbanSquare, PhoneCall, Rows3, Settings2 } from "lucide-react";
import AppShell from "@/components/AppShell";
import LeadCard from "@/components/LeadCard";
import { api } from "@/lib/api";
import type { FollowupItem, Lead, PageData } from "@/lib/api";
import { GRADE_COLOR, LEAD_STATUS, LOST_REASONS } from "@/lib/constants";

const MY_TABS = ["following", "new", "intent", "won", "lost", "pool"] as const;
const GRADE_TAG: Record<string, "red" | "orange" | "blue"> = { S: "red", A: "orange", B: "blue" };

/** 看板列（twenty pipeline 顺序；lost 终态放列表页看） */
const KANBAN_STAGES = ["new", "following", "intent", "won", "pool"] as const;

type ViewMode = "board" | "table" | "calendar";

export default function LeadsPage() {
  const [view, setView] = useState<ViewMode>("board");
  const [tab, setTab] = useState<string>("following");
  const [data, setData] = useState<PageData<Lead> | null>(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  // SPEC-T 公海批量领取：仅公海页签可用
  const [selectedPoolIds, setSelectedPoolIds] = useState<number[]>([]);
  const [claiming, setClaiming] = useState(false);

  // 看板数据：{status: Lead[]}
  const [board, setBoard] = useState<Record<string, Lead[]>>({});
  const [dragId, setDragId] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);
  // 日历视图（twenty record-calendar 模式）：按预计成交日落格，拖拽改期
  const [calMonth, setCalMonth] = useState(() => {
    const d = new Date();
    return { y: d.getFullYear(), m: d.getMonth() };
  });
  const [calDragDate, setCalDragDate] = useState<string | null>(null);
  const allLeads = useMemo(() => Object.values(board).flat(), [board]);

  const [followOpen, setFollowOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [lostOpen, setLostOpen] = useState(false);
  const [oppOpen, setOppOpen] = useState(false);
  const [current, setCurrent] = useState<Lead | null>(null);
  const [lostLead, setLostLead] = useState<Lead | null>(null);
  const [lostReason, setLostReason] = useState<string>("no_need");
  const [followups, setFollowups] = useState<FollowupItem[]>([]);
  // 统一时间线（twenty timeline 模块：跟进+信号合流）
  const [events, setEvents] = useState<
    { type: string; at: string; title: string; body: string; next_action?: string | null; next_time?: string | null }[]
  >([]);
  // 商机编辑表单
  const [oppForm, setOppForm] = useState({ amount_wan: "", probability: "", expected_close: "", next_action: "" });

  const load = useCallback(
    (t: string, p: number) => {
      setLoading(true);
      api
        .get<PageData<Lead>>(`/api/leads?status=${t}&page=${p}&page_size=20`)
        .then(setData)
        .catch((e) => Toast.error(e.message))
        .finally(() => setLoading(false));
    },
    [],
  );

  const loadBoard = useCallback(() => {
    // 看板一次拉全 5 列（每列上限 50，自用规模足够；分页语义交给列表视图）
    Promise.all(
      KANBAN_STAGES.map((s) =>
        api.get<PageData<Lead>>(`/api/leads?status=${s}&page=1&page_size=50`).catch(() => null),
      ),
    ).then((rs) => {
      const b: Record<string, Lead[]> = {};
      KANBAN_STAGES.forEach((s, i) => {
        b[s] = rs[i]?.items ?? [];
      });
      setBoard(b);
    });
  }, []);

  useEffect(() => {
    if (view === "table") load(tab, page);
    else loadBoard();
  }, [view, tab, page, load, loadBoard]);

  const refresh = () => (view === "table" ? load(tab, page) : loadBoard());

  const patchStatus = async (lead: Lead, status: string, lost_reason?: string) => {
    try {
      await api.patch(`/api/leads/${lead.id}`, { status, lost_reason });
      Toast.success(
        status === "pool" ? "已退回公海" : `已流转到「${LEAD_STATUS[status]?.label ?? status}」（赢率 ${status === "won" ? 100 : status === "intent" ? 70 : status === "following" ? 40 : 20}%）`,
      );
      refresh();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const submitFollow = async (values: { content: string; next_action?: string; next_time?: string }) => {
    if (!current) return;
    try {
      await api.post(`/api/leads/${current.id}/followups`, {
        content: values.content,
        next_action: values.next_action || undefined,
        next_time: values.next_time || undefined,
      });
      Toast.success("跟进已记录");
      setFollowOpen(false);
      refresh();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    }
  };

  const openHistory = async (lead: Lead) => {
    setCurrent(lead);
    setHistoryOpen(true);
    setFollowups([]);
    try {
      const d = await api.get<{
        items: { type: string; at: string; title: string; body: string; next_action?: string | null }[];
      }>(`/api/leads/${lead.id}/timeline`);
      setEvents(d.items);
    } catch {
      setEvents([]);
    }
  };

  const openOpp = (lead: Lead) => {
    setCurrent(lead);
    setOppForm({
      amount_wan: lead.amount_wan != null ? String(lead.amount_wan) : "",
      probability: lead.probability != null ? String(lead.probability) : "",
      expected_close: lead.expected_close || "",
      next_action: lead.next_action || "",
    });
    setOppOpen(true);
  };

  const saveOpp = async () => {
    if (!current) return;
    try {
      await api.patch(`/api/leads/${current.id}/opportunity`, {
        amount_wan: oppForm.amount_wan === "" ? null : Number(oppForm.amount_wan),
        probability: oppForm.probability === "" ? null : Number(oppForm.probability),
        expected_close: oppForm.expected_close || null,
        next_action: oppForm.next_action || null,
      });
      Toast.success("商机信息已更新");
      setOppOpen(false);
      refresh();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    }
  };

  /** 看板拖拽：drop 到目标列 → 状态流转 */
  const onDropTo = async (status: string) => {
    setDragOver(null);
    if (dragId == null) return;
    const lead = Object.values(board).flat().find((l) => l.id === dragId);
    setDragId(null);
    if (!lead || lead.status === status) return;
    if (status === "lost") {
      setLostLead(lead);
      setLostReason("no_need");
      setLostOpen(true);
      return;
    }
    await patchStatus(lead, status);
  };

  /** 日历拖拽改期：drop 到某天 → PATCH opportunity.expected_close（twenty 拖拽改期模式） */
  const onCalDrop = async (date: string) => {
    setCalDragDate(null);
    const lead = allLeads.find((l) => l.id === dragId);
    setDragId(null);
    if (!lead || lead.expected_close === date) return;
    try {
      await api.patch(`/api/leads/${lead.id}/opportunity`, { expected_close: date });
      Toast.success(`预计成交日改为 ${date}`);
      loadBoard();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "改期失败");
    }
  };

  const actions = (lead: Lead) => {
    const btns: React.ReactNode[] = [];
    if (lead.status === "new" || lead.status === "pool") {
      btns.push(
        <Button key="claim" size="small" theme="solid" onClick={() => patchStatus(lead, "following")}>
          {lead.status === "pool" ? "领取" : "开始跟进"}
        </Button>,
      );
    }
    if (lead.status === "following") {
      btns.push(
        <Button key="intent" size="small" onClick={() => patchStatus(lead, "intent")}>
          标记意向
        </Button>,
        <Button
          key="lost"
          size="small"
          type="danger"
          theme="borderless"
          onClick={() => {
            setLostLead(lead);
            setLostReason("no_need");
            setLostOpen(true);
          }}
        >
          放弃
        </Button>,
      );
    }
    if (lead.status === "intent") {
      btns.push(
        <Button key="won" size="small" theme="solid" onClick={() => patchStatus(lead, "won")}>
          成交
        </Button>,
        <Button
          key="lost2"
          size="small"
          type="danger"
          theme="borderless"
          onClick={() => {
            setLostLead(lead);
            setLostReason("no_need");
            setLostOpen(true);
          }}
        >
          放弃
        </Button>,
      );
    }
    btns.push(
      <Button key="follow" size="small" theme="light" icon={<PhoneCall size={13} />} onClick={() => { setCurrent(lead); setFollowOpen(true); }}>
        跟进
      </Button>,
      <Button key="opp" size="small" theme="borderless" icon={<Settings2 size={13} />} onClick={() => openOpp(lead)}>
        商机
      </Button>,
      <Button key="detail" size="small" theme="borderless" type="tertiary" onClick={() => openHistory(lead)}>
        记录
      </Button>,
    );
    return <div className="flex items-center gap-1.5 flex-wrap">{btns}</div>;
  };

  const columns = [
    {
      title: "企业",
      dataIndex: ["enterprise", "name"],
      render: (v: string) => <span className="font-medium text-slate-700">{v}</span>,
    },
    { title: "行业", dataIndex: ["enterprise", "industry_name"], width: 120, render: (v: string) => v || "-" },
    {
      title: "商机",
      width: 140,
      render: (_: unknown, r: Lead) => (
        <span className="text-[12.5px] tabular-nums text-slate-600">
          {r.amount_wan != null ? `${r.amount_wan} 万` : "—"}{" "}
          <span className="text-slate-300">·</span> {r.probability ?? 0}%
        </span>
      ),
    },
    {
      title: "等级",
      dataIndex: "grade",
      width: 64,
      render: (v: string) => (v ? <Tag color={GRADE_TAG[v]} type="solid">{v}</Tag> : <span className="text-slate-300">-</span>),
    },
    {
      title: "最近跟进",
      dataIndex: "last_followed_at",
      width: 120,
      render: (v: string) => (v ? <span className="text-slate-500 text-[12.5px]">{String(v).slice(0, 16).replace("T", " ")}</span> : <span className="text-slate-300">未跟进</span>),
    },
    { title: "操作", width: 330, render: (_: unknown, r: Lead) => actions(r) },
  ];

  /** 日历月视图：7 列网格，商机卡按 expected_close 落格，可拖拽改期 */
  const calendarEl = useMemo(() => {
    const first = new Date(calMonth.y, calMonth.m, 1);
    const startWeekday = (first.getDay() + 6) % 7; // 周一为首
    const daysInMonth = new Date(calMonth.y, calMonth.m + 1, 0).getDate();
    const todayStr = new Date().toISOString().slice(0, 10);
    const byDate: Record<string, Lead[]> = {};
    for (const l of allLeads) {
      if (l.expected_close) {
        (byDate[l.expected_close] ??= []).push(l);
      }
    }
    const cells: (string | null)[] = [
      ...Array.from({ length: startWeekday }, () => null),
      ...Array.from(
        { length: daysInMonth },
        (_, i) => `${calMonth.y}-${String(calMonth.m + 1).padStart(2, "0")}-${String(i + 1).padStart(2, "0")}`,
      ),
    ];
    while (cells.length % 7 !== 0) cells.push(null);
    const monthLabel = `${calMonth.y} 年 ${calMonth.m + 1} 月`;
    return (
      <div>
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Button size="small" theme="borderless" onClick={() => setCalMonth((c) => (c.m === 0 ? { y: c.y - 1, m: 11 } : { y: c.y, m: c.m - 1 }))}>←</Button>
            <span className="text-[15px] font-medium text-[var(--ink)]">{monthLabel}</span>
            <Button size="small" theme="borderless" onClick={() => setCalMonth((c) => (c.m === 11 ? { y: c.y + 1, m: 0 } : { y: c.y, m: c.m + 1 }))}>→</Button>
            <Button size="small" theme="borderless" type="tertiary" onClick={() => { const d = new Date(); setCalMonth({ y: d.getFullYear(), m: d.getMonth() }); }}>今天</Button>
          </div>
          <span className="text-[12px] text-[var(--muted)]">商机按预计成交日落格 · 拖动卡片改期（未设日期的商机不出现在日历）</span>
        </div>
        <div className="grid grid-cols-7 gap-px overflow-hidden rounded-lg bg-[var(--frame)]">
          {["一", "二", "三", "四", "五", "六", "日"].map((w) => (
            <div key={w} className="bg-[var(--raised)] py-1.5 text-center text-[11.5px] text-[var(--muted)]">周{w}</div>
          ))}
          {cells.map((date, idx) => {
            if (date === null) return <div key={`e${idx}`} className="min-h-[96px] bg-[#fafbfb]" />;
            const items = byDate[date] ?? [];
            const isToday = date === todayStr;
            return (
              <div
                key={date}
                onDragOver={(e) => { e.preventDefault(); setCalDragDate(date); }}
                onDragLeave={() => setCalDragDate((cur) => (cur === date ? null : cur))}
                onDrop={() => void onCalDrop(date)}
                className={`min-h-[96px] bg-white px-1.5 py-1 ${calDragDate === date ? "outline outline-2 -outline-offset-1 outline-[var(--accent)]" : ""}`}
              >
                <div className={`mb-1 inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[11px] tabular-nums ${isToday ? "bg-[var(--accent)] text-white" : "text-[var(--muted)]"}`}>
                  {Number(date.slice(8))}
                </div>
                {items.map((l) => (
                  <div
                    key={l.id}
                    draggable
                    onDragStart={() => setDragId(l.id)}
                    onDragEnd={() => { setDragId(null); setCalDragDate(null); }}
                    title={`${l.enterprise?.name} · ${l.probability ?? 0}%${l.amount_wan != null ? ` · ${Math.round(l.amount_wan)}万` : ""}（拖动改期）`}
                    className={`mb-1 cursor-grab truncate rounded bg-[color:var(--accent)]/8 px-1.5 py-1 text-[10.5px] leading-4 text-[var(--ink)] hover:bg-[color:var(--accent)]/15 ${dragId === l.id ? "opacity-40" : ""}`}
                  >
                    {l.enterprise?.name}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [calMonth, allLeads, dragId, calDragDate]);

  const boardEl = useMemo(
    () => (
      <div className="flex gap-3 overflow-x-auto pb-2">
        {KANBAN_STAGES.map((s) => {
          const items = board[s] ?? [];
          const meta = LEAD_STATUS[s];
          const amountSum = items.reduce((a, b) => a + (b.amount_wan ?? 0), 0);
          return (
            <div
              key={s}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(s);
              }}
              onDragLeave={() => setDragOver((cur) => (cur === s ? null : cur))}
              onDrop={() => void onDropTo(s)}
              className={`flex w-[236px] shrink-0 flex-col rounded-xl border bg-[var(--raised)] px-2.5 py-3 ${
                dragOver === s ? "border-[var(--accent)] bg-[color:var(--accent)]/5" : "border-[var(--border)]"
              }`}
            >
              <div className="mb-2 flex items-baseline justify-between px-0.5">
                <span className="text-[13px] font-medium text-[var(--ink)]">{meta?.label}</span>
                <span className="text-[11.5px] tabular-nums text-[var(--muted)]">
                  {items.length}{amountSum > 0 ? ` · ${Math.round(amountSum)}万` : ""}
                </span>
              </div>
              <div className="min-h-[320px] flex-1">
                {items.length === 0 ? (
                  <div className="flex h-32 items-center justify-center rounded-lg border border-dashed border-[var(--border)] text-[11.5px] text-[var(--muted)]">
                    拖卡片到这里
                  </div>
                ) : (
                  items.map((l) => (
                    <LeadCard
                      key={l.id}
                      lead={l}
                      dragging={dragId === l.id}
                      onDragStart={() => setDragId(l.id)}
                      onDragEnd={() => {
                        setDragId(null);
                        setDragOver(null);
                      }}
                      onOpen={() => openOpp(l)}
                    />
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [board, dragId, dragOver],
  );

  return (
    <AppShell>
      {/* 标题行 + 视图切换 */}
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h1 className="text-[20px] font-semibold leading-7 text-[var(--ink)]">商机管线</h1>
          <p className="mt-0.5 text-[13px] text-[var(--muted)]">
            拖拽卡片流转阶段（赢率自动更新）· 点击卡片编辑金额/赢率/预计成交
          </p>
        </div>
        <Tabs
          type="button"
          size="small"
          activeKey={view}
          onChange={(k) => setView(k as ViewMode)}
          tabList={[
            { tab: "看板", itemKey: "board" },
            { tab: "日历", itemKey: "calendar" },
            { tab: "列表", itemKey: "table" },
          ]}
        />
      </div>

      {view === "board" ? (
        boardEl
      ) : view === "calendar" ? (
        calendarEl
      ) : (
        <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-white">
          <Tabs
            type="line"
            activeKey={tab}
            onChange={(k) => {
              setTab(k as string);
              setPage(1);
            }}
            className="!px-4 !pt-1"
            tabList={MY_TABS.map((s) => ({ tab: LEAD_STATUS[s].label, itemKey: s }))}
          />
          {tab === "pool" && (
            <div className="!px-4 !pb-1">
              <Button
                size="small"
                theme="solid"
                disabled={selectedPoolIds.length === 0}
                loading={claiming}
                onClick={async () => {
                  setClaiming(true);
                  try {
                    const r = await api.post<{ claimed: number; skipped_limit: number }>(
                      "/api/leads/batch-claim",
                      { ids: selectedPoolIds },
                    );
                    Toast.success(`已领取 ${r.claimed} 条${r.skipped_limit ? `，${r.skipped_limit} 条超持有上限未领` : ""}`);
                    setSelectedPoolIds([]);
                    void load(tab, page);
                  } catch (e) {
                    Toast.error(e instanceof Error ? e.message : "领取失败");
                  } finally {
                    setClaiming(false);
                  }
                }}
              >
                批量领取所选（{selectedPoolIds.length}）
              </Button>
            </div>
          )}
          <Table<Lead>
            rowKey="id"
            rowSelection={{
              selectedRowKeys: selectedPoolIds,
              onChange: (keys?: (string | number)[]) =>
                setSelectedPoolIds((keys ?? []).map(Number)),
            }}
            columns={columns as never}
            dataSource={data?.items ?? []}
            loading={loading}
            size="middle"
            empty={
              tab === "pool"
                ? "公海暂时没有可领取的线索"
                : "该状态下暂无线索 —— 去企业筛选页转化高分客户"
            }
            pagination={{
              currentPage: page,
              pageSize: 20,
              total: data?.total ?? 0,
              onPageChange: (p: number) => setPage(p),
            }}
          />
        </div>
      )}

      {/* 跟进弹窗 */}
      <Modal
        title={`跟进 — ${current?.enterprise?.name ?? ""}`}
        visible={followOpen}
        onCancel={() => setFollowOpen(false)}
        footer={null}
        width={480}
      >
        <Form onSubmit={submitFollow} labelPosition="top">
          <Form.TextArea
            field="content"
            label="跟进内容"
            rows={4}
            rules={[{ required: true, message: "请填写跟进内容" }]}
            placeholder="沟通了什么？客户态度如何？"
          />
          <Form.Input field="next_action" label="下一步动作" placeholder="如：发送设备参数表 / 约参观工厂" />
          <Form.DatePicker
            field="next_time"
            label="下次跟进时间"
            type="dateTime"
            style={{ width: "100%" }}
          />
          <Button htmlType="submit" theme="solid" block className="!mt-2">
            保存跟进
          </Button>
        </Form>
      </Modal>

      {/* 商机编辑（twenty opportunity 三要素） */}
      <Modal
        title={`商机 — ${current?.enterprise?.name ?? ""}`}
        visible={oppOpen}
        onOk={() => void saveOpp()}
        onCancel={() => setOppOpen(false)}
        okText="保存"
        width={440}
      >
        <div className="space-y-3.5 pt-1">
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">商机金额（万元）</div>
            <InputNumber
              style={{ width: "100%" }}
              min={0}
              max={1000000}
              value={oppForm.amount_wan === "" ? undefined : Number(oppForm.amount_wan)}
              onChange={(v) => setOppForm((f) => ({ ...f, amount_wan: v == null ? "" : String(v) }))}
              placeholder="如：120"
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">赢率（%，阶段流转会自动更新）</div>
            <InputNumber
              style={{ width: "100%" }}
              min={0}
              max={100}
              value={oppForm.probability === "" ? undefined : Number(oppForm.probability)}
              onChange={(v) => setOppForm((f) => ({ ...f, probability: v == null ? "" : String(v) }))}
              placeholder="0-100"
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">预计成交日</div>
            <Input
              value={oppForm.expected_close}
              onChange={(v) => setOppForm((f) => ({ ...f, expected_close: v }))}
              placeholder="YYYY-MM-DD"
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">下一步动作</div>
            <Input
              value={oppForm.next_action}
              onChange={(v) => setOppForm((f) => ({ ...f, next_action: v }))}
              placeholder="如：寄送样品 / 邀约验厂"
            />
          </div>
        </div>
      </Modal>

      {/* 跟进历史 */}
      <SideSheet
        title={`跟进历史 — ${current?.enterprise?.name ?? ""}`}
        visible={historyOpen}
        onCancel={() => setHistoryOpen(false)}
        width={480}
      >
        {events.length === 0 ? (
          <div className="text-[13px] text-slate-400">暂无记录 —— 跟进动作与系统捕获的信号会合流到这条时间线</div>
        ) : (
          <Timeline mode="left" className="mt-2">
            {events.map((ev, i) => {
              const isFollow = ev.type === "followup";
              const badge =
                ev.type === "signal_tender_win" ? { text: "中标", color: "cyan" as const } :
                ev.type === "signal_tender_buy" ? { text: "公开采购", color: "green" as const } :
                ev.type === "signal_tender" ? { text: "政采公告", color: "blue" as const } :
                { text: "跟进", color: "blue" as const };
              return (
                <Timeline.Item key={i} time={ev.at.slice(0, 16).replace("T", " ")}>
                  <div className="flex items-center gap-1.5">
                    <Tag color={badge.color} type="light" size="small">{badge.text}</Tag>
                    {isFollow && <span className="text-[13.5px] font-medium text-slate-700">{ev.title}</span>}
                  </div>
                  <div className={`mt-1 text-[13px] leading-6 ${isFollow ? "text-slate-700" : "text-[var(--ink)]"}`}>
                    {ev.body}
                  </div>
                  {(ev.next_action || ev.next_time) && (
                    <div className="mt-1 flex items-center gap-1.5 flex-wrap">
                      {ev.next_action && <Tag color="blue" type="light">{ev.next_action}</Tag>}
                      {ev.next_time && <Tag color="orange" type="light">{String(ev.next_time).slice(0, 16)}</Tag>}
                    </div>
                  )}
                </Timeline.Item>
              );
            })}
          </Timeline>
        )}
      </SideSheet>

      {/* 放弃原因 */}
      <Modal
        title={`放弃线索 — ${lostLead?.enterprise?.name ?? ""}`}
        visible={lostOpen}
        onOk={() => {
          if (lostLead) void patchStatus(lostLead, "lost", lostReason);
          setLostOpen(false);
        }}
        onCancel={() => setLostOpen(false)}
        okText="确认放弃"
        okButtonProps={{ type: "danger", theme: "solid" }}
      >
        <Select
          style={{ width: "100%" }}
          optionList={Object.entries(LOST_REASONS).map(([value, label]) => ({ value, label }))}
          value={lostReason}
          onChange={(v) => setLostReason(v as string)}
        />
      </Modal>
    </AppShell>
  );
}
