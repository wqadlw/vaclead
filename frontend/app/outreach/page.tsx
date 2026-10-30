"use client";

/**
 * 序列触达（SPEC-J J4）：邮件序列编排 + 触达活动 + 发信账户。
 * 上下分区：邮件序列（listmonk 式变量模板）→ 触达活动（入队/启停/立即调度）→ 发信账户。
 * 静态导出：纯 client 页面，无查询参数；API 失败一律空态 + Toast 兜底（后端可后就绪）。
 * framed-sheet：白纸浅框、扁平、无渐变无阴影，主色青蓝 #1b6c80。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Input,
  InputNumber,
  Progress,
  Select,
  SideSheet,
  Table,
  Tag,
  Toast,
} from "@douyinfe/semi-ui";
import {
  ArrowDown,
  ArrowUp,
  ListChecks,
  Pause,
  Play,
  Plus,
  Trash2,
  Zap,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

// ---------- 类型（契约形状 + 可选兜底字段：后端返回什么就用什么，缺了走前端估算） ----------
interface SeqStep {
  delay_days: number;
  subject: string;
  body: string;
}
type StepDraft = SeqStep & { _key: number };

interface SequenceRow {
  id: number;
  name: string;
  steps: SeqStep[];
}

interface EmailAccountRow {
  id: number;
  name: string;
  email: string;
  provider: string; // emailengine | smtp
  smtp_host: string | null;
  smtp_port: number | null;
  daily_cap: number;
  warmup_started_at: string | null;
  status?: string;
  health?: string;
}

interface CampaignRow {
  id: number;
  name: string;
  sequence_id: number;
  email_account_id: number;
  status: string; // draft | running | paused | done
  daily_limit: number;
  // 后端可直接返回（契约外补充字段），缺省时前端从 messages 统计/按上限估算
  today_sent?: number;
  budget?: number;
  sequence_name?: string | null;
  account_email?: string | null;
}

interface MessageRow {
  id: number;
  campaign_id: number;
  status: string; // queued | sent | failed | bounced | replied
  sent_at: string | null;
}

interface EnqueueResult {
  created?: number;
  skipped_dup?: number;
  skipped_unsub?: number;
}

// ---------- 常量 ----------
const CAMPAIGN_TAG: Record<string, { label: string; color: "grey" | "green" | "amber" | "blue" }> = {
  draft: { label: "草稿", color: "grey" },
  running: { label: "发送中", color: "green" },
  paused: { label: "已暂停", color: "amber" },
  done: { label: "已完成", color: "blue" },
};
const ACCOUNT_TAG: Record<string, { label: string; color: "blue" | "grey" }> = {
  emailengine: { label: "EmailEngine", color: "blue" },
  smtp: { label: "SMTP 直连", color: "grey" },
};
const HEALTH_TAG: Record<string, { label: string; color: "green" | "amber" | "red" }> = {
  healthy: { label: "健康", color: "green" },
  watch: { label: "观察", color: "amber" },
  throttled: { label: "限速", color: "amber" },
  quarantined: { label: "隔离", color: "red" },
  blocked: { label: "封禁", color: "red" },
};
const VARIABLES = [
  "{{lead.company}}",
  "{{contact.name}}",
  "{{enterprise.city}}",
  "{{sender.name}}",
  "{{unsubscribe_url}}",
];
const VARIABLE_HINTS: Record<string, string> = {
  "{{lead.company}}": "线索所属企业名",
  "{{contact.name}}": "联系人姓名",
  "{{enterprise.city}}": "企业所在城市",
  "{{sender.name}}": "发信人姓名",
  "{{unsubscribe_url}}": "每信自动附带的退订链接",
};

/** 兼容 PageData / {items} / 裸数组三种返回形状 */
function extractItems<T>(d: unknown): T[] {
  if (Array.isArray(d)) return d as T[];
  if (d && typeof d === "object" && Array.isArray((d as { items?: unknown }).items)) {
    return (d as { items: T[] }).items;
  }
  return [];
}

const flatTextareaCls =
  "w-full rounded-lg border border-[var(--border)] bg-white p-3 text-[12.5px] leading-5 outline-none focus:border-[var(--accent)]";

