"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Input, InputNumber, Select, Table, Tag, Toast } from "@douyinfe/semi-ui";
import { Search, RotateCcw } from "lucide-react";
import AppShell from "@/components/AppShell";
import EnterpriseDrawer from "@/components/EnterpriseDrawer";
import { api } from "@/lib/api";
import type { Enterprise, PageData } from "@/lib/api";
import { INDUSTRY_OPTIONS } from "@/lib/constants";

const PAGE_SIZE = 20;

const PROVINCE_OPTIONS = [
  "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
  "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
  "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
  "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
].map((p) => ({ value: p, label: p }));

const INDUSTRY_SELECT = INDUSTRY_OPTIONS.map((i) => ({ value: i.key, label: i.name }));
const SIGNAL_DAY_OPTIONS = [
  { value: 7, label: "近 7 天" },
  { value: 30, label: "近 30 天" },
  { value: 90, label: "近 90 天" },
  { value: 365, label: "近一年" },
];
const SCORE_MIN_OPTIONS = [
  { value: 40, label: "≥ 40 分" },
  { value: 60, label: "≥ 60 分" },
  { value: 80, label: "≥ 80 分" },
];
/** 后端语义：0 排除同行；1 仅同行；-1 含同行（置灰展示）。 */
const COMPETITOR_OPTIONS = [
  { value: -1, label: "含同行" },
  { value: 0, label: "排除同行" },
  { value: 1, label: "仅同行" },
];

interface Filters {
  q?: string;
  industry_key?: string;
  province?: string;
  city?: string;
  district?: string;
  capital_min?: number | null;
  capital_max?: number | null;
  signal_days?: number | null;
  score_min?: number | null;
  is_competitor: number;
}

const TIER_TAG: Record<number, "indigo" | "cyan" | "grey"> = { 1: "indigo", 2: "cyan", 3: "grey" };
const TIER_BY_KEY: Record<string, number> = {};
for (const opt of INDUSTRY_OPTIONS) TIER_BY_KEY[opt.key] = opt.tier;

function regionOf(e: Enterprise): string {
  const s = [e.province, e.city].map((x) => (x ?? "").trim()).filter(Boolean).join(" ");
  return s || (e.district ?? "").trim() || "-";
}

