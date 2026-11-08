"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Checkbox, Dropdown, Input, InputNumber, Modal, Popover, Select, Table, Tag, Tabs, Toast } from "@douyinfe/semi-ui";
import { ChevronDown, Pencil, Save, Search, Settings2, RotateCcw, X } from "lucide-react";
import AppShell from "@/components/AppShell";
import { AGG_OPS, AdvancedFilterModal, QuickPicks, SummaryBar } from "@/components/EnterprisesWidgets";
import { addToCompare, readCompare } from "@/lib/compare";
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
/** 采集来源（通道级筛选：后端把 ggzy/ggzy_buyer 归并为一个通道）。 */
const SOURCE_OPTIONS = [
  { value: "ggzy", label: "官方中标数据" },
  { value: "ccgp", label: "政采网公告" },
  { value: "csv", label: "CSV 导入" },
  { value: "poi", label: "POI 采集" },
  { value: "tender", label: "招标导入" },
  { value: "manual", label: "手工" },
];
const SOURCE_TAG: Record<string, { label: string; color: "cyan" | "green" | "blue" | "grey" | "white" }> = {
  ggzy: { label: "官方中标", color: "cyan" },
  ggzy_buyer: { label: "官方买家", color: "green" },
  ccgp: { label: "政采供应商", color: "blue" },
  ccgp_buyer: { label: "政采买家", color: "green" },
  csv: { label: "CSV", color: "grey" },
  tender: { label: "招标", color: "grey" },
  poi: { label: "POI", color: "grey" },
  manual: { label: "手工", color: "white" },
};

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
  established_within_years?: number | null;
  bid_min?: number | null;
  bid_amount_min?: number | null;
  complete?: number;
  tag?: string;
  is_competitor: number;
  source?: string;
  has_contact?: number;
  customer_role?: string;
}

const ESTABLISHED_OPTIONS = [
  { value: 1, label: "成立 1 年内" },
  { value: 3, label: "成立 3 年内" },
  { value: 5, label: "成立 5 年内" },
  { value: 10, label: "成立 10 年内" },
];
const BID_MIN_OPTIONS = [
  { value: 1, label: "中标 ≥ 1 次" },
  { value: 3, label: "中标 ≥ 3 次" },
  { value: 5, label: "中标 ≥ 5 次" },
  { value: 10, label: "中标 ≥ 10 次" },
];
const BID_AMOUNT_OPTIONS = [
  { value: 100, label: "中标额 ≥ 100 万" },
  { value: 500, label: "中标额 ≥ 500 万" },
  { value: 1000, label: "中标额 ≥ 1000 万" },
  { value: 5000, label: "中标额 ≥ 5000 万" },
];

const TIER_TAG: Record<number, "indigo" | "cyan" | "grey"> = { 1: "indigo", 2: "cyan", 3: "grey" };
const TIER_BY_KEY: Record<string, number> = {};
for (const opt of INDUSTRY_OPTIONS) TIER_BY_KEY[opt.key] = opt.tier;

function regionOf(e: Enterprise): string {
  const s = [e.province, e.city].map((x) => (x ?? "").trim()).filter(Boolean).join(" ");
  return s || (e.district ?? "").trim() || "-";
}