// ---------- 页面 ----------
export default function OutreachPage() {
  const [sequences, setSequences] = useState<SequenceRow[]>([]);
  const [campaigns, setCampaigns] = useState<CampaignRow[]>([]);
  const [accounts, setAccounts] = useState<EmailAccountRow[]>([]);
  const [messages, setMessages] = useState<MessageRow[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);

  const load = useCallback(() => {
    let failCount = 0;
    const onFail = () => {
      failCount += 1;
      if (failCount === 1) setLoadFailed(true);
    };
    api
      .get<unknown>("/api/outreach/sequences")
      .then((d) => setSequences(extractItems<SequenceRow>(d)))
      .catch(onFail);
    api
      .get<unknown>("/api/outreach/campaigns")
      .then((d) => setCampaigns(extractItems<CampaignRow>(d)))
      .catch(onFail);
    api
      .get<unknown>("/api/outreach/accounts")
      .then((d) => setAccounts(extractItems<EmailAccountRow>(d)))
      .catch(onFail);
    api
      .get<unknown>("/api/outreach/messages?page_size=500")
      .then((d) => setMessages(extractItems<MessageRow>(d)))
      .catch(onFail);
  }, []);
  useEffect(load, [load]);

  // ---- 派生：客户端联表（后端字段缺省时的兜底） ----
  const seqNameById = useMemo(
    () => new Map(sequences.map((s) => [s.id, s.name] as const)),
    [sequences],
  );
  const accountById = useMemo(
    () => new Map(accounts.map((a) => [a.id, a] as const)),
    [accounts],
  );
  const seqRefCount = useMemo(() => {
    const m = new Map<number, number>();
    for (const c of campaigns) m.set(c.sequence_id, (m.get(c.sequence_id) ?? 0) + 1);
    return m;
  }, [campaigns]);
  const todaySentByCampaign = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const m = new Map<number, number>();
    for (const msg of messages) {
      if (msg.status !== "sent" || !msg.sent_at || !String(msg.sent_at).startsWith(today)) continue;
      m.set(msg.campaign_id, (m.get(msg.campaign_id) ?? 0) + 1);
    }
    return m;
  }, [messages]);

  const sentOf = (c: CampaignRow): number => {
    if (typeof c.today_sent === "number") return c.today_sent;
    return todaySentByCampaign.get(c.id) ?? 0;
  };
  const budgetOf = (c: CampaignRow): number => {
    if (typeof c.budget === "number") return c.budget;
    const acc = accountById.get(c.email_account_id);
    return Math.min(acc?.daily_cap ?? 20, c.daily_limit ?? 50);
  };

  // ==================== 序列编排器 ====================
  const [seqDrawer, setSeqDrawer] = useState(false);
  const [seqEditing, setSeqEditing] = useState<SequenceRow | null>(null);
  const [seqName, setSeqName] = useState("");
  const [steps, setSteps] = useState<StepDraft[]>([]);
  const [seqSaving, setSeqSaving] = useState(false);
  const stepKeyRef = useRef(1);
  const newStep = (delay: number): StepDraft => ({
    _key: stepKeyRef.current++,
    delay_days: delay,
    subject: "",
    body: "",
  });

  const openSeqNew = () => {
    setSeqEditing(null);
    setSeqName("");
    setSteps([newStep(0)]);
    setSeqDrawer(true);
  };
  const openSeqEdit = (s: SequenceRow) => {
    setSeqEditing(s);
    setSeqName(s.name);
    const rows = (s.steps ?? []).map((st) => ({
      _key: stepKeyRef.current++,
      delay_days: Number(st.delay_days) || 0,
      subject: st.subject ?? "",
      body: st.body ?? "",
    }));
    setSteps(rows.length ? rows : [newStep(0)]);
    setSeqDrawer(true);
  };

  const patchStep = (idx: number, patch: Partial<SeqStep>) =>
    setSteps((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
  const moveStep = (idx: number, dir: -1 | 1) =>
    setSteps((prev) => {
      const j = idx + dir;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });
  const removeStep = (idx: number) =>
    setSteps((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== idx)));
  const addStep = () =>
    setSteps((prev) => [...prev, newStep(prev.length ? 2 : 0)]);

  /** 变量 chips：插入到最近聚焦的正文框光标处 */
  const bodyFocusRef = useRef<{ key: number; el: HTMLTextAreaElement } | null>(null);
  const insertVar = (v: string) => {
    const f = bodyFocusRef.current;
    if (!f || !f.el.isConnected) {
      Toast.info("请先点击要插入变量的邮件正文框");
      return;
    }
    const idx = steps.findIndex((s) => s._key === f.key);
    if (idx < 0) {
      Toast.info("请先点击要插入变量的邮件正文框");
      return;
    }
    const el = f.el;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? el.value.length;
    const body = el.value.slice(0, start) + v + el.value.slice(end);
    patchStep(idx, { body });
    const caret = start + v.length;
    requestAnimationFrame(() => {
      el.focus();
      el.selectionStart = el.selectionEnd = caret;
    });
  };

  const saveSeq = async () => {
    if (!seqName.trim()) {
      Toast.warning("请填写序列名称");
      return;
    }
    if (steps.length === 0 || steps.some((s) => !s.subject.trim())) {
      Toast.warning("每一步都需要邮件主题");
      return;
    }
    setSeqSaving(true);
    try {
      const payload = {
        name: seqName.trim(),
        steps: steps.map((s) => ({
          delay_days: Math.max(0, Number(s.delay_days) || 0),
          subject: s.subject,
          body: s.body,
        })),
      };
      if (seqEditing) await api.put(`/api/outreach/sequences/${seqEditing.id}`, payload);
      else await api.post("/api/outreach/sequences", payload);
      Toast.success(seqEditing ? "序列已保存" : "序列已创建");
      setSeqDrawer(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSeqSaving(false);
    }
  };
  const delSeq = async (s: SequenceRow) => {
    try {
      await api.delete(`/api/outreach/sequences/${s.id}`);
      Toast.success("序列已删除");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "删除失败（被活动引用时不可删）");
    }
  };

  // ==================== 触达活动 ====================
  const [campDrawer, setCampDrawer] = useState(false);
  const [campName, setCampName] = useState("");
  const [campSeqId, setCampSeqId] = useState<number | null>(null);
  const [campAccId, setCampAccId] = useState<number | null>(null);
  const [campLimit, setCampLimit] = useState<number>(50);
  const [campSaving, setCampSaving] = useState(false);

  const saveCampaign = async () => {
    if (!campName.trim() || !campSeqId || !campAccId) {
      Toast.warning("填活动名，并选择序列与发信账户");
      return;
    }
    setCampSaving(true);
    try {
      await api.post("/api/outreach/campaigns", {
        name: campName.trim(),
        sequence_id: campSeqId,
        email_account_id: campAccId,
        daily_limit: Math.max(1, Number(campLimit) || 50),
      });
      Toast.success("活动已创建（草稿态），启动后开始按预算发送");
      setCampDrawer(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCampSaving(false);
    }
  };

  const flipStatus = async (c: CampaignRow) => {
    const next = c.status === "running" ? "paused" : "running";
    try {
      await api.put(`/api/outreach/campaigns/${c.id}`, { status: next });
      Toast.success(next === "running" ? "活动已启动" : "活动已暂停");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const enqueue = async (c: CampaignRow) => {
    try {
      const r = await api.post<EnqueueResult>(`/api/outreach/campaigns/${c.id}/enqueue`);
      Toast.success(
        `发送队列已生成：新增 ${r.created ?? 0} 封，重复跳过 ${r.skipped_dup ?? 0}，退订跳过 ${r.skipped_unsub ?? 0}`,
      );
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "生成队列失败");
    }
  };

  // ---- 立即调度（POST /api/tasks/outreach-dispatch，复用任务 SSE 模式） ----
  const esRef = useRef<EventSource | null>(null);
  const [dispTaskId, setDispTaskId] = useState<string | null>(null);
  const [dispProgress, setDispProgress] = useState<number | null>(null);
  const [dispLogs, setDispLogs] = useState<string[]>([]);
  const [dispStarting, setDispStarting] = useState(false);

  const subscribeDispatch = useCallback(
    (tid: string) => {
      esRef.current?.close();
      const base = window.location.port === "3000" ? "http://127.0.0.1:8000" : "";
      const es = new EventSource(`${base}/api/tasks/${tid}/events`);
      esRef.current = es;
      setDispTaskId(tid);
      setDispProgress(0);
      es.onmessage = (ev) => {
        try {
          const p = JSON.parse(ev.data) as { type: string; progress?: number; log?: string; message?: string };
          if (p.type === "snapshot" || p.type === "progress") {
            if (typeof p.progress === "number") setDispProgress(p.progress);
            if (p.log) setDispLogs((prev) => [...prev.slice(-100), String(p.log)]);
          } else if (p.type === "done") {
            setDispProgress(100);
            setDispLogs((prev) => [...prev, p.log ?? "✅ 调度完成"]);
            es.close();
            setDispTaskId(null);
            setDispStarting(false);
            load();
          } else if (p.type === "cancelled") {
            setDispLogs((prev) => [...prev, "⏹ 调度已取消"]);
            es.close();
            setDispTaskId(null);
            setDispStarting(false);
          } else if (p.type === "error") {
            setDispLogs((prev) => [...prev, `❌ ${p.message ?? p.log ?? "调度失败"}`]);
            es.close();
            setDispTaskId(null);
            setDispStarting(false);
          }
        } catch {
          /* 非 JSON 帧忽略 */
        }
      };
      es.onerror = () => {
        es.close();
        setDispTaskId(null);
        setDispStarting(false);
      };
    },
    [load],
  );
  useEffect(() => () => esRef.current?.close(), []);

  const dispatchNow = async () => {
    setDispStarting(true);
    setDispProgress(0);
    setDispLogs(["触达调度任务提交中…"]);
    try {
      const r = await api.post<{ task_id: string }>("/api/tasks/outreach-dispatch", {});
      Toast.success("调度任务已启动");
      subscribeDispatch(r.task_id);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "调度启动失败");
      setDispStarting(false);
    }
  };
  const cancelDispatch = async () => {
    if (!dispTaskId) return;
    await api.post(`/api/tasks/${dispTaskId}/cancel`).catch(() => undefined);
    Toast.info("取消请求已发送");
  };

  // ==================== 发信账户 ====================
  const [accDrawer, setAccDrawer] = useState(false);
  const [accForm, setAccForm] = useState({
    name: "",
    email: "",
    provider: "emailengine",
    smtp_host: "",
    smtp_port: 465,
    smtp_user: "",
    smtp_pass: "",
    daily_cap: 20,
    warmup_started_at: "",
  });
  const [accSaving, setAccSaving] = useState(false);
  const openAccNew = () => {
    setAccForm({
      name: "",
      email: "",
      provider: "emailengine",
      smtp_host: "",
      smtp_port: 465,
      smtp_user: "",
      smtp_pass: "",
      daily_cap: 20,
      warmup_started_at: "",
    });
    setAccDrawer(true);
  };
  const saveAccount = async () => {
    if (!accForm.name.trim() || !accForm.email.trim()) {
      Toast.warning("填账户名与发信邮箱");
      return;
    }
    setAccSaving(true);
    try {
      await api.post("/api/outreach/accounts", {
        name: accForm.name.trim(),
        email: accForm.email.trim(),
        provider: accForm.provider,
        smtp_host: accForm.smtp_host.trim() || null,
        smtp_port: Number(accForm.smtp_port) || null,
        smtp_user: accForm.smtp_user.trim() || null,
        smtp_pass: accForm.smtp_pass || null,
        daily_cap: Math.max(1, Number(accForm.daily_cap) || 20),
        warmup_started_at: accForm.warmup_started_at || null,
      });
      Toast.success("发信账户已创建");
      setAccDrawer(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "创建失败");
    } finally {
      setAccSaving(false);
    }
  };

  // ---------- 渲染 ----------
  const SectionHead = ({
    title,
    desc,
    children,
  }: {
    title: string;
    desc?: string;
    children?: React.ReactNode;
  }) => (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
      <div>
        <div className="text-[14.5px] font-medium text-[var(--ink)]">{title}</div>
        {desc && <div className="mt-0.5 text-[12px] leading-5 text-[var(--muted)]">{desc}</div>}
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </div>
  );

  return (
    <AppShell>
      {/* ===== 区一：序列编排器 ===== */}
      <section>
        <SectionHead
          title="邮件序列"
          desc="listmonk 式步骤模板：第 1 步当天发，后续步间隔 N 天；正文支持变量，退订链接每信自动附带。"
        >
          <Button theme="solid" icon={<Plus size={14} />} onClick={openSeqNew}>
            新建序列
          </Button>
        </SectionHead>
        <div className="rounded-xl border border-[var(--border)] bg-white">
          <Table<SequenceRow>
            size="small"
            dataSource={sequences}
            rowKey="id"
            pagination={false}
            empty="还没有序列 —— 先建一条首触序列，再挂到活动上"
            columns={[
              {
                title: "序列",
                dataIndex: "name",
                render: (v: string, s: SequenceRow) => (
                  <div>
                    <span className="font-medium text-[var(--ink)]">{v}</span>
                    <span className="ml-2 text-[11.5px] text-[var(--muted)]">
                      {(s.steps ?? [])[0]?.subject || ""}
                    </span>
                  </div>
                ),
              },
              {
                title: "步骤",
                width: 90,
                render: (_: unknown, s: SequenceRow) => `${s.steps?.length ?? 0} 步`,
              },
              {
                title: "被引用",
                width: 130,
                render: (_: unknown, s: SequenceRow) => {
                  const n = seqRefCount.get(s.id) ?? 0;
                  return n ? (
                    <span className="text-[var(--ink)]">{n} 个活动</span>
                  ) : (
                    <span className="text-[var(--muted)]">未被引用</span>
                  );
                },
              },
              {
                title: "",
                width: 150,
                render: (_: unknown, s: SequenceRow) => (
                  <span className="space-x-2">
                    <Button size="small" theme="borderless" onClick={() => openSeqEdit(s)}>
                      编辑
                    </Button>
                    <Button size="small" theme="borderless" type="danger" onClick={() => delSeq(s)}>
                      删除
                    </Button>
                  </span>
                ),
              },
            ]}
          />
        </div>
      </section>

      {/* ===== 区二：触达活动 ===== */}
      <section className="mt-8">
        <SectionHead
          title="触达活动"
          desc="活动=序列 × 发信账户 × 目标线索筛选；发送走每日 09:30/14:30 定时调度，也可手动立即调度。"
        >
          {dispTaskId ? (
            <Button type="danger" theme="outline" onClick={cancelDispatch}>
              取消调度
            </Button>
          ) : (
            <Button
              theme="solid"
              icon={<Zap size={14} />}
              loading={dispStarting}
              onClick={dispatchNow}
            >
              立即调度
            </Button>
          )}
          <Button
            icon={<Plus size={14} />}
            disabled={sequences.length === 0 || accounts.length === 0}
            onClick={() => {
              setCampName("");
              setCampSeqId(null);
              setCampAccId(null);
              setCampLimit(50);
              setCampDrawer(true);
            }}
          >
            新建活动
          </Button>
        </SectionHead>

        {sequences.length === 0 && (
          <div className="mb-3 rounded-lg border border-[var(--border)] px-4 py-2.5 text-[12.5px] text-[var(--muted)]">
            先在上方「邮件序列」建一条序列，并在「发信账户」接入邮箱后即可建活动。
          </div>
        )}

        {/* 调度任务实时进度（SSE） */}
        {(dispTaskId || dispLogs.length > 0) && (
          <div className="mb-3 rounded-xl border border-[var(--border)] bg-white px-4 py-3">
            <div className="flex items-center justify-between">
              <span className="text-[13px] font-medium text-[var(--ink)]">
                触达调度{dispTaskId ? "进行中" : " · 最近一次"}
              </span>
              {!dispTaskId && (
                <Button size="small" theme="borderless" onClick={() => setDispLogs([])}>
                  收起
                </Button>
              )}
            </div>
            {dispProgress !== null && (
              <Progress percent={dispProgress} showInput aria-label="调度进度" stroke="var(--accent)" className="!mt-2" />
            )}
            <pre className="mt-2 max-h-[150px] overflow-auto whitespace-pre-wrap rounded-lg bg-[#10202a] p-3 font-mono text-[11.5px] leading-5 text-emerald-200">
              {dispLogs.slice(-8).join("\n") || "等待日志…"}
            </pre>
          </div>
        )}

        <div className="rounded-xl border border-[var(--border)] bg-white">
          <Table<CampaignRow>
            size="small"
            dataSource={campaigns}
            rowKey="id"
            pagination={false}
            empty="还没有活动 —— 新建后点「生成发送队列」按目标筛选线索入队"
            columns={[
              { title: "#", dataIndex: "id", width: 64 },
              {
                title: "活动",
                dataIndex: "name",
                render: (v: string) => <span className="font-medium text-[var(--ink)]">{v}</span>,
              },
              {
                title: "关联序列",
                width: 170,
                render: (_: unknown, c: CampaignRow) =>
                  c.sequence_name ?? seqNameById.get(c.sequence_id) ?? (
                    <span className="text-[var(--muted)]">序列 #{c.sequence_id}</span>
                  ),
              },
              {
                title: "发信账户",
                width: 200,
                render: (_: unknown, c: CampaignRow) =>
                  c.account_email ?? accountById.get(c.email_account_id)?.email ?? (
                    <span className="text-[var(--muted)]">—</span>
                  ),
              },
              {
                title: "状态",
                dataIndex: "status",
                width: 96,
                render: (v: string) => {
                  const s = CAMPAIGN_TAG[v] ?? { label: v, color: "grey" as const };
                  return (
                    <Tag color={s.color} type="light">
                      {s.label}
                    </Tag>
                  );
                },
              },
              {
                title: "今日已发/预算",
                width: 120,
                render: (_: unknown, c: CampaignRow) => (
                  <span className="tabular-nums text-[var(--ink)]">
                    {sentOf(c)} / {budgetOf(c)}
                  </span>
                ),
              },
              {
                title: "",
                width: 250,
                render: (_: unknown, c: CampaignRow) => (
                  <span className="space-x-1.5 whitespace-nowrap">
                    {c.status !== "done" && (
                      <Button
                        size="small"
                        theme="borderless"
                        icon={c.status === "running" ? <Pause size={13} /> : <Play size={13} />}
                        onClick={() => flipStatus(c)}
                      >
                        {c.status === "running" ? "暂停" : c.status === "draft" ? "启动" : "恢复"}
                      </Button>
                    )}
                    <Button
                      size="small"
                      theme="borderless"
                      icon={<ListChecks size={13} />}
                      onClick={() => enqueue(c)}
                    >
                      生成发送队列
                    </Button>
                  </span>
                ),
              },
            ]}
          />
        </div>
      </section>

      {/* ===== 区三：发信账户 ===== */}
      <section className="mt-8">
        <SectionHead
          title="发信账户"
          desc="EmailEngine 代理或 SMTP 直连；日发送上限与预热起始日共同决定每日可用预算（warmup 渐进）。"
        >
          <Button icon={<Plus size={14} />} onClick={openAccNew}>
            新建账户
          </Button>
        </SectionHead>
        <div className="rounded-xl border border-[var(--border)] bg-white">
          <Table<EmailAccountRow>
            size="small"
            dataSource={accounts}
            rowKey="id"
            pagination={false}
            empty="还没有发信账户 —— 接入一个邮箱开始触达（后端未就绪时此处为空态）"
            columns={[
              {
                title: "账户",
                dataIndex: "name",
                render: (v: string, a: EmailAccountRow) => (
                  <div>
                    <span className="font-medium text-[var(--ink)]">{v}</span>
                    <span className="ml-2 text-[12px] text-[var(--muted)]">{a.email}</span>
                  </div>
                ),
              },
              {
                title: "接入方式",
                dataIndex: "provider",
                width: 130,
                render: (v: string) => {
                  const s = ACCOUNT_TAG[v] ?? { label: v, color: "grey" as const };
                  return (
                    <Tag color={s.color} type="light">
                      {s.label}
                    </Tag>
                  );
                },
              },
              {
                title: "日上限",
                dataIndex: "daily_cap",
                width: 80,
                render: (v: number) => <span className="tabular-nums">{v ?? 20}</span>,
              },
              {
                title: "预热起始",
                dataIndex: "warmup_started_at",
                width: 120,
                render: (v: string | null) => (v ? String(v).slice(0, 10) : "—"),
              },
              {
                title: "健康",
                dataIndex: "health",
                width: 90,
                render: (v: string | undefined) => {
                  if (!v) return <span className="text-[var(--muted)]">—</span>;
                  const s = HEALTH_TAG[v] ?? { label: v, color: "amber" as const };
                  return (
                    <Tag color={s.color} type="light">
                      {s.label}
                    </Tag>
                  );
                },
              },
            ]}
          />
        </div>
      </section>

      {/* ===== 抽屉：序列编辑 ===== */}
      <SideSheet
        title={seqEditing ? `编辑序列 · ${seqEditing.name}` : "新建序列"}
        visible={seqDrawer}
        onCancel={() => setSeqDrawer(false)}
        width={640}
        footer={null}
      >
        <div className="space-y-4 pb-8">
          <div>
            <div className="mb-1.5 text-[13px] font-medium text-slate-600">序列名称</div>
            <Input
              placeholder="如：中标企业首触序列"
              value={seqName}
              onChange={setSeqName}
            />
          </div>

          <div>
            <div className="mb-1.5 text-[13px] font-medium text-slate-600">
              步骤（按顺序发送；首步当天，后续步为距上一步的天数）
            </div>
            <div className="space-y-3">
              {steps.map((st, idx) => (
                <div key={st._key} className="rounded-xl border border-[var(--border)] bg-white p-3.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[12.5px] font-medium text-[var(--ink)]">第 {idx + 1} 封</span>
                    <span className="flex items-center">
                      <Button
                        size="small"
                        theme="borderless"
                        icon={<ArrowUp size={13} />}
                        disabled={idx === 0}
                        onClick={() => moveStep(idx, -1)}
                      />
                      <Button
                        size="small"
                        theme="borderless"
                        icon={<ArrowDown size={13} />}
                        disabled={idx === steps.length - 1}
                        onClick={() => moveStep(idx, 1)}
                      />
                      <Button
                        size="small"
                        theme="borderless"
                        type="danger"
                        icon={<Trash2 size={13} />}
                        disabled={steps.length <= 1}
                        onClick={() => removeStep(idx)}
                      />
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <span className="shrink-0 text-[12.5px] text-[var(--muted)]">间隔</span>
                    <InputNumber
                      value={st.delay_days}
                      min={0}
                      max={90}
                      style={{ width: 76 }}
                      onChange={(v) => patchStep(idx, { delay_days: typeof v === "number" ? v : 0 })}
                    />
                    <span className="shrink-0 text-[12.5px] text-[var(--muted)]">天后发</span>
                    <Input
                      className="flex-1"
                      placeholder="邮件主题，如 {{lead.company}} 采购设备对接"
                      value={st.subject}
                      onChange={(v) => patchStep(idx, { subject: v })}
                    />
                  </div>
                  <textarea
                    rows={4}
                    value={st.body}
                    placeholder="邮件正文…可先点此框，再点下方变量插入"
                    onFocus={(e) => {
                      bodyFocusRef.current = { key: st._key, el: e.currentTarget };
                    }}
                    onChange={(e) => patchStep(idx, { body: e.target.value })}
                    className={`${flatTextareaCls} mt-2`}
                  />
                </div>
              ))}
            </div>
            <Button size="small" className="!mt-3" icon={<Plus size={13} />} onClick={addStep}>
              加一步
            </Button>
          </div>

          <div>
            <div className="mb-1.5 text-[12.5px] text-[var(--muted)]">
              点击变量插入到最近聚焦的正文框光标处：
            </div>
            <div className="flex flex-wrap gap-1.5">
              {VARIABLES.map((v) => (
                <button
                  key={v}
                  title={VARIABLE_HINTS[v]}
                  onClick={() => insertVar(v)}
                  className="cursor-pointer rounded border border-[var(--border)] bg-[var(--raised)] px-2 py-0.5 font-mono text-[11.5px] text-[var(--accent)] hover:border-[var(--accent)]"
                >
                  {v}
                </button>
              ))}
            </div>
            <div className="mt-2 text-[11.5px] leading-5 text-[var(--muted)]">
              每封信自动附带退订链接（{"{{unsubscribe_url}}"}）；退订名单内的联系人在入队与发送前都会被排除（合规红线）。
            </div>
          </div>

          <div className="flex gap-2 pt-1">
            <Button theme="solid" loading={seqSaving} onClick={saveSeq}>
              保存序列
            </Button>
            <Button onClick={() => setSeqDrawer(false)}>取消</Button>
          </div>
        </div>
      </SideSheet>

      {/* ===== 抽屉：新建活动 ===== */}
      <SideSheet
        title="新建触达活动"
        visible={campDrawer}
        onCancel={() => setCampDrawer(false)}
        width={480}
        footer={null}
      >
        <div className="space-y-4 pb-8">
          <div>
            <div className="mb-1.5 text-[13px] font-medium text-slate-600">活动名称</div>
            <Input placeholder="如：苏州真空设备 Q4 首触" value={campName} onChange={setCampName} />
          </div>
          <div>
            <div className="mb-1.5 text-[13px] font-medium text-slate-600">关联序列</div>
            <Select
              placeholder="选择邮件序列"
              value={campSeqId ?? undefined}
              onChange={(v) => setCampSeqId(Number(v))}
              filter
              className="!w-full"
            >
              {sequences.map((s) => (
                <Select.Option key={s.id} value={s.id}>
                  {s.name}（{s.steps?.length ?? 0} 步）
                </Select.Option>
              ))}
            </Select>
          </div>
          <div>
            <div className="mb-1.5 text-[13px] font-medium text-slate-600">发信账户</div>
            <Select
              placeholder="选择发信邮箱"
              value={campAccId ?? undefined}
              onChange={(v) => setCampAccId(Number(v))}
              filter
              className="!w-full"
            >
              {accounts.map((a) => (
                <Select.Option key={a.id} value={a.id}>
                  {a.email}（{ACCOUNT_TAG[a.provider]?.label ?? a.provider}）
                </Select.Option>
              ))}
            </Select>
          </div>
          <div>
            <div className="mb-1.5 text-[13px] font-medium text-slate-600">每日发送上限</div>
            <InputNumber
              value={campLimit}
              min={1}
              max={500}
              style={{ width: 120 }}
              onChange={(v) => setCampLimit(typeof v === "number" ? v : 50)}
            />
            <div className="mt-1.5 text-[11.5px] leading-5 text-[var(--muted)]">
              实际预算 = min(账户日上限，活动限额，warmup 渐进额度)。
            </div>
          </div>
          <Button theme="solid" loading={campSaving} onClick={saveCampaign}>
            创建活动
          </Button>
        </div>
      </SideSheet>

      {/* ===== 抽屉：新建发信账户 ===== */}
      <SideSheet
        title="新建发信账户"
        visible={accDrawer}
        onCancel={() => setAccDrawer(false)}
        width={480}
        footer={null}
      >
        <div className="space-y-4 pb-8">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-slate-600">账户名</div>
              <Input
                placeholder="如：销售主邮箱"
                value={accForm.name}
                onChange={(v) => setAccForm((f) => ({ ...f, name: v }))}
              />
            </div>
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-slate-600">接入方式</div>
              <Select
                value={accForm.provider}
                onChange={(v) => setAccForm((f) => ({ ...f, provider: String(v) }))}
                className="!w-full"
                optionList={[
                  { value: "emailengine", label: "EmailEngine 代理" },
                  { value: "smtp", label: "SMTP 直连" },
                ]}
              />
            </div>
          </div>
          <div>
            <div className="mb-1.5 text-[13px] font-medium text-slate-600">发信邮箱</div>
            <Input
              placeholder="sender@company.com"
              value={accForm.email}
              onChange={(v) => setAccForm((f) => ({ ...f, email: v }))}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-slate-600">SMTP 主机</div>
              <Input
                placeholder={accForm.provider === "smtp" ? "smtp.exmail.com" : "可留空"}
                value={accForm.smtp_host}
                onChange={(v) => setAccForm((f) => ({ ...f, smtp_host: v }))}
              />
            </div>
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-slate-600">端口</div>
              <InputNumber
                value={accForm.smtp_port}
                min={1}
                max={65535}
                style={{ width: "100%" }}
                onChange={(v) => setAccForm((f) => ({ ...f, smtp_port: typeof v === "number" ? v : 465 }))}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-slate-600">SMTP 用户</div>
              <Input
                placeholder={accForm.provider === "smtp" ? "同邮箱地址" : "可留空"}
                value={accForm.smtp_user}
                onChange={(v) => setAccForm((f) => ({ ...f, smtp_user: v }))}
              />
            </div>
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-slate-600">SMTP 密码 / 授权码</div>
              <Input
                mode="password"
                placeholder={accForm.provider === "smtp" ? "必填" : "可留空"}
                value={accForm.smtp_pass}
                onChange={(v) => setAccForm((f) => ({ ...f, smtp_pass: v }))}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-slate-600">日发送上限</div>
              <InputNumber
                value={accForm.daily_cap}
                min={1}
                max={2000}
                style={{ width: "100%" }}
                onChange={(v) => setAccForm((f) => ({ ...f, daily_cap: typeof v === "number" ? v : 20 }))}
              />
            </div>
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-slate-600">预热起始日</div>
              <input
                type="date"
                value={accForm.warmup_started_at}
                onChange={(e) => setAccForm((f) => ({ ...f, warmup_started_at: e.target.value }))}
                className="h-8 w-full cursor-pointer rounded border border-[var(--border)] bg-white px-2 text-[12.5px] text-[var(--ink)] outline-none focus:border-[var(--accent)]"
              />
            </div>
          </div>
          <div className="text-[11.5px] leading-5 text-[var(--muted)]">
            预热期从起始日起按天渐进放量，保护新邮箱信誉；未填则按完整预算发送。
          </div>
          <Button theme="solid" loading={accSaving} onClick={saveAccount}>
            创建账户
          </Button>
        </div>
      </SideSheet>
    </AppShell>
  );
}
