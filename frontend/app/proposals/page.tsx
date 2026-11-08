"use client";

/**
 * 拟建雷达（SPEC-BP）：备案公示信号的专属视图与培育流。
 * "早"的三级货币化阶梯最早一级（备案→招标→中标）；对标剑鱼把拟建当
 * 核心付费点。粤/鄂通道数据已在库，本页只做视图与培育语义：
 * KPI（30 天新增/正处招标窗口）+ 省份分布 + 列表（项目名/培育回访）。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, Select, Tag, Toast } from "@douyinfe/semi-ui";
import { Sprout, UserPlus } from "lucide-react";
import AppShell from "@/components/AppShell";
import LoadMoreSensor from "@/components/LoadMoreSensor";
import { ProvinceBars } from "@/components/charts";
import { api } from "@/lib/api";

interface CenterData {
  overview: {
    total_30d: number;
    total_90d: number;
    window_now: number;
    province_total: number;
    top_province: string;
  };
  provinces: Record<string, number>;
  industries: Record<string, number>;
  items: ProposalItem[];
  total: number;
}

interface ProposalItem {
  id: number;
  enterprise_id: number;
  name: string;
  province: string | null;
  city: string | null;
  industry_name: string | null;
  channel: "gd" | "hb" | "js" | "zj";
  pub_date: string;
  title: string;
  project: string;
  url: string;
}

const CHANNEL_META: Record<string, { label: string; color: "blue" | "green" | "cyan" | "amber" }> = {
  gd: { label: "广东备案", color: "blue" },
  hb: { label: "湖北备案", color: "green" },
  js: { label: "江苏备案", color: "cyan" },
  zj: { label: "浙江办理", color: "amber" },
};

export default function ProposalsPage() {
  const [data, setData] = useState<CenterData | null>(null);
  const [province, setProvince] = useState("");
  const [days, setDays] = useState(90);
  const [q, setQ] = useState("");
  const [nurturing, setNurturing] = useState<number | null>(null);
  const [converting, setConverting] = useState<number | null>(null);

  const [visibleCount, setVisibleCount] = useState(30);
  const [onlyWindow, setOnlyWindow] = useState(false);
  const today = new Date().toISOString().slice(0, 10);
  const d90 = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10);
  const d30 = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);

  const load = useCallback(() => {
    const p = new URLSearchParams({ days: String(days), limit: "100" });
    if (province) p.set("province", province);
    if (q.trim()) p.set("q", q.trim());
    api.get<CenterData>(`/api/proposals/center?${p}`)
      .then((d) => { setData(d); setVisibleCount(30); })
      .catch(() => undefined);
  }, [days, province, q]);
  useEffect(load, [load]);

  const nurture = async (eid: number) => {
    setNurturing(eid);
    try {
      const r = await api.post<{ created: boolean; due_date: string }>(
        `/api/proposals/${eid}/nurture`,
        {},
      );
      Toast.success(
        r.created
          ? `已建回访待办（${r.due_date} 到期）——踩在招标窗口开头`
          : "已有回访待办，无需重复",
      );
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setNurturing(null);
    }
  };

  const toLead = async (eid: number) => {
    setConverting(eid);
    try {
      await api.post(`/api/enterprises/${eid}/lead`, {});
      Toast.success("已转线索，去线索看板跟进");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "转线索失败");
    } finally {
      setConverting(null);
    }
  };

  const ov = data?.overview;
  const provinceItems = Object.entries(data?.provinces ?? {})
    .map(([name, count]) => ({ province: name, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 8);

  // SPEC-CF：客户端窗口过滤 + 分页切片（后端已按省份/天数/关键词过滤）
  const filtered = (data?.items ?? []).filter(
    (s) => !onlyWindow || (s.pub_date >= d90 && s.pub_date <= d30)
  );
  // SPEC-CH CH4：按单位聚合——同一单位多条公示（审批节点流）合并一行
  const entCount = new Map<number, number>();
  for (const s of filtered) entCount.set(s.enterprise_id, (entCount.get(s.enterprise_id) ?? 0) + 1);
  const seenEnt = new Set<number>();
  const grouped = filtered.filter((s) => {
    if (seenEnt.has(s.enterprise_id)) return false;
    seenEnt.add(s.enterprise_id);
    return true;
  });
  const paged = grouped.slice(0, visibleCount);

  return (
    <AppShell>
      {/* KPI 晨检卡 */}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-[var(--border)] md:grid-cols-4">
        {[
          { k: "近 30 天新增备案", v: ov ? String(ov.total_30d) : "—", sub: "扩张前兆持续进池" },
          { k: "正处招标窗口", v: ov ? String(ov.window_now) : "—", sub: "备案后 1-3 个月=招标前夜", amber: true },
          { k: "近 90 天新增", v: ov ? String(ov.total_90d) : "—", sub: "培育池总深度" },
          { k: "覆盖省份", v: ov ? String(ov.province_total) : "—", sub: ov?.top_province ? `最活跃：${ov.top_province}` : "" },
        ].map((c) => (
          <div key={c.k} className="bg-white px-4 py-3">
            <div className="text-[11.5px] text-[var(--muted)]">{c.k}</div>
            <div className={`mt-0.5 text-[18px] font-medium tabular-nums ${c.amber ? "text-[#c2801b]" : "text-[var(--ink)]"}`}>
              {c.v}
            </div>
            {c.sub && <div className="mt-0.5 truncate text-[11.5px] text-[var(--muted)]">{c.sub}</div>}
          </div>
        ))}
      </div>

      {/* SPEC-CF 重构：全宽紧凑列表——省份 chips 行 + 仅窗口内开关 + 单行式卡片 */}
      {/* 工具条 */}
      <div className="mt-4 flex flex-nowrap items-center gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [-&::-webkit-scrollbar]:hidden md:flex-wrap md:overflow-visible">
        <Input
          size="small"
          placeholder="搜单位/项目名"
          value={q}
          onChange={setQ}
          showClear
          className="!w-52 shrink-0"
        />
        <Select value={province || undefined} placeholder="全部省份" showClear onChange={(v) => setProvince(String(v ?? ""))} className="!w-28 shrink-0">
          {provinceItems.map((p) => (
            <Select.Option key={p.province} value={p.province}>{p.province} ({p.count})</Select.Option>
          ))}
        </Select>
        <Select value={String(days)} onChange={(v) => setDays(Number(v))} className="!w-28 shrink-0">
          <Select.Option value="30">近 30 天</Select.Option>
          <Select.Option value="90">近 90 天</Select.Option>
          <Select.Option value="365">近 1 年</Select.Option>
          <Select.Option value="0">全部</Select.Option>
        </Select>
        <button
          onClick={() => setOnlyWindow(!onlyWindow)}
          className={`shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-[12px] ${
            onlyWindow
              ? "border-[#c2801b] bg-[#c2801b] text-white"
              : "border-[var(--border)] bg-white text-[var(--muted)]"
          }`}
          title="备案后 1-3 个月 = 招标前夜，优先看这批"
        >
          仅看正处招标窗口
        </button>
        <span className="shrink-0 whitespace-nowrap text-[12.5px] text-[var(--muted)]">
          共 {grouped.length} 家单位{onlyWindow ? "（窗口内）" : ""}
        </span>
      </div>

      {/* 省份 chips（点击筛选，横向一行不占竖空间） */}
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        {provinceItems.map((p) => (
          <button
            key={p.province}
            onClick={() => setProvince(province === p.province ? "" : p.province)}
            className={`rounded-full border px-3 py-1.5 text-[12.5px] md:px-2.5 md:py-0.5 md:text-[11.5px] ${
              province === p.province
                ? "border-[var(--accent)] bg-[var(--accent-weak,rgba(94,106,210,0.1))] text-[var(--accent)]"
                : "border-[var(--border)] bg-white text-[var(--muted)] hover:border-[var(--accent)]"
            }`}
          >
            {p.province} {p.count}
          </button>
        ))}
      </div>

      {/* 紧凑列表 */}
      <div className="mt-2.5 overflow-hidden rounded-lg border border-[var(--border)] bg-white">
        {paged.map((s) => {
          const inWindow = s.pub_date >= d90 && s.pub_date <= d30;
          return (
            <div key={s.id} className="flex items-start gap-3 border-b border-[var(--rule)] px-4 py-2.5 last:border-b-0 hover:bg-[var(--raised)]">
              <span className="w-[84px] shrink-0 pt-0.5 text-[12px] tabular-nums text-[var(--muted)]">{s.pub_date}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Tag className="!shrink-0" color={(CHANNEL_META[s.channel] ?? CHANNEL_META.hb).color} type="light" size="small">
                    {(CHANNEL_META[s.channel] ?? CHANNEL_META.hb).label}
                  </Tag>
                  {inWindow && <Tag className="!shrink-0" color="amber" type="light" size="small">窗口内</Tag>}
                  {(entCount.get(s.enterprise_id) ?? 0) > 1 && (
                    <Tag className="!shrink-0" color="grey" type="light" size="small">共 {entCount.get(s.enterprise_id)} 条公示</Tag>
                  )}
                  <a href={`/enterprises/view?id=${s.enterprise_id}`} className="inline-flex items-center self-stretch truncate py-2 text-[13.5px] font-medium text-[var(--ink)] hover:text-[var(--accent)]">
                    {s.name}
                  </a>
                  {s.industry_name && <span className="text-[11.5px] text-[var(--muted)]">{s.industry_name}</span>}
                </div>
                {s.project && (
                  <div className="mt-0.5 truncate text-[12px] text-[var(--muted)]" title={s.project}>
                    项目：{s.project}
                  </div>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-1.5 pt-0.5">
                <Button size="small" theme="borderless" icon={<Sprout size={13} />} loading={nurturing === s.enterprise_id} onClick={() => nurture(s.enterprise_id)}>
                  培育
                </Button>
                <Button size="small" icon={<UserPlus size={13} />} loading={converting === s.enterprise_id} onClick={() => toLead(s.enterprise_id)}>
                  转线索
                </Button>
              </div>
            </div>
          );
        })}
        {paged.length === 0 && (
          <div className="py-14 text-center text-[13px] text-[var(--muted)]">
            {onlyWindow ? "窗口内暂无备案 —— 切回全部时间范围看看" : "当前筛选下暂无备案公示 —— 四省通道每日清晨自动采集"}
          </div>
        )}
      </div>
      {grouped.length > visibleCount && (
        <LoadMoreSensor onLoadMore={() => setVisibleCount((n) => n + 50)} />
      )}
    </AppShell>
  );
}