export default function EnterprisesPage() {
  const [filters, setFilters] = useState<Filters>({ is_competitor: -1 });
  const [applied, setApplied] = useState<Filters>({ is_competitor: -1 });
  const [page, setPage] = useState(1);
  const [data, setData] = useState<PageData<Enterprise> | null>(null);
  const [loading, setLoading] = useState(false);
  const [drawerId, setDrawerId] = useState<number | null>(null);
  const [convertingId, setConvertingId] = useState<number | null>(null);

  const fetchData = useCallback(async (f: Filters, p: number) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      const q = f.q?.trim();
      if (q) params.set("q", q);
      if (f.industry_key) params.set("industry_key", f.industry_key);
      const province = f.province?.trim();
      if (province) params.set("province", province);
      const city = f.city?.trim();
      if (city) params.set("city", city);
      const district = f.district?.trim();
      if (district) params.set("district", district);
      if (typeof f.capital_min === "number") params.set("capital_min", String(f.capital_min));
      if (typeof f.capital_max === "number") params.set("capital_max", String(f.capital_max));
      if (typeof f.signal_days === "number") params.set("signal_days", String(f.signal_days));
      if (typeof f.score_min === "number") params.set("score_min", String(f.score_min));
      params.set("is_competitor", String(f.is_competitor));
      params.set("page", String(p));
      params.set("page_size", String(PAGE_SIZE));
      setData(await api.get<PageData<Enterprise>>(`/api/enterprises?${params.toString()}`));
    } catch (e) {
      Toast.error(`查询失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData(applied, page);
  }, [fetchData, applied, page]);

  const set = (patch: Partial<Filters>) => setFilters((prev) => ({ ...prev, ...patch }));

  const convert = async (e: Enterprise) => {
    setConvertingId(e.id);
    try {
      await api.post(`/api/enterprises/${e.id}/lead`);
      Toast.success(`已转为线索：${e.name}`);
      setDrawerId(null);
      void fetchData(applied, page);
    } catch (err) {
      Toast.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setConvertingId(null);
    }
  };

  const columns = [
    {
      title: "企业名称",
      dataIndex: "name",
      width: 260,
      render: (_: unknown, r: Enterprise) => (
        <button
          onClick={() => setDrawerId(r.id)}
          className="block max-w-[240px] cursor-pointer truncate border-0 bg-transparent p-0 text-left text-[13.5px] font-medium text-[var(--ink)] hover:text-[var(--accent)]"
          title={r.name}
        >
          {r.name}
        </button>
      ),
    },
    {
      title: "行业",
      dataIndex: "industry_name",
      width: 130,
      render: (_: unknown, r: Enterprise) =>
        r.is_competitor === 1 ? (
          <Tag color="amber" type="light">疑似同行</Tag>
        ) : (
          <Tag color={TIER_TAG[TIER_BY_KEY[r.industry_key ?? ""] ?? 3]} type="light">
            {r.industry_name || "未分类"}
          </Tag>
        ),
    },
    { title: "地区", width: 140, render: (_: unknown, r: Enterprise) => <span className="text-[var(--muted)]">{regionOf(r)}</span> },
    {
      title: "注册资本（万）",
      dataIndex: "registered_capital_wan",
      width: 130,
      align: "right" as const,
      render: (v: number | null) => <span className="tabular-nums text-[var(--muted)]">{v == null ? "—" : v.toLocaleString("zh-CN")}</span>,
    },
    {
      title: "ICP",
      dataIndex: "icp_score",
      width: 70,
      align: "right" as const,
      sorter: (a: Enterprise, b: Enterprise) => a.icp_score - b.icp_score,
      render: (v: number) => <span className="tabular-nums text-[var(--ink)]">{v}</span>,
    },
    {
      title: "意图",
      dataIndex: "intent_score",
      width: 70,
      align: "right" as const,
      sorter: (a: Enterprise, b: Enterprise) => a.intent_score - b.intent_score,
      render: (v: number) => (
        <span className={`tabular-nums ${v > 0 ? "font-medium text-[var(--accent)]" : "text-[#9ca0a3]"}`}>{v}</span>
      ),
    },
    {
      title: "信号",
      dataIndex: "signal_count",
      width: 64,
      align: "right" as const,
      render: (v: number) => (v > 0 ? <span className="font-medium tabular-nums text-[var(--negative)]">{v}</span> : <span className="tabular-nums text-[#9ca0a3]">0</span>),
    },
    {
      title: "",
      width: 90,
      render: (_: unknown, r: Enterprise) =>
        r.is_competitor === 1 ? (
          <span className="text-[12.5px] text-[#9ca0a3]">不可转</span>
        ) : (
          <button
            onClick={() => convert(r)}
            disabled={convertingId === r.id}
            className="cursor-pointer border-0 bg-transparent p-0 text-[13px] font-medium text-[var(--ink)] hover:text-[var(--accent)] disabled:opacity-50"
          >
            {convertingId === r.id ? "转入中…" : "转线索 →"}
          </button>
        ),
    },
  ];

  const ctl = { width: 128 };

  return (
    <AppShell>
      {/* 标题行 */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-[22px] font-semibold leading-7 text-[var(--ink)]">企业筛选</h1>
          <p className="mt-1 text-[13.5px] text-[var(--muted)]">
            共 {data?.total ?? 0} 家 · 疑似同行已置灰 · 点击企业名查看评分理由与信号
          </p>
        </div>
      </div>

      {/* 工具行：左侧筛选+搜索，右侧重置 */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Select style={ctl} optionList={INDUSTRY_SELECT} placeholder="全部行业" filter showClear value={filters.industry_key} onChange={(v) => set({ industry_key: (v as string) || undefined })} />
        <Select style={ctl} optionList={PROVINCE_OPTIONS} placeholder="全部省份" filter showClear value={filters.province} onChange={(v) => set({ province: (v as string) || undefined })} />
        <Input style={{ width: 104 }} placeholder="城市" value={filters.city} onChange={(v) => set({ city: v })} />
        <Input style={{ width: 104 }} placeholder="区县" value={filters.district} onChange={(v) => set({ district: v })} />
        <div className="flex items-center gap-1">
          <InputNumber style={{ width: 84 }} placeholder="资本≥" min={0} value={filters.capital_min ?? undefined} onChange={(v) => set({ capital_min: typeof v === "number" ? v : null })} />
          <span className="text-[var(--control)]">—</span>
          <InputNumber style={{ width: 84 }} placeholder="≤" min={0} value={filters.capital_max ?? undefined} onChange={(v) => set({ capital_max: typeof v === "number" ? v : null })} />
        </div>
        <Select style={ctl} optionList={SIGNAL_DAY_OPTIONS} placeholder="信号不限" showClear value={filters.signal_days ?? undefined} onChange={(v) => set({ signal_days: typeof v === "number" ? v : null })} />
        <Select style={ctl} optionList={SCORE_MIN_OPTIONS} placeholder="评分不限" showClear value={filters.score_min ?? undefined} onChange={(v) => set({ score_min: typeof v === "number" ? v : null })} />
        <Select style={ctl} optionList={COMPETITOR_OPTIONS} value={filters.is_competitor} onChange={(v) => set({ is_competitor: (v as number) ?? -1 })} />
        <Input
          style={{ width: 210 }}
          prefix={<Search size={13} className="text-[#9ca0a3]" />}
          placeholder="名称 / 经营范围关键词"
          value={filters.q}
          onChange={(v) => set({ q: v })}
          onKeyDown={(e) => {
            if (e.keyCode === 13) {
              setPage(1);
              setApplied(filters);
            }
          }}
        />
        <Button theme="solid" className="!h-8" onClick={() => { setPage(1); setApplied(filters); }}>
          查询
        </Button>
        <Button
          theme="borderless"
          type="tertiary"
          icon={<RotateCcw size={13} />}
          onClick={() => {
            const reset: Filters = { is_competitor: -1 };
            setFilters(reset);
            setPage(1);
            setApplied(reset);
          }}
        />
      </div>

      {/* 表格直接落在纸上 */}
      <div className="mt-4">
        <Table<Enterprise>
          rowKey="id"
          columns={columns as never}
          dataSource={data?.items ?? []}
          loading={loading}
          size="middle"
          empty="没有匹配的企业 —— 放宽筛选条件，或先去「拓客任务」采集数据"
          onRow={(r) => ({ className: r?.is_competitor === 1 ? "opacity-45 saturate-50" : "" })}
          pagination={{
            currentPage: page,
            pageSize: PAGE_SIZE,
            total: data?.total ?? 0,
            onPageChange: (p: number) => setPage(p),
          }}
        />
      </div>

      <EnterpriseDrawer
        open={drawerId !== null}
        enterpriseId={drawerId}
        onClose={() => setDrawerId(null)}
        onConverted={() => void fetchData(applied, page)}
      />
    </AppShell>
  );
}
