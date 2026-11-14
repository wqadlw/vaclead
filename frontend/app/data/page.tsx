"use client";

/**
 * 信息中心（SPEC-INFO）：数据资产总览——有什么/从哪来/多新/多全。
 * 把散在 tasks（采集入口）/quality（质量运维）/competitors（雷达）的
 * "信息"维度收拢成一个数据资产视角页面。质量运维仍在 quality 页。
 */
import { useEffect, useState } from "react";
import { Tag } from "@douyinfe/semi-ui";
import { Database, RefreshCw } from "lucide-react";
import { Button } from "@douyinfe/semi-ui";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface SourceRow {
  source: string;
  label: string;
  count: number;
  dialable: number;
  first_at: string;
  last_at: string;
  batch_count: number;
}

interface BatchRow {
  batch_id: string;
  source: string;
  label: string;
  count: number;
  first_at: string;
  last_at: string;
}

interface OverviewData {
  assets: { total: number; contacts_dialable: number; signals: number; leads: number };
  sources: SourceRow[];
  batches: BatchRow[];
  /** SPEC-INFO v2：行业分布 + 字段溯源概览 */
  industries: { key: string; name: string; count: number }[];
  prov_stats: [string, number][];
}

export default function DataCenterPage() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    api.get<OverviewData>("/api/data/overview")
      .then(setData)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  return (
    <AppShell>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[20px] font-semibold text-[var(--ink)]">信息中心</h1>
          <p className="mt-0.5 text-[12.5px] text-[var(--muted)]">
            数据资产总览——有什么信息、从哪来、多新、多全；采集入口在拓客任务，质量运维在数据质量
          </p>
        </div>
        <Button size="small" icon={<RefreshCw size={13} />} onClick={load}>刷新</Button>
      </div>

      {/* 数据资产总览 */}
      <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-[var(--border)] lg:grid-cols-4">
        {[
          { k: "企业库", v: data?.assets.total, sub: "全来源去重在管" },
          { k: "可拨联系人", v: data?.assets.contacts_dialable, sub: "号码库规范化口径" },
          { k: "意图信号", v: data?.assets.signals, sub: "公告 × 设备词表命中" },
          { k: "线索", v: data?.assets.leads, sub: "含全部状态" },
        ].map((c) => (
          <div key={c.k} className="bg-white px-4 py-3.5">
            <div className="text-[12px] text-[var(--muted)]">{c.k}</div>
            <div className="mt-1 text-[24px] font-semibold tabular-nums leading-8 text-[var(--ink)]">
              {loading || c.v == null ? "—" : c.v.toLocaleString("zh-CN")}
            </div>
            <div className="text-[11.5px] text-[var(--muted)]">{c.sub}</div>
          </div>
        ))}
      </div>

      {/* 来源矩阵 */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4">
        <h2 className="text-[15px] font-medium text-[var(--ink)]">来源矩阵</h2>
        <p className="mt-0.5 text-[12px] text-[var(--muted)]">
          每个信息来源的资产规模与新鲜度——「最近入库」久未更新的来源说明采集链路需要检查
        </p>
        <div className="mt-2.5 overflow-x-auto">
          <div className="hscroll-table-wrap">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-[12.5px] font-normal text-[var(--muted)]">
                  <th className="py-2 pr-4 font-normal">来源</th>
                  <th className="py-2 pr-4 text-right font-normal">企业数</th>
                  <th className="py-2 pr-4 text-right font-normal">可拨</th>
                  <th className="py-2 pr-4 font-normal">最早入库</th>
                  <th className="py-2 pr-4 font-normal">最近入库</th>
                  <th className="py-2 text-right font-normal">批次数</th>
                </tr>
              </thead>
              <tbody>
                {(data?.sources ?? []).map((s) => (
                  <tr key={s.source} className="border-t border-[var(--rule)] hover:bg-[var(--raised)]">
                    <td className="py-2 pr-4 font-medium text-[var(--ink)]">{s.label}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{s.count.toLocaleString()}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      <span className={s.dialable > 0 ? "font-medium text-[var(--positive)]" : "text-[var(--muted)]"}>
                        {s.dialable.toLocaleString()}
                      </span>
                    </td>
                    <td className="py-2 pr-4 tabular-nums text-[var(--muted)]">{s.first_at || "—"}</td>
                    <td className="py-2 pr-4 tabular-nums text-[var(--muted)]">{s.last_at || "—"}</td>
                    <td className="py-2 text-right tabular-nums">{s.batch_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {loading && (
            <div className="py-8 text-center text-[13px] text-[var(--muted)]">加载中…</div>
          )}
        </div>
      </div>

      {/* 批次台账 */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4">
        <h2 className="text-[15px] font-medium text-[var(--ink)]">批次台账</h2>
        <p className="mt-0.5 text-[12px] text-[var(--muted)]">
          每次导入/采集的批次追溯（source_batch_id）——POI 采集暂未打批次标
        </p>
        <div className="mt-2.5 space-y-1.5">
          {(data?.batches ?? []).map((b) => (
            <div key={b.batch_id} className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--rule)] px-3 py-2">
              <Tag color="cyan" type="light" size="small">{b.label}</Tag>
              <span className="truncate font-mono text-[12px] text-[var(--muted)]" title={b.batch_id}>
                {b.batch_id}
              </span>
              <span className="ml-auto text-[12.5px] tabular-nums text-[var(--ink)]">{b.count} 家</span>
              <span className="text-[11.5px] tabular-nums text-[var(--muted)]">{b.last_at}</span>
            </div>
          ))}
          {(data?.batches ?? []).length === 0 && !loading && (
            <div className="py-6 text-center text-[13px] text-[var(--muted)]">暂无批次记录</div>
          )}
        </div>
      </div>

      {/* SPEC-INFO v2：行业分布 + 溯源概览（双卡并排，移动端纵排） */}
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-[var(--border)] bg-white p-4">
          <h2 className="text-[15px] font-medium text-[var(--ink)]">行业分布 Top 10</h2>
          <p className="mt-0.5 text-[12px] text-[var(--muted)]">客户储备按行业——哪个行业储备最厚，哪个就是优先主攻方向</p>
          <div className="mt-2.5 space-y-1.5">
            {(data?.industries ?? []).map((ind) => {
              const max = Math.max(...(data?.industries ?? []).map((x) => x.count), 1);
              return (
                <div key={ind.key} className="flex items-center gap-2.5">
                  <span className="w-32 shrink-0 truncate text-[12px] text-[var(--ink)]" title={ind.name}>{ind.name}</span>
                  <div className="h-4 flex-1 rounded-sm bg-[var(--raised)]">
                    <div className="h-full rounded-sm bg-[var(--accent)]" style={{ width: `${Math.max((ind.count / max) * 100, 2)}%` }} />
                  </div>
                  <span className="w-12 shrink-0 text-right text-[12px] tabular-nums text-[var(--muted)]">{ind.count.toLocaleString()}</span>
                </div>
              );
            })}
            {(data?.industries ?? []).length === 0 && (
              <div className="py-4 text-center text-[12px] text-[var(--muted)]">暂无行业分类</div>
            )}
          </div>
        </div>
        <div className="rounded-lg border border-[var(--border)] bg-white p-4">
          <h2 className="text-[15px] font-medium text-[var(--ink)]">信息溯源概览</h2>
          <p className="mt-0.5 text-[12px] text-[var(--muted)]">
            工商字段按来源统计——每条信息都记录"从哪来、谁来写、何时写"（字段级溯源）
          </p>
          <div className="mt-2.5 space-y-1.5">
            {(data?.prov_stats ?? []).map(([src, n]) => (
              <div key={src} className="flex items-center justify-between rounded-lg border border-[var(--rule)] px-3 py-2">
                <span className="text-[12.5px] text-[var(--ink)]">{src}</span>
                <span className="text-[12.5px] tabular-nums font-medium text-[var(--accent)]">{n} 个字段</span>
              </div>
            ))}
            {(data?.prov_stats ?? []).length === 0 && (
              <div className="py-4 text-center text-[12px] text-[var(--muted)]">
                暂无溯源记录——通过「风鸟搬运」或「工商补数」录入后此处可见
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 姊妹页入口 */}
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
        {[
          { t: "数据质量", d: "覆盖漏斗 / 缺口回填 / 号码共享分析 / 查重复核", href: "/quality" },
          { t: "拓客任务", d: "采集与导入入口（官方数据 / POI / Excel / 工商补数）", href: "/tasks" },
          { t: "采集水位", d: "各采集源新鲜度 SLA 监控（超期红标）", href: "/quality" },
        ].map((c) => (
          <a key={c.href} href={c.href} className="rounded-lg border border-[var(--border)] bg-white px-4 py-3 transition-colors hover:border-[var(--accent)]">
            <div className="text-[13.5px] font-medium text-[var(--ink)]">{c.t}</div>
            <div className="mt-1 text-[12px] leading-4 text-[var(--muted)]">{c.d}</div>
          </a>
        ))}
      </div>
    </AppShell>
  );
}
