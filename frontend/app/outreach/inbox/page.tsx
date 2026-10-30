"use client";

/**
 * 触达收件箱（SPEC-J J4）：GET /api/outreach/inbox 按 thread_id 分组的会话视图。
 * 左：会话列表（企业/主题/最后活动/replied 绿徽章，可按状态筛选，已回复优先）；
 * 右：sent/replied 时间线 + 回信正文 + 「去跟进」跳线索看板。
 * 静态导出：纯 client 页面，无查询参数；后端未就绪时空态 + 重试兜底。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Select, Skeleton, Tag, Timeline, Toast } from "@douyinfe/semi-ui";
import { Inbox, RefreshCw } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

// ---------- 契约形状 + 兜底字段（后端字段名允许小幅漂移） ----------
interface RawMsg {
  id?: number;
  step_idx?: number;
  direction?: string;
  status?: string;
  subject?: string | null;
  body?: string | null;
  sent_at?: string | null;
  created_at?: string | null;
  at?: string | null;
  reply_at?: string | null;
}
interface RawThread {
  thread_id?: string | number;
  id?: number;
  lead_id?: number | null;
  enterprise_name?: string | null;
  lead_name?: string | null;
  contact_name?: string | null;
  subject?: string | null;
  status?: string | null;
  replied?: boolean | number;
  last_activity_at?: string | null;
  last_at?: string | null;
  reply_body?: string | null;
  messages?: RawMsg[];
}
interface TimelineMsg {
  id?: number;
  direction: "sent" | "reply";
  at: string | null;
  subject: string;
  body: string;
  step: number;
}
interface ThreadView {
  key: string;
  leadId: number | null;
  company: string;
  contact: string | null;
  subject: string;
  lastAt: string | null;
  replied: boolean;
  messages: TimelineMsg[];
}

function fmtTime(v: string | null | undefined): string {
  return v ? String(v).replace("T", " ").slice(0, 16) : "—";
}

function normalizeThread(raw: RawThread, fallbackIdx: number): ThreadView {
  const rawMsgs = Array.isArray(raw.messages) ? raw.messages : [];
  const messages: TimelineMsg[] = rawMsgs.map((m, i) => ({
    id: m.id,
    direction: m.direction === "reply" || m.status === "replied" ? "reply" : "sent",
    at: m.sent_at ?? m.created_at ?? m.at ?? m.reply_at ?? null,
    subject: m.subject ?? "",
    body: m.body ?? "",
    step: typeof m.step_idx === "number" ? m.step_idx + 1 : i + 1,
  }));
  const replied =
    raw.replied === true ||
    raw.replied === 1 ||
    raw.status === "replied" ||
    raw.status === "reply" ||
    messages.some((m) => m.direction === "reply");
  // 后端只给汇总（无 messages 数组）时的最小时间线兜底
  if (messages.length === 0) {
    if (raw.subject) {
      messages.push({
        direction: "sent",
        at: raw.last_activity_at ?? raw.last_at ?? null,
        subject: raw.subject,
        body: "",
        step: 1,
      });
    }
    if (raw.reply_body) {
      messages.push({
        direction: "reply",
        at: raw.last_activity_at ?? raw.last_at ?? null,
        subject: "客户回信",
        body: raw.reply_body,
        step: 0,
      });
    }
  }
  const lastAt =
    raw.last_activity_at ?? raw.last_at ?? messages.reduce<string | null>((a, m) => m.at ?? a, null);
  return {
    key: String(raw.thread_id ?? raw.id ?? `t${fallbackIdx}`),
    leadId: raw.lead_id ?? null,
    company: raw.enterprise_name ?? raw.lead_name ?? "未知企业",
    contact: raw.contact_name ?? null,
    subject: raw.subject ?? messages[0]?.subject ?? "（无主题）",
    lastAt,
    replied,
    messages,
  };
}

type FilterKey = "all" | "replied" | "pending";

export default function OutreachInboxPage() {
  const router = useRouter();
  const [threads, setThreads] = useState<ThreadView[]>([]);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api
      .get<unknown>("/api/outreach/inbox")
      .then((d) => {
        const items = Array.isArray(d)
          ? (d as RawThread[])
          : Array.isArray((d as { items?: RawThread[] }).items)
            ? ((d as { items: RawThread[] }).items)
            : [];
        setThreads(items.map((t, i) => normalizeThread(t, i)));
        setFailed(false);
      })
      .catch(() => {
        setFailed(true);
        Toast.error("收件箱加载失败 —— 触达服务可能尚未就绪");
      })
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);

  // 已回复优先（契约），同级按最后活动倒序
  const visible = useMemo(() => {
    const list = threads.filter((t) =>
      filter === "all" ? true : filter === "replied" ? t.replied : !t.replied,
    );
    return [...list].sort((a, b) =>
      a.replied === b.replied
        ? String(b.lastAt ?? "").localeCompare(String(a.lastAt ?? ""))
        : a.replied
          ? -1
          : 1,
    );
  }, [threads, filter]);

  const selected = useMemo(
    () => threads.find((t) => t.key === selectedKey) ?? null,
    [threads, selectedKey],
  );
  // 首次加载自动选中第一条（已回复优先的列表头）
  useEffect(() => {
    if (!selectedKey && visible.length > 0) setSelectedKey(visible[0].key);
  }, [visible, selectedKey]);

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="text-[13px] leading-5 text-[var(--muted)]">
          触达邮件按会话聚合：客户回信自动匹配到原线程（In-Reply-To + 主题回退），自动回复不计入。
        </div>
        <div className="flex items-center gap-2">
          <Select<FilterKey>
            value={filter}
            onChange={(v) => setFilter(String(v ?? "all") as FilterKey)}
            className="!w-32"
            optionList={[
              { value: "all", label: "全部会话" },
              { value: "replied", label: "已回复" },
              { value: "pending", label: "未回复" },
            ]}
          />
          <Button icon={<RefreshCw size={14} />} loading={loading} onClick={load}>
            刷新
          </Button>
        </div>
      </div>

      <div className="flex h-[calc(100vh-190px)] min-h-[460px] overflow-hidden rounded-xl border border-[var(--border)] bg-white">
        {/* 左：会话列表 */}
        <div className="w-[330px] shrink-0 overflow-y-auto border-r border-[var(--border)]">
          {loading && threads.length === 0 ? (
            <div className="p-4">
              <Skeleton placeholder={<Skeleton.Paragraph rows={5} />} loading active />
            </div>
          ) : visible.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center px-6 py-16 text-center">
              <Inbox size={34} strokeWidth={1.5} className="text-[var(--control)]" />
              <div className="mt-3 text-[13.5px] font-medium text-[var(--ink)]">暂无触达会话</div>
              <div className="mt-1 max-w-[240px] text-[12px] leading-5 text-[var(--muted)]">
                {failed
                  ? "触达服务暂不可用，可点右上角「刷新」重试"
                  : "活动发出首封邮件后，往来会话会按线程聚合在这里"}
              </div>
              {failed && (
                <Button size="small" className="!mt-3" onClick={load}>
                  重试
                </Button>
              )}
            </div>
          ) : (
            visible.map((t) => (
              <button
                key={t.key}
                onClick={() => setSelectedKey(t.key)}
                className={`block w-full cursor-pointer border-b border-[var(--rule)] px-4 py-3 text-left transition-colors ${
                  selectedKey === t.key
                    ? "bg-[rgba(27,108,128,0.06)]"
                    : "hover:bg-[rgba(19,20,22,0.03)]"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] font-medium text-[var(--ink)]">
                    {t.company}
                  </span>
                  {t.replied && (
                    <Tag color="green" type="light" size="small">
                      已回复
                    </Tag>
                  )}
                </div>
                <div className="mt-0.5 truncate text-[12px] text-[var(--muted)]">
                  {t.contact ? `${t.contact} · ` : ""}
                  {t.subject}
                </div>
                <div className="mt-1 text-[11px] text-[var(--muted)]">{fmtTime(t.lastAt)}</div>
              </button>
            ))
          )}
        </div>

        {/* 右：会话详情 */}
        <div className="flex-1 overflow-y-auto">
          {!selected ? (
            <div className="flex h-full items-center justify-center text-[13px] text-[var(--muted)]">
              {threads.length === 0 ? "" : "从左侧选择一个会话查看往来邮件"}
            </div>
          ) : (
            <div className="p-5">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--rule)] pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[15px] font-semibold text-[var(--ink)]">
                      {selected.company}
                    </span>
                    {selected.replied && (
                      <Tag color="green" type="light">
                        已回复
                      </Tag>
                    )}
                  </div>
                  <div className="mt-0.5 text-[12.5px] text-[var(--muted)]">
                    {selected.contact ?? "未知联系人"}
                    {selected.leadId !== null && (
                      <span className="ml-2">线索 #{selected.leadId}</span>
                    )}
                  </div>
                  <div className="mt-1 text-[13px] text-[var(--ink)]">{selected.subject}</div>
                </div>
                <Button
                  theme="solid"
                  size="small"
                  onClick={() => router.push("/leads")}
                  disabled={selected.leadId === null}
                  title={selected.leadId === null ? "该会话未关联线索" : undefined}
                >
                  去跟进
                </Button>
              </div>

              {selected.messages.length === 0 ? (
                <div className="py-10 text-center text-[13px] text-[var(--muted)]">
                  暂无邮件明细
                </div>
              ) : (
                <Timeline className="!mt-4">
                  {selected.messages.map((m, i) => (
                    <Timeline.Item
                      key={m.id ?? `${selected.key}-${i}`}
                      time={fmtTime(m.at)}
                      type={m.direction === "reply" ? "success" : undefined}
                    >
                      <div className="text-[13px] font-medium text-[var(--ink)]">
                        {m.direction === "reply" ? "客户回信" : `我方发送 · 第 ${m.step} 步`}
                      </div>
                      {m.subject && (
                        <div className="text-[12.5px] text-[var(--ink)]">{m.subject}</div>
                      )}
                      {m.body && (
                        <div className="mt-1 whitespace-pre-wrap rounded-lg border border-[var(--rule)] bg-[var(--raised)] px-3.5 py-2.5 text-[12.5px] leading-5 text-[var(--muted)]">
                          {m.body}
                        </div>
                      )}
                    </Timeline.Item>
                  ))}
                </Timeline>
              )}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