export default function EnterprisesPage() {
  const router = useRouter();
  // 初始筛选从 URL 恢复（吸收 twenty useSetViewInUrl：视图状态可分享/可刷新）
  const initFromUrl = (): Filters => {
    if (typeof window === "undefined") return { is_competitor: -1 };
    const sp = new URLSearchParams(window.location.search);
    const f: Filters = { is_competitor: Number(sp.get("is_competitor") ?? -1) };
    const q = sp.get("q");
    if (q) f.q = q;
    const ik = sp.get("industry_key");
    if (ik) f.industry_key = ik;
    const pv = sp.get("province");
    if (pv) f.province = pv;
    const ct = sp.get("city");
    if (ct) f.city = ct;
    const src2 = sp.get("source");
    if (src2) f.source = src2;
    const hc = sp.get("has_contact");
    if (hc) f.has_contact = Number(hc);
    const cr = sp.get("customer_role");
    if (cr) f.customer_role = cr;
    const sm = sp.get("score_min");
    if (sm) f.score_min = Number(sm);
    const ey = sp.get("established_within_years");
    if (ey) f.established_within_years = Number(ey);
    const bm = sp.get("bid_min");
    if (bm) f.bid_min = Number(bm);
    const ba = sp.get("bid_amount_min");
    if (ba) f.bid_amount_min = Number(ba);
    const cp = sp.get("complete");
    if (cp) f.complete = Number(cp);
    const tg = sp.get("tag");
    if (tg) f.tag = tg;
    return f;
  };
  const [filters, setFilters] = useState<Filters>(initFromUrl);
  // SPEC-AM AM1 回收站状态
  const [trashOpen, setTrashOpen] = useState(false);
  const [trashItems, setTrashItems] = useState<{ id: number; name: string; source: string | null; deleted_at: string | null }[] | null>(null);

  const loadTrash = useCallback(async () => {
    try {
      const d = await api.get<{ items: { id: number; name: string; source: string | null; deleted_at: string | null }[] }>("/api/enterprises/trash");
      setTrashItems(d.items);
    } catch {
      setTrashItems([]);
    }
  }, []);
  useEffect(() => {
    if (trashOpen) void loadTrash();
  }, [trashOpen, loadTrash]);
  const restoreOne = useCallback(async (id: number) => {
    try {
      await api.post(`/api/enterprises/${id}/restore`);
      Toast.success("已恢复");
      await loadTrash();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "恢复失败");
    }
  }, [loadTrash]);
  const purgeOne = useCallback(async (id: number) => {
    Modal.confirm({
      title: "彻底删除？",
      content: "该企业的信号/联系人/线索将一并删除，不可恢复。",
      onOk: async () => {
        try {
          await api.delete(`/api/enterprises/${id}/purge?confirm=1`);
          Toast.success("已彻底删除");
          await loadTrash();
        } catch (e) {
          Toast.error(e instanceof Error ? e.message : "删除失败");
        }
      },
    });
  }, [loadTrash]);
  const [applied, setApplied] = useState<Filters>(initFromUrl);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<PageData<Enterprise> | null>(null);
  const [loading, setLoading] = useState(false);
  const fetchSeq = useRef(0);
  const [convertingId, setConvertingId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  // 行内编辑（twenty InlineCell 双态）：editingKey="col:entId"，draft 可取消
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>("");
  const [batching, setBatching] = useState(false);
  // SPEC-Y：批量打标签 Modal + 批量退公海确认
  const [tagModal, setTagModal] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  const [poolConfirm, setPoolConfirm] = useState(false);
  // Pro 表格（ant-design-pro 模式）：列显隐 / 密度 / 保存视图
  // SPEC-CI：更多筛选展开态 + 激活计数
  const [moreOpen, setMoreOpen] = useState(false);
  const moreCount = [
    filters.city, filters.district, filters.capital_min, filters.capital_max,
    filters.signal_days, filters.score_min, filters.established_within_years,
    filters.bid_min, filters.bid_amount_min, filters.tag,
    filters.is_competitor !== undefined && filters.is_competitor !== -1 ? 1 : null,
    filters.source,
  ].filter((v) => v !== null && v !== undefined && v !== "").length;
  // SPEC-CI：默认销售视角——地址/注册资本默认收进列显隐
  const [hiddenCols, setHiddenCols] = useState<string[]>(["address"]);
  const [dense, setDense] = useState(false);
  // 视图 = 完整工作区快照（吸收 twenty View：筛选+列配置+密度+聚合全打包）
  const [views, setViews] = useState<
    { name: string; filters: Filters; hiddenCols: string[]; dense: boolean; colAggs: Record<string, string> }[]
  >(() => {
    if (typeof window === "undefined") return [];
    try {
      const legacy = JSON.parse(localStorage.getItem("vaclead_views") || "[]") as {
        name: string;
        filters: Filters;
      }[];
      const v2 = localStorage.getItem("vaclead_views_v2");
      if (v2) return JSON.parse(v2);
      return legacy.map((v) => ({ ...v, hiddenCols: [], dense: false, colAggs: {} }));
    } catch {
      return [];
    }
  });
  const [viewName, setViewName] = useState("");
  // 高级筛选（twenty advanced-filter 模式：AND/OR 逻辑组 + 条件三元组行）
  const [afOpen, setAfOpen] = useState(false);
  // 列聚合（twenty record-table-footer：点表头选聚合操作，汇总条实时计算）
  const [colAggs, setColAggs] = useState<Record<string, string>>({});


  const saveView = () => {
    const name = viewName.trim();
    if (!name) return;
    const next = [
      ...views.filter((v) => v.name !== name),
      { name, filters: applied, hiddenCols, dense, colAggs },
    ];
    setViews(next);
    localStorage.setItem("vaclead_views_v2", JSON.stringify(next));
    setViewName("");
    Toast.success(`视图「${name}」已保存（含列配置/密度/聚合）`);
  };
  const applyView = (v: {
    name: string;
    filters: Filters;
    hiddenCols: string[];
    dense: boolean;
    colAggs: Record<string, string>;
  }) => {
    setFilters(v.filters);
    setHiddenCols(v.hiddenCols ?? []);
    setDense(v.dense ?? false);
    setColAggs(v.colAggs ?? {});
    setPage(1);
    setApplied(v.filters);
  };
  const removeView = (name: string) => {
    const next = views.filter((v) => v.name !== name);
    setViews(next);
    localStorage.setItem("vaclead_views_v2", JSON.stringify(next));
  };

  const saveInline = async (
    entId: number,
    col: "registered_capital_wan" | "industry_key" | "address",
  ) => {
    const key = `${col}:${entId}`;
    setEditingKey(null);
    let value: number | string | null = draft.trim();
    if (col === "registered_capital_wan") {
      value = draft.trim() === "" ? null : Number(draft.trim());
      if (value !== null && (Number.isNaN(value) || (value as number) < 0 || (value as number) > 10_000_000)) {
        Toast.error("注册资本需在 0-1000 亿元");
        return;
      }
    }
    try {
      await api.patch(`/api/enterprises/${entId}/quick-update`, { field: col, value });
      Toast.success("已更新");
      void fetchData(applied, page);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "更新失败");
    }
  };

  /** 列头聚合菜单（twenty record-table-footer：表头下拉选聚合操作） */
  const aggHeader = (col: string, label: string) => {
    const op = colAggs[col];
    return (
      <span className="inline-flex items-center gap-1">
        {label}
        <Dropdown
          trigger="click"
          position="bottomRight"
          render={
            <Dropdown.Menu>
              {AGG_OPS.map((o) => (
                <Dropdown.Item
                  key={o.value}
                  active={op === o.value}
                  onClick={() => {
                    setColAggs((prev) => {
                      const next = { ...prev };
                      if (o.value === "NONE" || prev[col] === o.value) delete next[col];
                      else next[col] = o.value;
                      return next;
                    });
                  }}
                >
                  {o.label}
                </Dropdown.Item>
              ))}
            </Dropdown.Menu>
          }
        >
          <span
            className={`ml-0.5 inline-flex h-4 w-4 cursor-pointer items-center justify-center rounded text-[10px] leading-none ${
              op ? "bg-[var(--accent)] text-white" : "text-[var(--control)] hover:bg-[var(--raised)]"
            }`}
            title="选择聚合操作"
            onClick={(e) => e.stopPropagation()}
          >
            {op ? AGG_OPS.find((o) => o.value === op)?.label.slice(0, 1) : "Σ"}
          </span>
        </Dropdown>
      </span>
    );
  };

  const COLUMN_LABELS: Record<string, string> = {
    industry_name: "行业", region: "地区", phone: "电话", registered_capital_wan: "注册资本",
    icp_score: "ICP", intent_score: "意图", signal_count: "信号", source: "来源", address: "地址",
  };

  const fetchData = useCallback(async (f: Filters, p: number) => {
    // 请求序号守卫：挂载请求与点击筛选请求几乎同时发出时，慢的旧响应
    // 会覆盖新结果（E2E 旅程复现：筛选后仍显示未筛选计数）——只让最新请求落账
    const seq = ++fetchSeq.current;
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
      if (typeof f.established_within_years === "number") params.set("established_within_years", String(f.established_within_years));
      if (typeof f.bid_min === "number") params.set("bid_min", String(f.bid_min));
      if (typeof f.bid_amount_min === "number") params.set("bid_amount_min", String(f.bid_amount_min));
      if (typeof f.complete === "number") params.set("complete", String(f.complete));
      if (f.tag) params.set("tag", f.tag);
      params.set("is_competitor", String(f.is_competitor));
      if (f.source) params.set("source", f.source);
      if (f.has_contact) params.set("has_contact", "1");
      if (f.customer_role) params.set("customer_role", f.customer_role);
      params.set("page", String(p));
      params.set("page_size", String(PAGE_SIZE));
      const next = await api.get<PageData<Enterprise>>(`/api/enterprises?${params.toString()}`);
      if (seq === fetchSeq.current) setData(next);
    } catch (e) {
      Toast.error(`查询失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      if (seq === fetchSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData(applied, page);
  }, [fetchData, applied, page]);

  // SPEC-V V2：标签聚合（筛选下拉选项）
  const [tagOptions, setTagOptions] = useState<{ tag: string; count: number }[]>([]);
  useEffect(() => {
    api.get<{ items: { tag: string; count: number }[] }>("/api/tags")
      .then((d) => setTagOptions(d.items))
      .catch(() => undefined);
  }, []);

  // URL 状态同步（吸收 twenty useSetViewInUrl）：筛选变化写入 query，可分享/刷新恢复
  useEffect(() => {
    const sp = new URLSearchParams();
    if (applied.q?.trim()) sp.set("q", applied.q.trim());
    if (applied.industry_key) sp.set("industry_key", applied.industry_key);
    if (applied.province?.trim()) sp.set("province", applied.province.trim());
    if (applied.city?.trim()) sp.set("city", applied.city.trim());
    if (applied.source) sp.set("source", applied.source);
    if (applied.has_contact) sp.set("has_contact", String(applied.has_contact));
    if (applied.customer_role) sp.set("customer_role", applied.customer_role);
    if (typeof applied.score_min === "number") sp.set("score_min", String(applied.score_min));
    if (typeof applied.established_within_years === "number") sp.set("established_within_years", String(applied.established_within_years));
    if (typeof applied.bid_min === "number") sp.set("bid_min", String(applied.bid_min));
    if (typeof applied.bid_amount_min === "number") sp.set("bid_amount_min", String(applied.bid_amount_min));
    if (applied.tag) sp.set("tag", applied.tag);
    if (applied.is_competitor !== -1) sp.set("is_competitor", String(applied.is_competitor));
    const qs = sp.toString();
    window.history.replaceState(null, "", qs ? `/enterprises?${qs}` : "/enterprises");
  }, [applied]);

  const set = (patch: Partial<Filters>) => setFilters((prev) => ({ ...prev, ...patch }));

  const convert = async (e: Enterprise) => {
    setConvertingId(e.id);
    try {
      await api.post(`/api/enterprises/${e.id}/lead`);
      Toast.success(`已转为线索：${e.name}`);
      void fetchData(applied, page);
    } catch (err) {
      Toast.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setConvertingId(null);
    }
  };

  const batchConvert = async () => {
    if (selectedIds.length === 0) return;
    setBatching(true);
    try {
      const r = await api.post<{ converted: number; existed: number; skipped_competitor: number }>(
        "/api/enterprises/batch-lead",
        { ids: selectedIds },
      );
      Toast.success(
        `批量完成：转入 ${r.converted}，已有 ${r.existed}，同行跳过 ${r.skipped_competitor}`,
      );
      setSelectedIds([]);
      void fetchData(applied, page);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "批量失败");
    } finally {
      setBatching(false);
    }
  };

  // SPEC-Y Y1：批量打标签（append 合并，不覆盖已有）
  const batchTags = async () => {
    if (selectedIds.length === 0 || !tagDraft.trim()) return;
    setBatching(true);
    try {
      const tags = tagDraft.split(/[,，;；\s]+/).map((t) => t.trim()).filter(Boolean);
      const r = await api.post<{ updated: number; skipped: { id: number; reason: string }[] }>(
        "/api/enterprises/batch-tags",
        { ids: selectedIds, tags, mode: "append" },
      );
      const skippedMsg = r.skipped.length
        ? `，跳过 ${r.skipped.length}（${r.skipped[0].reason}）`
        : "";
      Toast.success(`已为 ${r.updated} 家打上标签${skippedMsg}`);
      setTagModal(false);
      setTagDraft("");
      setSelectedIds([]);
      void fetchData(applied, page);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "打标签失败");
    } finally {
      setBatching(false);
    }
  };

  // SPEC-Y Y2：批量退公海（确认后执行）
  const batchPool = async () => {
    if (selectedIds.length === 0) return;
    setBatching(true);
    try {
      const r = await api.post<{ moved: number; skipped: { id: number; reason: string }[] }>(
        "/api/leads/batch-pool",
        { ids: selectedIds },
      );
      const skippedMsg = r.skipped.length ? `，跳过 ${r.skipped.length}` : "";
      Toast.success(`已退回公海 ${r.moved} 家${skippedMsg}`);
      setPoolConfirm(false);
      setSelectedIds([]);
      void fetchData(applied, page);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "批量退公海失败");
    } finally {
      setBatching(false);
    }
  };

  const exportUrl = () => {
    const params = new URLSearchParams();
    const q = applied.q?.trim();
    if (q) params.set("q", q);
    if (applied.industry_key) params.set("industry_key", applied.industry_key);
    const province = applied.province?.trim();
    if (province) params.set("province", province);
    if (applied.source) params.set("source", applied.source);
    if (typeof applied.established_within_years === "number") params.set("established_within_years", String(applied.established_within_years));
    if (typeof applied.bid_min === "number") params.set("bid_min", String(applied.bid_min));
    if (typeof applied.bid_amount_min === "number") params.set("bid_amount_min", String(applied.bid_amount_min));
    if (applied.tag) params.set("tag", applied.tag);
    params.set("is_competitor", String(applied.is_competitor));
    if (typeof window === "undefined") return "#";  // SSG 预渲染守卫
    return `${window.location.port === "3000" ? "http://127.0.0.1:8000" : ""}/api/enterprises/export?${params.toString()}`;
  };

  const columns = [
    {
      title: "企业名称",
      dataIndex: "name",
      width: 260,
      render: (_: unknown, r: Enterprise) => (
        <>
          <button
            onClick={() => router.push(`/enterprises/view?id=${r.id}`)}
            className="block max-w-[240px] cursor-pointer truncate border-0 bg-transparent p-0 text-left text-[13.5px] font-medium text-[var(--ink)] hover:text-[var(--accent)]"
            title={`${r.name} · 打开完整情报页`}
          >
            {r.name}
          </button>
          {/* SPEC-U U3：资料完整度徽标（法人/成立日期/注册资本/经营范围 四点） */}
          <span className="mt-0.5 flex items-center gap-0.5" title="资料完整度：法人/成立日期/注册资本/经营范围">
            {[0, 1, 2, 3].map((i) => (
              <span
                key={i}
                className={`inline-block h-1.5 w-3 rounded-sm ${i < (r.completeness ?? 0) ? "bg-[var(--accent)]" : "bg-[var(--rule)]"}`}
              />
            ))}
          </span>
          {/* SPEC-V V2：标签徽标（最多 2 个 + n） */}
          {(() => {
            const parsed = r.tags ?? [];
            if (parsed.length === 0) return null;
            return (
              <span className="mt-0.5 flex flex-wrap items-center gap-1">
                {parsed.slice(0, 2).map((t) => (
                  <Tag key={t} color="light-blue" type="light" size="small">{t}</Tag>
                ))}
                {parsed.length > 2 && (
                  <span className="text-[11px] text-[var(--muted)]">+{parsed.length - 2}</span>
                )}
              </span>
            );
          })()}
        </>
      ),
    },
    {
      title: "行业",
      dataIndex: "industry_name",
      width: 150,
      render: (_: unknown, r: Enterprise) => {
        if (r.is_competitor === 1) {
          return <Tag color="amber" type="light">疑似同行</Tag>;
        }
        const key = `industry_key:${r.id}`;
        if (editingKey === key) {
          return (
            <span className="inline-cell" onClick={(e) => e.stopPropagation()}>
              <select
                autoFocus
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setTimeout(() => void saveInline(r.id, "industry_key"), 50);
                }}
                onBlur={() => setEditingKey(null)}
                className="w-full bg-transparent text-[12.5px] text-[var(--ink)] outline-none"
              >
                <option value="">未分类</option>
                {INDUSTRY_OPTIONS.map((i) => (
                  <option key={i.key} value={i.key}>{i.name}</option>
                ))}
              </select>
            </span>
          );
        }
        return (
          <span
            className="inline-cell"
            title="点击编辑行业"
            onClick={(e) => {
              e.stopPropagation();
              setDraft(r.industry_key ?? "");
              setEditingKey(key);
            }}
          >
            <Tag color={TIER_TAG[TIER_BY_KEY[r.industry_key ?? ""] ?? 3]} type="light">
              {r.industry_name || "未分类"}
            </Tag>
            <Pencil size={11} className="inline-cell-hint" />
          </span>
        );
      },
    },
    {
      title: "地区",
      width: 120,
      render: (_: unknown, r: Enterprise) => <span className="text-[var(--muted)]">{regionOf(r)}</span>,
    },

    {
      title: "电话",
      dataIndex: "phone",
      width: 170,
      render: (_: unknown, r: Enterprise) =>
        r.phone ? (
          <span
            className="inline-flex cursor-pointer items-center rounded border border-[var(--border)] bg-[var(--raised)] px-1.5 py-0.5 font-mono text-[12.5px] tabular-nums text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white"
            title="点击复制"
            onClick={(e) => {
              e.stopPropagation();
              void navigator.clipboard?.writeText(r.phone ?? "");
              Toast.success(`已复制 ${r.phone}`);
            }}
          >
            {r.phone}
          </span>
        ) : (
          <span className="text-[var(--control)]">—</span>
        ),
    },
    {
      title: "地址",
      dataIndex: "address",
      width: 170,
      render: (v: string | null, r: Enterprise) => {
        const key = `address:${r.id}`;
        if (editingKey === key) {
          return (
            <span className="inline-cell" onClick={(e) => e.stopPropagation()}>
              <input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void saveInline(r.id, "address");
                  if (e.key === "Escape") setEditingKey(null);
                }}
                onBlur={() => void saveInline(r.id, "address")}
                className="w-full bg-transparent text-[13px] text-[var(--ink)] outline-none"
              />
            </span>
          );
        }
        return (
          <span
            className="inline-cell"
            title="点击编辑地址"
            onClick={(e) => {
              e.stopPropagation();
              setDraft(v ?? "");
              setEditingKey(key);
            }}
          >
            <span className="max-w-[140px] truncate text-[var(--muted)]">{v || "—"}</span>
            <Pencil size={11} className="inline-cell-hint" />
          </span>
        );
      },
    },
    {
      title: aggHeader("registered_capital_wan", "注册资本（万）"),
      dataIndex: "registered_capital_wan",
      width: 150,
      align: "right" as const,
      render: (v: number | null, r: Enterprise) => {
        const key = `registered_capital_wan:${r.id}`;
        if (editingKey === key) {
          return (
            <span className="inline-cell" onClick={(e) => e.stopPropagation()}>
              <input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void saveInline(r.id, "registered_capital_wan");
                  if (e.key === "Escape") setEditingKey(null);
                }}
                onBlur={() => void saveInline(r.id, "registered_capital_wan")}
                className="w-full bg-transparent text-right text-[13px] tabular-nums text-[var(--ink)] outline-none"
              />
            </span>
          );
        }
        return (
          <span
            className="inline-cell justify-end"
            title="点击编辑"
            onClick={(e) => {
              e.stopPropagation();
              setDraft(v == null ? "" : String(v));
              setEditingKey(key);
            }}
          >
            <span className="tabular-nums text-[var(--muted)]">
              {v == null ? "—" : v.toLocaleString("zh-CN")}
            </span>
            <Pencil size={11} className="inline-cell-hint" />
          </span>
        );
      },
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
      title: aggHeader("intent_score", "意图"),
      dataIndex: "intent_score",
      width: 70,
      align: "right" as const,
      sorter: (a: Enterprise, b: Enterprise) => a.intent_score - b.intent_score,
      render: (v: number) => (
        <span className={`tabular-nums ${v > 0 ? "font-medium text-[var(--accent)]" : "text-[#9ca0a3]"}`}>{v}</span>
      ),
    },
    {
      title: aggHeader("signal_count", "信号"),
      dataIndex: "signal_count",
      width: 64,
      align: "right" as const,
      render: (v: number) => (v > 0 ? <span className="font-medium tabular-nums text-[var(--negative)]">{v}</span> : <span className="tabular-nums text-[#9ca0a3]">0</span>),
    },
    {
      title: "来源",
      dataIndex: "source",
      width: 96,
      render: (_: unknown, r: Enterprise) => {
        const meta = SOURCE_TAG[r.source ?? ""] ?? { label: r.source || "—", color: "grey" as const };
        return <Tag color={meta.color} type="light" size="small">{meta.label}</Tag>;
      },
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
          <h1 className="text-[22px] font-semibold leading-7 text-[var(--ink)]">企业列表</h1>
          <p className="mt-1 text-[13.5px] text-[var(--muted)]">
            {loading ? "加载中…" : `共 ${data?.total ?? 0} 家 · 疑似同行已置灰 · 点击企业名进入完整情报页`}
          </p>
        </div>
        {typeof window !== "undefined" && localStorage.getItem("vaclead_role") === "admin" && (
          <Button
            theme="borderless"
            onClick={() => setTrashOpen(true)}
          >
            回收站
          </Button>
        )}
      </div>

      {/* SPEC-AM AM1 回收站（admin） */}
      <Modal
        title="回收站"
        visible={trashOpen}
        onCancel={() => setTrashOpen(false)}
        footer={null}
        width={560}
      >
        <div className="space-y-1.5">
          {(trashItems ?? []).length === 0 && (
            <div className="py-6 text-center text-[13px] text-[var(--muted)]">
              回收站为空 —— 删除的企业会在这里保留 30 天
            </div>
          )}
          {(trashItems ?? []).map((t) => (
            <div key={t.id} className="flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium text-[var(--ink)]">{t.name}</div>
                <div className="text-[11.5px] text-[var(--muted)]">
                  {t.source ?? "—"} · 删除于 {String(t.deleted_at ?? "").slice(0, 16).replace("T", " ")}
                </div>
              </div>
              <Button size="small" theme="borderless" onClick={() => void restoreOne(t.id)}>恢复</Button>
              <Button size="small" theme="borderless" type="danger" onClick={() => void purgeOne(t.id)}>彻底删</Button>
            </div>
          ))}
        </div>
      </Modal>

      <SummaryBar data={data} colAggs={colAggs} />

      <QuickPicks
        filters={filters}
        applied={applied}
        onApply={(next) => {
          setFilters(next);
          setPage(1);
          setApplied(next);
        }}
      />

      {/* 保存视图 chips（ant-design-pro 查询方案模式） */}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <div className="flex items-center gap-1.5">
          <input
            value={viewName}
            onChange={(e) => setViewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.keyCode === 13) saveView();
            }}
            placeholder="当前筛选存为视图…"
            className="h-7 w-40 rounded border border-[var(--border)] bg-white px-2 text-[12px] outline-none focus:border-[var(--accent)]"
          />
          <button
            onClick={saveView}
            className="flex h-7 cursor-pointer items-center gap-1 rounded border-0 bg-transparent px-1.5 text-[12px] text-[var(--muted)] hover:text-[var(--accent)]"
          >
            <Save size={12} /> 保存
          </button>
        </div>
        {views.map((v) => (
          <span
            key={v.name}
            className={`inline-flex h-7 cursor-pointer items-center gap-1 rounded-full border px-2.5 text-[12px] ${
              JSON.stringify(v.filters) === JSON.stringify(applied) &&
                JSON.stringify(v.hiddenCols ?? []) === JSON.stringify(hiddenCols) &&
                v.dense === dense
                ? "border-[var(--accent)] bg-[color:var(--accent)]/5 text-[var(--accent)]"
                : "border-[var(--border)] bg-white text-[var(--ink)] hover:border-[var(--accent)]"
            }`}
            onClick={() => applyView(v)}
          >
            {v.name}
            <X
              size={11}
              className="text-[var(--muted)] hover:text-[var(--negative)]"
              onClick={(e) => {
                e.stopPropagation();
                removeView(v.name);
              }}
            />
          </span>
        ))}
      </div>

      {selectedIds.length > 0 && (
        <div className="mt-3 flex items-center gap-3 rounded-lg border border-[var(--border)] bg-white px-4 py-2.5">
          <span className="text-[13px] text-[var(--ink)]">已选 {selectedIds.length} 家</span>
          <Button theme="solid" size="small" loading={batching} onClick={batchConvert}>
            批量转线索
          </Button>
          <Button size="small" onClick={() => setTagModal(true)}>打标签</Button>
          <Button
            size="small"
            onClick={() => {
              const results = selectedIds.map((id) => addToCompare(id));
              const ok = results.filter((r) => r.ok).length;
              const firstFail = results.find((r) => !r.ok);
              if (ok) Toast.success(`已加入对比 ${ok} 家（${readCompare().length}/4）`);
              if (firstFail?.reason) Toast.warning(firstFail.reason);
            }}
          >
            加入对比
          </Button>
          <Button size="small" type="warning" theme="light" onClick={() => setPoolConfirm(true)}>
            批量退公海
          </Button>
          <Button size="small" theme="borderless" type="tertiary" onClick={() => setSelectedIds([])}>
            清除选择
          </Button>
        </div>
      )}

      {/* SPEC-Y Y1：批量打标签 Modal */}
      <Modal
        title={`为 ${selectedIds.length} 家企业打标签（append 合并，不覆盖已有）`}
        visible={tagModal}
        onOk={batchTags}
        onCancel={() => setTagModal(false)}
        okText="打标签"
        width={420}
      >
        <Input
          placeholder="多个标签用逗号/空格分隔，如：重点客户, 圈层A"
          value={tagDraft}
          onChange={setTagDraft}
        />
        <div className="mt-2 text-[12px] text-[var(--muted)]">
          合并后每家总数 ≤8 个，超限的企业会被跳过并列出。
        </div>
      </Modal>

      {/* SPEC-Y Y2：批量退公海确认 */}
      <Modal
        title="批量退公海"
        visible={poolConfirm}
        onOk={batchPool}
        onCancel={() => setPoolConfirm(false)}
        okText="确认退回"
        okButtonProps={{ type: "danger" }}
        width={420}
      >
        <div className="text-[13.5px] leading-6 text-[var(--ink)]">
          将选中的 {selectedIds.length} 家企业的线索退回公海（释放归属）。
          <div className="mt-1.5 text-[12px] text-[var(--muted)]">
            仅跟进中/意向状态可退；已赢单/输单/公海的会被跳过并列出。
          </div>
        </div>
      </Modal>

      {/* SPEC-CI 工具行收敛：搜索先行，11 个次要维度收进「更多筛选」展开行 */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Input
          style={{ width: 250 }}
          prefix={<Search size={13} className="text-[#9ca0a3]" />}
          placeholder="名称 / 经营范围关键词，回车查"
          value={filters.q}
          onChange={(v) => set({ q: v })}
          onKeyDown={(e) => {
            if (e.keyCode === 13) {
              setPage(1);
              setApplied(filters);
            }
          }}
        />
        <Select style={ctl} optionList={INDUSTRY_SELECT} placeholder="全部行业" filter showClear value={filters.industry_key} onChange={(v) => set({ industry_key: (v as string) || undefined })} />
        <Select style={ctl} optionList={PROVINCE_OPTIONS} placeholder="全部省份" filter showClear value={filters.province} onChange={(v) => set({ province: (v as string) || undefined })} />
        <Button theme="solid" className="!h-8" onClick={() => { setPage(1); setApplied(filters); }}>
          查询
        </Button>
        <Button
          theme="borderless"
          type="tertiary"
          className="!h-8 !px-2 text-[12.5px]"
          icon={<ChevronDown size={13} className={moreOpen ? "rotate-180 transition-transform" : "transition-transform"} />}
          onClick={() => setMoreOpen(!moreOpen)}
        >
          更多筛选{moreCount > 0 ? ` · ${moreCount}` : ""}
        </Button>
        <a href={exportUrl()} target="_blank" rel="noreferrer" className="no-underline">
          <Button theme="outline" className="!h-8">导出 CSV</Button>
        </a>
        <Button theme="borderless" type="tertiary" className="!h-8 !px-2 text-[12px]" onClick={() => setAfOpen(true)}>
          高级筛选
        </Button>
        <Popover
          trigger="click"
          position="bottomRight"
          content={
            <div className="w-44 space-y-1 p-2">
              <div className="px-2 pb-1 text-[11.5px] text-[var(--muted)]">列显示</div>
              {Object.entries(COLUMN_LABELS).map(([key, label]) => (
                <Checkbox
                  key={key}
                  checked={!hiddenCols.includes(key)}
                  onChange={(e) =>
                    setHiddenCols((prev) =>
                      e.target.checked ? prev.filter((k) => k !== key) : [...prev, key],
                    )
                  }
                >
                  <span className="text-[13px]">{label}</span>
                </Checkbox>
              ))}
            </div>
          }
        >
          <Button theme="borderless" type="tertiary" icon={<Settings2 size={14} />} className="!h-8" />
        </Popover>
        <Button
          theme="borderless"
          type="tertiary"
          className="!h-8 !px-2 text-[12px]"
          onClick={() => setDense((d) => !d)}
        >
          {dense ? "宽松" : "紧凑"}
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

      {moreOpen && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-[var(--border)] bg-white px-3 py-2.5">
          <Input style={{ width: 104 }} placeholder="城市" value={filters.city} onChange={(v) => set({ city: v })} />
          <Input style={{ width: 104 }} placeholder="区县" value={filters.district} onChange={(v) => set({ district: v })} />
          <div className="flex items-center gap-1">
            <InputNumber style={{ width: 84 }} placeholder="资本≥" min={0} value={filters.capital_min ?? undefined} onChange={(v) => set({ capital_min: typeof v === "number" ? v : null })} />
            <span className="text-[var(--control)]">—</span>
            <InputNumber style={{ width: 84 }} placeholder="≤" min={0} value={filters.capital_max ?? undefined} onChange={(v) => set({ capital_max: typeof v === "number" ? v : null })} />
          </div>
          <Select style={ctl} optionList={SIGNAL_DAY_OPTIONS} placeholder="信号不限" showClear value={filters.signal_days ?? undefined} onChange={(v) => set({ signal_days: typeof v === "number" ? v : null })} />
          <Select style={ctl} optionList={SCORE_MIN_OPTIONS} placeholder="评分不限" showClear value={filters.score_min ?? undefined} onChange={(v) => set({ score_min: typeof v === "number" ? v : null })} />
          <Select style={ctl} optionList={ESTABLISHED_OPTIONS} placeholder="成立年限不限" showClear value={filters.established_within_years ?? undefined} onChange={(v) => set({ established_within_years: typeof v === "number" ? v : null })} />
          <Select style={ctl} optionList={BID_MIN_OPTIONS} placeholder="中标次数不限" showClear value={filters.bid_min ?? undefined} onChange={(v) => set({ bid_min: typeof v === "number" ? v : null })} />
          <Select style={ctl} optionList={BID_AMOUNT_OPTIONS} placeholder="中标额不限" showClear value={filters.bid_amount_min ?? undefined} onChange={(v) => set({ bid_amount_min: typeof v === "number" ? v : null })} />
          <Select style={ctl} placeholder="标签不限" showClear filter value={filters.tag ?? undefined} onChange={(v) => set({ tag: (v as string) || undefined })}>
            {tagOptions.map((t) => (
              <Select.Option key={t.tag} value={t.tag}>
                {t.tag} ({t.count})
              </Select.Option>
            ))}
          </Select>
          <Select style={ctl} optionList={COMPETITOR_OPTIONS} value={filters.is_competitor} onChange={(v) => set({ is_competitor: (v as number) ?? -1 })} />
          <Select style={ctl} optionList={SOURCE_OPTIONS} placeholder="全部来源" showClear value={filters.source ?? undefined} onChange={(v) => set({ source: (v as string) || undefined })} />
          <Button size="small" theme="outline" onClick={() => { setPage(1); setApplied(filters); }}>应用</Button>
        </div>
      )}

      {/* 表格直接落在纸上 */}
      <div className="mt-4">
        <Table<Enterprise>
          rowKey="id"
              rowSelection={{
                selectedRowKeys: selectedIds,
                onChange: (keys) => setSelectedIds((keys ?? []).map(Number)),
              }}
          columns={(columns as unknown as Record<string, unknown>[]).filter(
            (c) => !hiddenCols.includes(String((c as { dataIndex?: string }).dataIndex)),
          ) as never}
          dataSource={data?.items ?? []}
          loading={loading}
          size={dense ? "small" : "middle"}
          empty={loading ? " " : "没有匹配的企业 —— 放宽筛选条件，或先去「拓客任务」采集数据"}
          onRow={(r) => ({ className: r?.is_competitor === 1 ? "opacity-45 saturate-50" : "" })}
          pagination={{
            currentPage: page,
            pageSize: PAGE_SIZE,
            total: data?.total ?? 0,
            onPageChange: (p: number) => setPage(p),
          }}
        />
      </div>
      <AdvancedFilterModal
        open={afOpen}
        onClose={() => setAfOpen(false)}
        onSendToTable={(name) => {
          const next = { ...filters, q: name };
          setFilters(next);
          setPage(1);
          setApplied(next);
        }}
      />

    </AppShell>
  );
}
