"use client";

/**
 * 通知中心（SPEC-CC CC1）：站内通知的历史/筛选/批量已读——
 * 铃铛下拉只放最近 8 条，全量收口在这里。后端端点全部复用既有
 * （list 分页/未读筛选/单条已读/全部已读），本页零新端点。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Tag, Toast } from "@douyinfe/semi-ui";
import { Bell } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface NoticeRow {
  id: number;
  kind: string;
  title: string;
  body: string;
  ref_type: string;
  ref_id: number;
  read: number;
  created_at: string;
}

const KIND_META: Record<string, { label: string; color: "cyan" | "blue" | "grey" | "red" | "green" }> = {
  signal: { label: "信号", color: "cyan" },
  report: { label: "周报", color: "blue" },
  system: { label: "系统", color: "grey" },
  watchdog: { label: "看门狗", color: "red" },
};

function refRoute(n: NoticeRow): string | null {
  if (n.ref_type === "competitor_win") return "/competitors";
  if (n.ref_type === "report") return "/reports";
  if (n.ref_type === "watchdog") return null;
  if (["signal", "sub_signal", "owner_signal", "proposal"].includes(n.ref_type)) {
    return "/signals";
  }
  return null;
}

export default function NotificationsPage() {
  const [items, setItems] = useState<NoticeRow[]>([]);
  const [unread, setUnread] = useState(0);
  const [onlyUnread, setOnlyUnread] = useState(0);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 20;

  const load = useCallback(() => {
    const p = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (onlyUnread) p.set("unread", "1");
    api
      .get<{ unread_count: number; items: NoticeRow[]; total?: number }>(`/api/notifications?${p}`)
      .then((d) => {
        setItems(d.items);
        setUnread(d.unread_count);
        setTotal(d.total ?? d.items.length);
      })
      .catch(() => undefined);
  }, [page, onlyUnread]);
  useEffect(load, [load]);

  const openOne = async (n: NoticeRow) => {
    try {
      if (!n.read) await api.post(`/api/notifications/${n.id}/read`, {});
    } catch {
      /* 已读失败不阻断跳转 */
    }
    const route = refRoute(n);
    if (route) window.location.href = route;
    else load();
  };

  const readAll = async () => {
    try {
      await api.post("/api/notifications/read-all", {});
      Toast.success("已全部标为已读");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  return (
    <AppShell>
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-[15px] font-medium text-[var(--ink)]">
          <Bell size={16} /> 通知中心
        </span>
        <Button
          size="small"
          theme={onlyUnread ? "solid" : "borderless"}
          onClick={() => { setOnlyUnread(onlyUnread ? 0 : 1); setPage(1); }}
        >
          仅未读
        </Button>
        <span className="text-[12.5px] text-[var(--muted)]">未读 {unread}</span>
        <Button size="small" className="ml-auto" onClick={readAll} disabled={unread === 0}>
          全部已读
        </Button>
      </div>

      <div className="mt-3 space-y-2">
        {items.map((n) => {
          const meta = KIND_META[n.kind] ?? { label: n.kind, color: "grey" as const };
          return (
            <button
              key={n.id}
              onClick={() => void openOne(n)}
              className={`block w-full cursor-pointer rounded-lg border px-4 py-3 text-left transition-colors hover:border-[var(--accent)] ${
                n.read ? "border-[var(--border)] bg-white" : "border-[var(--border)] bg-white"
              }`}
            >
              <div className="flex items-start gap-3">
                {!n.read && <span className="mt-1.5 h-8 w-0.5 shrink-0 rounded bg-[var(--accent)]" />}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Tag color={meta.color} type="light" size="small">{meta.label}</Tag>
                    <span className={`truncate text-[13.5px] ${n.read ? "text-[var(--ink)]" : "font-medium text-[var(--ink)]"}`}>
                      {n.title}
                    </span>
                    <span className="ml-auto shrink-0 text-[11.5px] tabular-nums text-[var(--muted)]">
                      {(n.created_at || "").replace("T", " ").slice(0, 16)}
                    </span>
                  </div>
                  {n.body && (
                    <div className="mt-0.5 truncate text-[12.5px] text-[var(--muted)]" title={n.body}>
                      {n.body}
                    </div>
                  )}
                </div>
              </div>
            </button>
          );
        })}
        {items.length === 0 && (
          <div className="py-16 text-center text-[13px] text-[var(--muted)]">
            {onlyUnread ? "没有未读通知" : "暂无通知 —— 订阅/雷达命中后会出现在这里"}
          </div>
        )}
      </div>

      {total > pageSize && (
        <div className="mt-4 flex items-center justify-center gap-3">
          <Button size="small" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button>
          <span className="text-[12.5px] text-[var(--muted)]">第 {page} 页</span>
          <Button size="small" disabled={items.length < pageSize} onClick={() => setPage(page + 1)}>下一页</Button>
        </div>
      )}
    </AppShell>
  );
}
