"use client";

/**
 * 标讯中心（SPEC-M M1）：全局信号流——平台门面页。
 * 类型/省份/行业/关键词/时间窗多维筛选 + facet 计数；
 * 行操作：进企业情报页 / 一键订阅该企业 / 转线索（非同行）。
 * 参照形态调研（探迹招投标/剑鱼/千里马）：筛选+订阅+标讯→客户打通。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, Select, Tag, Toast } from "@douyinfe/semi-ui";
import { Bell, Crosshair, Search, Sprout, Target, UserPlus } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { INDUSTRY_OPTIONS } from "@/lib/constants";

interface SignalRow {
  id: number;
  type: string;
  type_cn: string;
  title: string;
  url: string | null;
  ent_id: number;
  ent_name: string;
  ent_industry: string | null;
  is_competitor: number;
  province: string | null;
  amount_wan: number | null;
  pub_date: string | null;
  created_at: string;
  evidence_claim: string | null;
  base_score: number;
}

const TYPE_TAG: Record<string, { label: string; color: "cyan" | "green" | "blue" | "amber" }> = {
  tender_win: { label: "中标", color: "cyan" },
  tender_buy: { label: "公开采购", color: "green" },
  tender: { label: "政采公告", color: "blue" },
  proposal: { label: "项目备案", color: "amber" },
};

const PROVINCES = ["江苏省", "广东省", "北京市", "上海市", "浙江省", "山东省", "河南省", "四川省", "湖北省", "湖南省", "安徽省", "陕西省", "福建省", "河北省", "辽宁省"];

export default function SignalsPage() {
  const [items, setItems] = useState<SignalRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [facets, setFacets] = useState<{ type: Record<string, number>; province: Record<string, number> } | null>(null);
  const [type, setType] = useState<string>("");
  const [province, setProvince] = useState<string>("");
  const [industry, setIndustry] = useState<string>("");
  const [q, setQ] = useState("");
  const [days, setDays] = useState<number>(30);
  const [competitor, setCompetitor] = useState<number | null>(null);
  const [subscribing, setSubscribing] = useState<number | null>(null);
  // SPEC-AMT 排序：""=最新动态（id desc）| amount=金额最大（后端持久列 SQL 排序）
  // URL ?order=amount 恢复（enterprises 页 initFromUrl 同款轻量版，只管 order）
  const initOrder = (): string => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("order") === "amount" ? "amount" : "";
  };
  const [order, setOrder] = useState<string>(initOrder);

  const load = useCallback(() => {
    const p = new URLSearchParams({ page: String(page), page_size: "20", facets: "1" });
    if (type) p.set("type", type);
    if (province) p.set("province", province);
    if (industry) p.set("industry_key", industry);
    if (q.trim()) p.set("q", q.trim());
    if (days) p.set("days", String(days));
    if (competitor != null) p.set("competitor", String(competitor));
    if (order) p.set("order", order);
    api.get<{ items: SignalRow[]; total: number; facets: typeof facets }>(`/api/signals?${p}`)
      .then((d) => {
        setItems(d.items);
        setTotal(d.total);
        setFacets(d.facets);
      })
      .catch(() => undefined);
  }, [page, type, province, industry, q, days, competitor, order]);
  useEffect(load, [load]);

  // SPEC-AMT：order 同步 URL（replaceState 静默写，可分享/刷新返回不丢，不触发重拉）
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    if (order) sp.set("order", order);
    else sp.delete("order");
    const qs = sp.toString();
    const target = `${window.location.pathname}${qs ? `?${qs}` : ""}`;
    if (window.location.pathname + window.location.search !== target) {
      window.history.replaceState(null, "", target);
    }
  }, [order]);

  const subscribe = async (entId: number) => {
    setSubscribing(entId);
    try {
      await api.post("/api/subscriptions", { enterprise_id: entId });
      Toast.success("已订阅该企业，新信号将通知你");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "订阅失败（可能已订阅）");
    } finally {
      setSubscribing(null);
    }
  };
  const toLead = async (entId: number) => {
    try {
      await api.post(`/api/enterprises/${entId}/lead`, {});
      Toast.success("已转线索，去线索看板跟进");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "转线索失败");
    }
  };
  // SPEC-O O2：把当前筛选存为订阅规则（每日新信号命中即通知）
  const subscribeFilter = async () => {
    const cond: Record<string, unknown> = {};
    if (type) cond.type = type;
    if (province) cond.province = province;
    if (industry) cond.industry_key = industry;
    if (q.trim()) cond.q = q.trim();
    if (days) cond.days = days;
    try {
      await api.post("/api/subscription-rules", { kind: "filter", value: JSON.stringify(cond) });
      Toast.success("已订阅当前筛选，每日新信号命中后将通知你");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "订阅失败");
    }
  };

  const typeCount = (t: string) => facets?.type?.[t] ?? 0;

  // SPEC-AB：拟建/备案 = 扩张前兆（培育线索，非即时行动）
  const toggleProposal = () => {
    setType(type === "proposal" ? "" : "proposal");
    setPage(1);
  };

  // SPEC-AA AA2：行动窗口快捷筛选（数据已在库，纯视图切换）
  const toggleCompetitor = () => {
    setCompetitor(competitor === 1 ? null : 1);
    setPage(1);
  };

  return (
    <AppShell>
      {/* 行动窗口快捷 chip（SPEC-AA AA2） */}
      <div className="mb-3 flex flex-nowrap items-center gap-2 overflow-x-auto [scrollbar-width:none] [-&::-webkit-scrollbar]:hidden md:flex-wrap md:overflow-visible">
        <button
          onClick={() => { setType(type === "tender" ? "" : "tender"); setPage(1); }}
          className={`shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-[12.5px] ${
            type === "tender"
              ? "border-[#c2801b] bg-[#c2801b] text-white"
              : "border-[var(--border)] bg-white text-[var(--ink)]"
          }`}
          title="招标公告/竞争性磋商/谈判——采购正在发生，行动窗口"
        >
          <Target size={12} className="mr-1 inline" />
          招标中 · 行动窗口
        </button>
        <button
          onClick={toggleProposal}
          className={`shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-[12.5px] ${
            type === "proposal"
              ? "border-[#c2801b] bg-[#c2801b] text-white"
              : "border-[var(--border)] bg-white text-[var(--ink)]"
          }`}
          title="项目备案公示——扩张前兆，比招标早 1-3 个月（培育线索）"
        >
          <Sprout size={12} className="mr-1 inline" />
          拟建·备案
        </button>
        <button
          onClick={toggleCompetitor}
          className={`shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-[12.5px] ${
            competitor === 1
              ? "border-[#c0392b] bg-[#c0392b] text-white"
              : "border-[var(--border)] bg-white text-[var(--ink)]"
          }`}
          title="竞对（设备制造商）中标动态——他们刚拿下谁的订单"
        >
          <Crosshair size={12} className="mr-1 inline" />
          竞对中标动态
        </button>
      </div>
      {/* 筛选条 */}
      <div className="mb-4 flex flex-nowrap items-center gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [-&::-webkit-scrollbar]:hidden md:flex-wrap md:overflow-visible">
        <Input
          prefix={<Search size={14} className="text-slate-400" />}
          placeholder="搜标题关键词，如 分子泵 / 监护仪"
          value={q}
          onChange={setQ}
          showClear
          className="!w-64 shrink-0"
        />
        <Select value={type || undefined} placeholder="全部类型" showClear onChange={(v) => { setType(String(v ?? "")); setPage(1); }} className="!w-36 shrink-0">
          <Select.Option value="tender_win">中标 ({typeCount("tender_win")})</Select.Option>
          <Select.Option value="tender_buy">公开采购 ({typeCount("tender_buy")})</Select.Option>
          <Select.Option value="tender">政采公告 ({typeCount("tender")})</Select.Option>
          <Select.Option value="proposal">项目备案 ({typeCount("proposal")})</Select.Option>
        </Select>
        <Select value={province || undefined} placeholder="全部省份" showClear filter onChange={(v) => { setProvince(String(v ?? "")); setPage(1); }} className="!w-32 shrink-0">
          {PROVINCES.map((p) => (
            <Select.Option key={p} value={p}>
              {p} {facets?.province?.[p] ? `(${facets.province[p]})` : ""}
            </Select.Option>
          ))}
        </Select>
        <Select value={industry || undefined} placeholder="全部行业" showClear filter onChange={(v) => { setIndustry(String(v ?? "")); setPage(1); }} className="!w-40 shrink-0">
          {INDUSTRY_OPTIONS.map((o) => (
            <Select.Option key={o.key} value={o.key}>{o.name}</Select.Option>
          ))}
        </Select>
        <Select value={String(days)} onChange={(v) => { setDays(Number(v)); setPage(1); }} className="!w-28 shrink-0">
          <Select.Option value="7">近 7 天</Select.Option>
          <Select.Option value="30">近 30 天</Select.Option>
          <Select.Option value="90">近 90 天</Select.Option>
          <Select.Option value="0">全部</Select.Option>
        </Select>
        {/* SPEC-AMT 排序：金额最大走后端持久列 SQL 排序（无金额排尾） */}
        <Select value={order || ""} onChange={(v) => { setOrder(String(v ?? "")); setPage(1); }} className="!w-32 shrink-0">
          <Select.Option value="">最新动态</Select.Option>
          <Select.Option value="amount">金额最大</Select.Option>
        </Select>
        <span className="shrink-0 whitespace-nowrap text-[12.5px] text-[var(--muted)]">共 {total} 条标讯</span>
        <Button
          size="small"
          icon={<Bell size={13} />}
          onClick={subscribeFilter}
          disabled={!type && !province && !industry && !q.trim()}
          className="shrink-0 !whitespace-nowrap"
        >
          订阅当前筛选
        </Button>
      </div>

      {/* 信号流 */}
      <div className="space-y-2.5">
        {items.map((s) => {
          const tag = TYPE_TAG[s.type] ?? { label: s.type_cn, color: "blue" as const };
          return (
            <div key={s.id} className="rounded-lg border border-[var(--border)] bg-white px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Tag color={tag.color} type="light" size="small">{tag.label}</Tag>
                    {s.amount_wan != null && (
                      <span className="text-[13px] font-medium tabular-nums text-[#c2801b]">
                        {s.amount_wan.toLocaleString()} 万
                      </span>
                    )}
                    {s.pub_date && <span className="text-[12px] text-[var(--muted)]">{s.pub_date}</span>}
                    {s.province && <span className="text-[12px] text-[var(--muted)]">· {s.province}</span>}
                  </div>
                  <div className="mt-1 flex items-center gap-1.5">
                    <span className="truncate text-[14px] font-medium text-[var(--ink)]" title={s.title}>
                      {s.title}
                    </span>
                    {s.url && (
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex shrink-0 items-center py-2 pl-1 text-[11.5px] text-[var(--accent)] hover:underline"
                        title="查看官方公示原文"
                      >
                        原文 ↗
                      </a>
                    )}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-[13px]">
                    <a href={`/enterprises/view?id=${s.ent_id}`} className="inline-flex items-center py-1.5 text-[var(--accent)] hover:underline">
                      {s.ent_name}
                    </a>
                    {s.is_competitor === 1 && <Tag color="red" type="light" size="small">疑似同行</Tag>}
                  </div>
                  {s.evidence_claim && (
                    <div className="mt-1 border-l-2 border-[var(--accent)] pl-2 text-[12px] leading-4 text-[var(--muted)]">
                      {s.evidence_claim}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1.5">
                  <Button size="small" className="!whitespace-nowrap" icon={<Bell size={13} />} loading={subscribing === s.ent_id} onClick={() => subscribe(s.ent_id)}>
                    订阅
                  </Button>
                  {s.is_competitor !== 1 && (
                    <Button size="small" theme="solid" className="!whitespace-nowrap" icon={<UserPlus size={13} />} onClick={() => toLead(s.ent_id)}>
                      转线索
                    </Button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
        {items.length === 0 && (
          <div className="py-16 text-center text-[13px] text-[var(--muted)]">
            当前筛选下暂无标讯 —— 放宽时间窗或清空关键词试试
          </div>
        )}
      </div>

      {/* 分页 */}
      {total > 20 && (
        <div className="mt-4 flex items-center justify-center gap-3">
          <Button size="small" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button>
          <span className="text-[12.5px] text-[var(--muted)]">
            第 {page} / {Math.ceil(total / 20)} 页
          </span>
          <Button size="small" disabled={page >= Math.ceil(total / 20)} onClick={() => setPage(page + 1)}>下一页</Button>
        </div>
      )}
    </AppShell>
  );
}
