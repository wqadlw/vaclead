"use client";

/**
 * 机会雷达（SPEC-INFO v2 一期）：采购时机分排序——
 * 多信号加权 × 30 天线性衰减，"本周最该打的 20 家"。
 * 时机分 = Σ(类型权重 × (1-距今/30))，tender_win 30/proposal 25/
 * web_change 20/新号码 15/tender 10。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Select, Tag, Toast } from "@douyinfe/semi-ui";
import { Flame, RefreshCw } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface OppRow {
  ent_id: number;
  name: string;
  industry_name: string | null;
  province: string | null;
  score: number;
  kinds: string[];
  last_at: string;
  /** SPEC-INFO v2：已有活跃线索 = 已在跟进（沉底不消失） */
  followed?: boolean;
}

const KIND_CN: Record<string, string> = {
  tender_win: "中标",
  proposal: "备案",
  web_change: "官网变更",
  tender: "公告",
  tender_buy: "采购",
};

export default function IntelOppPage() {
  const [items, setItems] = useState<OppRow[]>([]);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  // SPEC-INFO v2：只看未跟进开关（已在跟的沉底不消失，新机会优先）
  const [onlyNew, setOnlyNew] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api.get<{ items: OppRow[] }>(`/api/intel/opp?days=${days}&top=20`)
      .then((d) => setItems(d.items))
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [days]);
  useEffect(load, [load]);

  const toLead = async (entId: number) => {
    try {
      await api.post(`/api/enterprises/${entId}/lead`, {});
      Toast.success("已转线索，去线索看板跟进");
      // SPEC-INFO v2：转完即标已跟进（本地即时，沉底不占新机会位）
      setItems((prev) =>
        prev.map((it) => (it.ent_id === entId ? { ...it, followed: true } : it)),
      );
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "转线索失败（可能已是线索）");
    }
  };

  return (
    <AppShell>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-[20px] font-semibold text-[var(--ink)]">机会雷达</h1>
          <p className="mt-0.5 text-[12.5px] text-[var(--muted)]">
            采购时机分 = 近 {days} 天多信号加权（中标 30/备案 25/官网变更 20/新号码 15）× 时间衰减——
            分越高，越可能在买泵
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={String(days)} onChange={(v) => setDays(Number(v))} className="!w-28 shrink-0">
            <Select.Option value="7">近 7 天</Select.Option>
            <Select.Option value="30">近 30 天</Select.Option>
            <Select.Option value="90">近 90 天</Select.Option>
          </Select>
          <Button size="small" icon={<RefreshCw size={13} />} onClick={load}>刷新</Button>
        </div>
      </div>

      <div className="mt-4 space-y-2.5">
        {/* SPEC-INFO v2：只看未跟进开关——已转线索的沉底，新机会优先 */}
        <label className="flex cursor-pointer items-center gap-1.5 text-[12px] text-[var(--muted)]">
          <input
            type="checkbox"
            checked={onlyNew}
            onChange={(e) => setOnlyNew(e.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--accent)]"
          />
          只看未跟进（隐藏已转线索的）
        </label>
        {items
          .filter((r) => !(onlyNew && r.followed))
          .map((r, idx) => (
          <div key={r.ent_id} className="rounded-lg border border-[var(--border)] bg-white px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[13px] font-bold ${idx < 3 ? "bg-[#c2801b] text-white" : "bg-[var(--raised)] text-[var(--muted)]"}`}>
                  {idx + 1}
                </span>
                <a href={`/enterprises/view?id=${r.ent_id}`} className="min-w-0 truncate text-[14.5px] font-medium text-[var(--accent)] hover:underline">
                  {r.name}
                </a>
                {r.industry_name && <Tag color="cyan" type="light" size="small">{r.industry_name}</Tag>}
                {r.followed && <Tag color="green" type="light" size="small">已跟进</Tag>}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Flame size={16} className="text-[#c2801b]" />
                <span className="text-[20px] font-bold tabular-nums text-[#c2801b]">{r.score}</span>
                <span className="text-[11.5px] text-[var(--muted)]">时机分</span>
                {!r.followed && (
                  <Button size="small" theme="outline" onClick={() => void toLead(r.ent_id)}>转线索</Button>
                )}
              </div>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-[var(--muted)]">
              <span>信号组合：</span>
              {r.kinds.map((k) => (
                <Tag key={k} color="amber" type="light" size="small">{KIND_CN[k] ?? k}</Tag>
              ))}
              {r.province && <span>· {r.province}</span>}
              <span>· 最近信号 {r.last_at.slice(0, 10)}</span>
            </div>
          </div>
        ))}
        {!loading && items.length === 0 && (
          <div className="py-16 text-center text-[13px] text-[var(--muted)]">
            时间窗内暂无多信号企业 —— 等每日采集入库后回来刷新
          </div>
        )}
      </div>
    </AppShell>
  );
}
