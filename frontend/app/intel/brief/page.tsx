"use client";

/**
 * 情报晨报（SPEC-INFO v2 一期）：按日分组的获客情报流——
 * 每条带获客判断语（确定性规则）+ 一键转线索/进详情。
 * 信息价值=时机：今天打开=今天的情报。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Select, Tag, Toast } from "@douyinfe/semi-ui";
import { RefreshCw } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface BriefItem {
  signal_id: number;
  kind: string;
  kind_cn: string;
  icon: string;
  why: string;
  title: string;
  ent_id: number;
  ent_name: string;
  industry_name: string | null;
  province: string | null;
  at: string;
  evidence: string;
  url: string | null;
  seen?: boolean;
}

const KIND_TAG: Record<string, { color: "cyan" | "green" | "blue" | "amber" }> = {
  tender_win: { color: "cyan" },
  proposal: { color: "amber" },
  web_change: { color: "blue" },
  tender: { color: "blue" },
  tender_buy: { color: "green" },
};

export default function IntelBriefPage() {
  const [days, setDays] = useState(7);
  const [kind, setKind] = useState("");
  const [items, setItems] = useState<BriefItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  // SPEC-INFO v2：已处理标记（晨报闭环——看完标记，不重复刷屏）
  const [hideSeen, setHideSeen] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    const p = new URLSearchParams({ days: String(days), page_size: "100" });
    if (kind) p.set("kind", kind);
    api.get<{ items: BriefItem[]; total: number; seen?: number[] }>(`/api/intel/brief?${p}`)
      .then((d) => {
        // SPEC-INFO v2：seen 集标注到条目（后端 seen 列表 → 前端本地映射）
        const seenSet = new Set(d.seen ?? []);
        setItems(d.items.map((it) => ({ ...it, seen: seenSet.has(it.signal_id) })));
        setTotal(d.total);
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [days, kind]);
  useEffect(load, [load]);

  // SPEC-INFO v2：标记已处理（本地即时灰显 + 后端持久，不整页重拉）
  const markSeen = (sid: number) => {
    setItems((prev) => prev.map((it) => (it.signal_id === sid ? { ...it, seen: true } : it)));
    api.post("/api/intel/seen", { signal_ids: [sid] }).catch(() => undefined);
  };

  const toLead = async (entId: number) => {
    try {
      await api.post(`/api/enterprises/${entId}/lead`, {});
      Toast.success("已转线索，去线索看板跟进");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "转线索失败（可能已是线索）");
    }
  };

  return (
    <AppShell>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-[20px] font-semibold text-[var(--ink)]">情报晨报</h1>
          <p className="mt-0.5 text-[12.5px] text-[var(--muted)]">
            每条情报都带"为什么值得打"的判断——看到的不是公告，是时机
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={String(days)} onChange={(v) => setDays(Number(v))} className="!w-28 shrink-0">
            <Select.Option value="1">今天</Select.Option>
            <Select.Option value="7">近 7 天</Select.Option>
            <Select.Option value="30">近 30 天</Select.Option>
          </Select>
          <Select value={kind || undefined} placeholder="全部类型" showClear onChange={(v) => setKind(String(v ?? ""))} className="!w-32 shrink-0">
            <Select.Option value="tender_win">新中标</Select.Option>
            <Select.Option value="proposal">新备案</Select.Option>
            <Select.Option value="web_change">官网变更</Select.Option>
            <Select.Option value="tender">采购公告</Select.Option>
            <Select.Option value="tender_buy">公开采购</Select.Option>
          </Select>
          <Button size="small" icon={<RefreshCw size={13} />} onClick={load}>刷新</Button>
        </div>
      </div>

      <div className="mt-4 space-y-2.5">
        {/* SPEC-INFO v2：隐藏已处理开关（晨报闭环——处理过的不再刷屏） */}
        {items.some((it) => it.seen) && (
          <label className="flex cursor-pointer items-center gap-1.5 text-[12px] text-[var(--muted)]">
            <input
              type="checkbox"
              checked={hideSeen}
              onChange={(e) => setHideSeen(e.target.checked)}
              className="h-3.5 w-3.5 accent-[var(--accent)]"
            />
            隐藏已处理
          </label>
        )}
        {items
          .filter((it) => !(hideSeen && it.seen))
          .map((it) => (
          <div
            key={it.signal_id}
            className={`rounded-lg border bg-white px-4 py-3 ${
              it.seen ? "border-[var(--rule)] opacity-60" : "border-[var(--border)]"
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[15px]">{it.icon}</span>
              <Tag color={(KIND_TAG[it.kind] ?? { color: "blue" }).color} type="light" size="small">
                {it.kind_cn}
              </Tag>
              <a href={`/enterprises/view?id=${it.ent_id}`} className="truncate text-[14px] font-medium text-[var(--accent)] hover:underline">
                {it.ent_name}
              </a>
              {it.seen && <Tag color="grey" type="light" size="small">已处理</Tag>}
              <span className="text-[11.5px] tabular-nums text-[var(--muted)]">{it.at}</span>
              <span className="ml-auto flex shrink-0 items-center gap-1.5">
                {!it.seen && (
                  <Button size="small" theme="borderless" onClick={() => markSeen(it.signal_id)}>
                    ✓ 已处理
                  </Button>
                )}
                <Button size="small" theme="light" onClick={() => void toLead(it.ent_id)}>转线索</Button>
                <Button
                  size="small"
                  theme="borderless"
                  onClick={() => { window.location.href = `/contacts?q=${encodeURIComponent(it.ent_name)}`; }}
                >
                  打电话
                </Button>
              </span>
            </div>
            <div className="mt-1.5 text-[13px] leading-5 text-[var(--ink)]">{it.title}</div>
            <div className="mt-1 border-l-2 border-[var(--accent)] pl-2.5 text-[12px] leading-4 text-[var(--muted)]">
              <span className="font-medium text-[var(--accent)]">为什么值得打：</span>
              {it.why}
              {it.evidence ? ` · ${it.evidence}` : ""}
            </div>
            <div className="mt-1 flex items-center gap-2 text-[11.5px] text-[var(--muted)]">
              {it.industry_name && <span>{it.industry_name}</span>}
              {it.province && <span>· {it.province}</span>}
              {it.url && (
                <a href={it.url} target="_blank" rel="noreferrer" className="text-[var(--accent)] hover:underline">
                  原文 ↗
                </a>
              )}
            </div>
          </div>
        ))}
        {!loading && items.filter((it) => !(hideSeen && it.seen)).length === 0 && (
          <div className="py-16 text-center text-[13px] text-[var(--muted)]">
            时间窗内暂无情报 —— 放宽时间窗或等每日采集
          </div>
        )}
        {total > items.length && (
          <div className="py-3 text-center text-[12px] text-[var(--muted)]">
            已显示最新 {items.length} 条 / 共 {total} 条情报（时间窗内）
          </div>
        )}
      </div>
    </AppShell>
  );
}
