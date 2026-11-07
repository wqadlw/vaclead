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
  channel: "gd" | "hb" | "js";
  pub_date: string;
  title: string;
  project: string;
  url: string;
}

const CHANNEL_META: Record<string, { label: string; color: "blue" | "green" | "cyan" }> = {
  gd: { label: "广东备案", color: "blue" },
  hb: { label: "湖北备案", color: "green" },
  js: { label: "江苏备案", color: "cyan" },
};

export default function ProposalsPage() {
  const [data, setData] = useState<CenterData | null>(null);
  const [province, setProvince] = useState("");
  const [days, setDays] = useState(90);
  const [q, setQ] = useState("");
  const [nurturing, setNurturing] = useState<number | null>(null);
  const [converting, setConverting] = useState<number | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams({ days: String(days), limit: "100" });
    if (province) p.set("province", province);
    if (q.trim()) p.set("q", q.trim());
    api.get<CenterData>(`/api/proposals/center?${p}`)
      .then(setData)
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

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* 省份分布 */}
        <div className="rounded-lg border border-[var(--border)] bg-white p-4 lg:col-span-1">
          <div className="mb-2 text-[13.5px] font-medium text-[var(--ink)]">备案省份分布</div>
          {provinceItems.length > 0 ? (
            <ProvinceBars
              items={provinceItems}
              onPick={(p) => { setProvince(province === p ? "" : p); }}
            />
          ) : (
            <div className="py-8 text-center text-[12.5px] text-[var(--muted)]">暂无数据</div>
          )}
        </div>

        {/* 列表 */}
        <div className="lg:col-span-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1.5 text-[13.5px] font-medium text-[var(--ink)]">
              <Sprout size={15} /> 备案动态
            </span>
            <Input
              size="small"
              placeholder="搜单位/项目名"
              value={q}
              onChange={setQ}
              showClear
              className="!w-48"
            />
            <Select value={province || undefined} placeholder="全部省份" showClear onChange={(v) => setProvince(String(v ?? ""))} className="!w-32">
              {provinceItems.map((p) => (
                <Select.Option key={p.province} value={p.province}>{p.province} ({p.count})</Select.Option>
              ))}
            </Select>
            <Select value={String(days)} onChange={(v) => setDays(Number(v))} className="!w-28">
              <Select.Option value="30">近 30 天</Select.Option>
              <Select.Option value="90">近 90 天</Select.Option>
              <Select.Option value="365">近 1 年</Select.Option>
              <Select.Option value="0">全部</Select.Option>
            </Select>
            <span className="ml-auto text-[12.5px] text-[var(--muted)]">共 {data?.total ?? 0} 条</span>
          </div>

          <div className="mt-2.5 space-y-2.5">
            {(data?.items ?? []).map((s) => (
              <div key={s.id} className="rounded-lg border border-[var(--border)] bg-white px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Tag color={(CHANNEL_META[s.channel] ?? CHANNEL_META.hb).color} type="light" size="small">
                        {(CHANNEL_META[s.channel] ?? CHANNEL_META.hb).label}
                      </Tag>
                      <span className="text-[12px] tabular-nums text-[var(--muted)]">{s.pub_date}</span>
                      {(s.province || s.city) && (
                        <span className="text-[12px] text-[var(--muted)]">
                          {[s.province, s.city].filter(Boolean).join(" · ")}
                        </span>
                      )}
                      {s.industry_name && <span className="text-[12px] text-[var(--muted)]">· {s.industry_name}</span>}
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                      <a
                        href={`/enterprises/view?id=${s.enterprise_id}`}
                        className="truncate text-[14px] font-medium text-[var(--accent)] hover:underline"
                      >
                        {s.name}
                      </a>
                      {s.url && (
                        <a href={s.url} target="_blank" rel="noreferrer" className="shrink-0 text-[11.5px] text-[var(--accent)] hover:underline">
                          原文 ↗
                        </a>
                      )}
                    </div>
                    {s.project && (
                      <div className="mt-0.5 border-l-2 border-[var(--accent)] pl-2 text-[12px] leading-4 text-[var(--muted)]">
                        项目：{s.project}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col gap-1.5">
                    <Button size="small" icon={<Sprout size={13} />} loading={nurturing === s.enterprise_id} onClick={() => nurture(s.enterprise_id)}>
                      培育回访
                    </Button>
                    <Button size="small" theme="solid" icon={<UserPlus size={13} />} loading={converting === s.enterprise_id} onClick={() => toLead(s.enterprise_id)}>
                      转线索
                    </Button>
                  </div>
                </div>
              </div>
            ))}
            {(data?.items ?? []).length === 0 && (
              <div className="py-16 text-center text-[13px] text-[var(--muted)]">
                当前筛选下暂无备案公示 —— 广东/湖北通道每日 05:00/05:10 自动采集
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
