"use client";

/**
 * 区域市场（SPEC-INFO v2 二期）：地市渗透/竞对压力/备案机会——
 * 按数据事实动态列地市（不硬编码区域），"未定位"= 区划代码脏值/无 city。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Select, Tag } from "@douyinfe/semi-ui";
import { RefreshCw } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface MarketRow {
  city: string;
  market: number;
  dialable: number;
  comp_wins: number;
  proposals: number;
}

export default function IntelMarketPage() {
  const [items, setItems] = useState<MarketRow[]>([]);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    api.get<{ items: MarketRow[] }>(`/api/intel/market?days=${days}`)
      .then((d) => setItems(d.items))
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [days]);
  useEffect(load, [load]);

  const maxMarket = Math.max(...items.map((i) => i.market), 1);
  const totalComp = items.reduce((a, b) => a + b.comp_wins, 0);
  const totalProp = items.reduce((a, b) => a + b.proposals, 0);

  return (
    <AppShell>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-[20px] font-semibold text-[var(--ink)]">区域市场</h1>
          <p className="mt-0.5 text-[12.5px] text-[var(--muted)]">
            按城市看市场盘子 / 已建联 / 竞对压力（30 天中标）/ 备案机会——
            盘子大且竞对活跃的城市优先深耕
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={String(days)} onChange={(v) => setDays(Number(v))} className="!w-28 shrink-0">
            <Select.Option value="30">近 30 天</Select.Option>
            <Select.Option value="90">近 90 天</Select.Option>
            <Select.Option value="365">近 1 年</Select.Option>
          </Select>
          <Button size="small" icon={<RefreshCw size={13} />} onClick={load}>刷新</Button>
        </div>
      </div>

      {/* 汇总条 */}
      <div className="mt-3 flex flex-wrap gap-2">
        <Tag color="cyan" type="light">竞对 30 天中标：{totalComp}</Tag>
        <Tag color="amber" type="light">备案机会：{totalProp}</Tag>
      </div>

      {/* 地市热力表 */}
      <div className="mt-3 rounded-lg border border-[var(--border)] bg-white p-4">
        <div className="overflow-x-auto">
          <div className="hscroll-table-wrap">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-[12.5px] font-normal text-[var(--muted)]">
                  <th className="py-2 pr-4 font-normal">城市</th>
                  <th className="py-2 pr-4 text-right font-normal">市场盘子</th>
                  <th className="py-2 pr-4 text-right font-normal">已建联（可拨）</th>
                  <th className="py-2 pr-4 text-right font-normal">建联率</th>
                  <th className="py-2 pr-4 text-right font-normal">竞对中标({days}d)</th>
                  <th className="py-2 text-right font-normal">备案({days}d)</th>
                </tr>
              </thead>
              <tbody>
                {items.map((m) => {
                  const rate = m.market > 0 ? Math.round((m.dialable / m.market) * 100) : 0;
                  const hot = m.comp_wins > 0 || m.proposals > 0;
                  return (
                    <tr key={m.city} className="border-t border-[var(--rule)] hover:bg-[var(--raised)]">
                      <td className="py-2 pr-4">
                        <a
                          href={`/enterprises?province=${encodeURIComponent(m.city)}`}
                          className="font-medium text-[var(--ink)] hover:text-[var(--accent)]"
                        >
                          {m.city}
                        </a>
                        {hot && <Tag color="amber" type="light" size="small" className="!ml-2">活跃</Tag>}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">{m.market.toLocaleString()}</td>
                      <td className="py-2 pr-4 text-right tabular-nums">
                        <span className={m.dialable > 0 ? "text-[var(--positive)]" : "text-[var(--muted)]"}>
                          {m.dialable.toLocaleString()}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">{rate}%</td>
                      <td className="py-2 pr-4 text-right tabular-nums">
                        {m.comp_wins > 0 ? (
                          <span className="font-medium text-[#c2801b]">{m.comp_wins}</span>
                        ) : (
                          <span className="text-[var(--muted)]">—</span>
                        )}
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {m.proposals > 0 ? (
                          <span className="font-medium text-[var(--accent)]">{m.proposals}</span>
                        ) : (
                          <span className="text-[var(--muted)]">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        {loading && (
          <div className="py-8 text-center text-[13px] text-[var(--muted)]">加载中…</div>
        )}
      </div>
    </AppShell>
  );
}
