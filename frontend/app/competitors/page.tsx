"use client";

/**
 * 竞对雷达（SPEC-BN）：组合级竞对监测——谁家的蛋糕被竞对抢了。
 * 对标剑鱼"竞对监测"付费功能（PRODUCT_GAP §5.3 #3）：is_competitor 的
 * 信号早已在库，本页是专门视图：KPI 晨检卡 + 竞对动态流 + 竞对榜单 +
 * admin 手动标记（此前只有自动化 tag_competitor 入口）。
 * 金额/买家从公告文本回抽（与 intel/reports 同源正则），可溯源不发明。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, Select, Tag, Toast } from "@douyinfe/semi-ui";
import { Crosshair, Search, UserX } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface Overview {
  competitor_total: number;
  signals_30d: number;
  wins_30d: number;
  win_amount_30d_wan: number;
  lost_to_competitor: number;
  lost_amount_wan: number;
  top_competitor: string;
}

interface FeedItem {
  id: number;
  enterprise_id: number;
  enterprise_name: string;
  type: string;
  title: string;
  pub_date: string;
  url: string;
  amount_wan: number;
  stolen_from: string;
}

interface RankItem {
  id: number;
  name: string;
  industry_name: string | null;
  signal_count: number;
  win_count: number;
  win_amount_total_wan: number;
  wins_30d: number;
  last_signal_date: string;
  top_buyer: string;
}

const TYPE_TAG: Record<string, { label: string; color: "cyan" | "green" | "blue" | "amber" }> = {
  tender_win: { label: "中标", color: "cyan" },
  tender_buy: { label: "公开采购", color: "green" },
  tender: { label: "政采公告", color: "blue" },
  proposal: { label: "项目备案", color: "amber" },
};

interface WatchRow {
  id: number;
  label: string;
  url: string;
  ent_id: number;
  ent_name: string;
  last_checked_at: string | null;
  last_changed_at: string | null;
  snapshot_head: string | null;
  check_count: number;
  error_last: string | null;
}

export default function CompetitorsPage() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [feedTotal, setFeedTotal] = useState(0);
  const [ranks, setRanks] = useState<RankItem[]>([]);
  // SPEC-RADAR：官网变更监控（昊志获客前兆——竞对/重点客户官网盯防）
  const [watches, setWatches] = useState<WatchRow[]>([]);
  const [wLabel, setWLabel] = useState("");
  const [wUrl, setWUrl] = useState("");
  const [wEntId, setWEntId] = useState<number | null>(null);
  const [wBusy, setWBusy] = useState(false);
  const [type, setType] = useState("");
  const [days, setDays] = useState(90);
  const [q, setQ] = useState("");
  const [role, setRole] = useState("sales");
  const [marking, setMarking] = useState<number | null>(null);
  const [searchName, setSearchName] = useState("");
  const [hits, setHits] = useState<{ id: number; name: string; is_competitor: number }[]>([]);
  // SPEC-CF 细节审查：榜单默认只看有动态（零动态竞对折叠）
  const [onlyActive, setOnlyActive] = useState(true);

  useEffect(() => {
    setRole(localStorage.getItem("vaclead_role") || "sales");
  }, []);

  const load = useCallback(() => {
    const p = new URLSearchParams({ days: String(days) });
    if (type) p.set("type", type);
    const listQ = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
    api.get<Overview>("/api/competitors/overview").then(setOv).catch(() => undefined);
    api.get<{ items: FeedItem[]; total: number }>(`/api/competitors/feed?${p}`)
      .then((d) => { setFeed(d.items); setFeedTotal(d.total); })
      .catch(() => undefined);
    api.get<{ items: RankItem[] }>(`/api/competitors${listQ}`)
      .then((d) => setRanks(d.items))
      .catch(() => undefined);
    api.get<{ items: WatchRow[] }>("/api/watches")
      .then((d) => setWatches(d.items))
      .catch(() => undefined);
  }, [days, type, q]);
  useEffect(load, [load]);

  // SPEC-RADAR：添加监控（首采建基线，SSRF 守卫失败给可读错误）
  const addWatch = async () => {
    if (!wEntId || !wLabel.trim() || !wUrl.trim()) {
      Toast.warning("选企业、起标签、填 URL");
      return;
    }
    setWBusy(true);
    try {
      await api.post("/api/watches", {
        enterprise_id: wEntId, label: wLabel.trim(), url: wUrl.trim(),
      });
      Toast.success("监控已添加（首次抓取已建基线）");
      setWLabel(""); setWUrl(""); setWEntId(null);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "添加失败");
    } finally {
      setWBusy(false);
    }
  };
  const checkWatch = async (id: number) => {
    try {
      const r = await api.post<{ changed: boolean }>(`/api/watches/${id}/check`, {});
      Toast.success(r.changed ? "有变更——已生成信号！" : "无变化");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "检查失败");
    }
  };
  const delWatch = async (id: number) => {
    await api.delete(`/api/watches/${id}`).catch(() => undefined);
    load();
  };

  const mark = async (id: number, flag: 0 | 1) => {
    setMarking(id);
    try {
      await api.post(`/api/competitors/${id}/mark`, { flag });
      Toast.success(flag === 1 ? "已标记为竞对" : "已取消竞对标记");
      setHits([]);
      setSearchName("");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败（需管理员）");
    } finally {
      setMarking(null);
    }
  };

  const searchEnterprise = async () => {
    if (!searchName.trim()) return;
    try {
      const d = await api.get<{ items: { id: number; name: string; is_competitor: number }[] }>(
        `/api/enterprises?q=${encodeURIComponent(searchName.trim())}&page_size=6`
      );
      setHits(d.items);
      if (d.items.length === 0) Toast.info("没有匹配的企业");
    } catch {
      Toast.error("搜索失败");
    }
  };

  // 榜单可见行（只看有动态过滤）：移动卡片与桌面表格共用
  const visibleRanks = ranks.filter((r) => !onlyActive || r.wins_30d > 0 || r.signal_count > 0);

  return (
    <AppShell>
      {/* KPI 晨检卡（framed-sheet：gap-px 发丝分格，无阴影） */}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-[var(--border)] md:grid-cols-5">
        {[
          { k: "竞对总数", v: ov ? String(ov.competitor_total) : "—", sub: "is_competitor 在管标记" },
          { k: "近 30 天中标", v: ov ? `${ov.wins_30d} 笔` : "—", sub: ov ? `${Math.round(ov.win_amount_30d_wan).toLocaleString()} 万` : "", amber: true },
          { k: "近 30 天信号", v: ov ? String(ov.signals_30d) : "—", sub: "竞对全部公开动态" },
          { k: "被抢走的生意", v: ov ? `${ov.lost_to_competitor} 条` : "—", sub: ov && ov.lost_amount_wan > 0 ? `流失合同 ${Math.round(ov.lost_amount_wan)} 万` : "竞对原因流失线索", red: true },
          { k: "最活跃竞对", v: ov?.top_competitor || "—", sub: "按 30 天中标数" },
        ].map((c, i) => (
          <div key={c.k} className={`bg-white px-4 py-3 ${i === 4 ? "col-span-2 md:col-span-1" : ""}`}>
            <div className="text-[11.5px] text-[var(--muted)]">{c.k}</div>
            <div className={`mt-0.5 truncate text-[18px] font-medium tabular-nums ${c.red ? "text-[var(--negative)]" : c.amber ? "text-[#c2801b]" : "text-[var(--ink)]"}`} title={c.v}>
              {c.v}
            </div>
            {c.sub && <div className="mt-0.5 truncate text-[11.5px] text-[var(--muted)]">{c.sub}</div>}
          </div>
        ))}
      </div>

      {/* 竞对榜单 */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white">
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--rule)] px-4 py-3">
          <span className="text-[13.5px] font-medium text-[var(--ink)]">竞对榜单</span>
          <span className="text-[12px] text-[var(--muted)]">按 30 天中标数排序</span>
          <button
            onClick={() => setOnlyActive(!onlyActive)}
            className={`ml-2 rounded-full border px-2.5 py-0.5 text-[11.5px] ${
              onlyActive
                ? "border-[var(--accent)] bg-[var(--accent-weak,rgba(94,106,210,0.1))] text-[var(--accent)]"
                : "border-[var(--border)] bg-white text-[var(--muted)]"
            }`}
          >
            只看有动态
          </button>
          {role === "admin" && (
            <div className="ml-auto flex items-center gap-2">
              <Input
                size="small"
                prefix={<Search size={13} className="text-slate-400" />}
                placeholder="搜企业名，手动标记竞对"
                value={searchName}
                onChange={setSearchName}
                onEnterPress={searchEnterprise}
                className="!w-56"
              />
              <Button size="small" onClick={searchEnterprise}>搜索</Button>
            </div>
          )}
        </div>
        {/* admin 标记搜索命中 */}
        {role === "admin" && hits.length > 0 && (
          <div className="flex flex-wrap gap-2 border-b border-[var(--rule)] bg-[var(--raised)] px-4 py-2.5">
            {hits.map((h) => (
              <span key={h.id} className="flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-white px-2.5 py-1 text-[12.5px]">
                <span className="max-w-[220px] truncate">{h.name}</span>
                {h.is_competitor === 1 ? (
                  <Button size="small" theme="borderless" type="danger" loading={marking === h.id} onClick={() => mark(h.id, 0)}>
                    取消标记
                  </Button>
                ) : (
                  <Button size="small" theme="borderless" loading={marking === h.id} onClick={() => mark(h.id, 1)}>
                    标记竞对
                  </Button>
                )}
              </span>
            ))}
          </div>
        )}
        <div className="max-h-[560px] overflow-auto">
          {/* 移动端卡片（<md）：5 列表格挤窄屏（公司名竖排）→ 行变卡（企查查榜单惯例） */}
          <div className="md:hidden">
            {visibleRanks.map((r) => (
              <div key={r.id} className="border-b border-[var(--rule)] px-4 py-3 last:border-b-0">
                <div className="flex items-start justify-between gap-2">
                  <a href={`/enterprises/view?id=${r.id}`} className="min-w-0 flex-1 break-all text-[14px] font-medium leading-5 text-[var(--accent)] hover:underline">
                    {r.name}
                  </a>
                  {role === "admin" && (
                    <Button size="small" theme="borderless" type="danger" className="!whitespace-nowrap" icon={<UserX size={13} />} loading={marking === r.id} onClick={() => mark(r.id, 0)}>
                      取消
                    </Button>
                  )}
                </div>
                <div className="mt-1.5 flex items-center gap-4 text-[12.5px] tabular-nums">
                  <span className="text-[var(--muted)]">30 天中标 <span className={r.wins_30d > 0 ? "font-medium text-[#c2801b]" : "text-[var(--ink)]"}>{r.wins_30d}</span></span>
                  <span className="text-[var(--muted)]">累计 <span className="text-[var(--ink)]">{r.win_count}</span></span>
                  <span className="text-[var(--muted)]">金额 <span className="text-[var(--ink)]">{r.win_amount_total_wan > 0 ? `${Math.round(r.win_amount_total_wan).toLocaleString()} 万` : "—"}</span></span>
                </div>
                <div className="mt-1 flex items-center justify-between gap-2 text-[12px] text-[var(--muted)]">
                  <span className="min-w-0 truncate" title={r.top_buyer}>大户：{r.top_buyer || "—"}</span>
                  <span className="shrink-0 tabular-nums">{r.last_signal_date || ""}</span>
                </div>
              </div>
            ))}
            {ranks.length === 0 && (
              <div className="px-4 py-10 text-center text-[13px] text-[var(--muted)]">
                还没有竞对标记 —— 在上方搜索企业名手动标记，或由自动化规则 tag_competitor 打标
              </div>
            )}
          </div>
          {/* 桌面表格（md+） */}
          <table className="hidden w-full text-[13px] md:table">
            <thead className="sticky top-0 z-10 bg-white">
              <tr className="text-left text-[12.5px] font-normal text-[var(--muted)]">
                <th className="px-4 py-2.5 font-normal">竞对</th>
                <th className="px-4 py-2.5 text-right font-normal">30 天中标</th>
                <th className="px-4 py-2.5 text-right font-normal">累计中标</th>
                <th className="px-4 py-2.5 text-right font-normal">累计金额</th>
                <th className="px-4 py-2.5 font-normal">采购大户</th>
                <th className="px-4 py-2.5 font-normal">最近动态</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {visibleRanks.map((r) => (
                <tr key={r.id} className="border-t border-[var(--rule)] hover:bg-[var(--raised)]">
                  <td className="px-4 py-2.5">
                    <a href={`/enterprises/view?id=${r.id}`} className="font-medium text-[var(--accent)] hover:underline">{r.name}</a>
                    {r.industry_name && <span className="ml-2 text-[12px] text-[var(--muted)]">{r.industry_name}</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {r.wins_30d > 0 ? <span className="font-medium text-[#c2801b]">{r.wins_30d}</span> : <span className="text-[var(--muted)]">0</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{r.win_count}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{r.win_amount_total_wan > 0 ? `${Math.round(r.win_amount_total_wan).toLocaleString()} 万` : "—"}</td>
                  <td className="max-w-[180px] truncate px-4 py-2.5" title={r.top_buyer}>{r.top_buyer || "—"}</td>
                  <td className="px-4 py-2.5 tabular-nums text-[var(--muted)]">{r.last_signal_date || "—"}</td>
                  <td className="px-4 py-2.5 text-right">
                    {role === "admin" && (
                      <Button size="small" theme="borderless" type="danger" icon={<UserX size={13} />} loading={marking === r.id} onClick={() => mark(r.id, 0)}>
                        取消
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
              {ranks.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-[13px] text-[var(--muted)]">
                    还没有竞对标记 —— 在上方搜索企业名手动标记，或由自动化规则 tag_competitor 打标
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 动态流筛选 */}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-[13.5px] font-medium text-[var(--ink)]">
          <Crosshair size={15} /> 竞对动态流
        </span>
        <Select value={type || undefined} placeholder="全部类型" showClear onChange={(v) => setType(String(v ?? ""))} className="!w-32">
          <Select.Option value="tender_win">中标</Select.Option>
          <Select.Option value="tender">政采公告</Select.Option>
          <Select.Option value="tender_buy">公开采购</Select.Option>
          <Select.Option value="proposal">项目备案</Select.Option>
        </Select>
        <Select value={String(days)} onChange={(v) => setDays(Number(v))} className="!w-28">
          <Select.Option value="30">近 30 天</Select.Option>
          <Select.Option value="90">近 90 天</Select.Option>
          <Select.Option value="365">近 1 年</Select.Option>
        </Select>
        <span className="ml-auto text-[12.5px] text-[var(--muted)]">共 {feedTotal} 条动态</span>
      </div>

      {/* 动态流 */}
      <div className="mt-2.5 space-y-2.5">
        {feed.map((s) => {
          const tag = TYPE_TAG[s.type] ?? { label: s.type, color: "blue" as const };
          return (
            <div key={s.id} className="rounded-lg border border-[var(--border)] bg-white px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Tag color={tag.color} type="light" size="small">{tag.label}</Tag>
                    {s.amount_wan > 0 && (
                      <span className="text-[13px] font-medium tabular-nums text-[#c2801b]">
                        {s.amount_wan.toLocaleString()} 万
                      </span>
                    )}
                    <span className="text-[12px] tabular-nums text-[var(--muted)]">{s.pub_date}</span>
                  </div>
                  <div className="mt-1 flex items-center gap-1.5">
                    <span className="truncate text-[14px] font-medium text-[var(--ink)]" title={s.title}>{s.title}</span>
                    {s.url && (
                      <a href={s.url} target="_blank" rel="noreferrer" className="shrink-0 text-[11.5px] text-[var(--accent)] hover:underline">
                        原文 ↗
                      </a>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[13px]">
                    <a href={`/enterprises/view?id=${s.enterprise_id}`} className="font-medium text-[var(--accent)] hover:underline">
                      {s.enterprise_name}
                    </a>
                    {s.stolen_from && (
                      <span className="text-[12.5px] text-[var(--muted)]">
                        卖给了 <span className="font-medium text-[var(--negative)]">{s.stolen_from}</span>
                        <span className="ml-1 text-[11.5px]">—— 被抢走的机会点</span>
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
        {feed.length === 0 && (
          <div className="py-16 text-center text-[13px] text-[var(--muted)]">
            窗口内竞对暂无公开动态 —— 放宽时间窗或类型试试
          </div>
        )}
      </div>

      {/* SPEC-RADAR：官网变更监控（昊志获客前兆）——盯竞对/重点客户官网
          新闻/招聘页，工业词命中即推信号+群播报（借鉴 changedetection.io） */}
      <section className="mt-6 rounded-lg border border-[var(--border)] bg-white p-4">
        <h2 className="text-[15px] font-medium text-[var(--ink)]">官网监控雷达</h2>
        <p className="mt-1 text-[12.5px] leading-5 text-[var(--muted)]">
          盯竞对和重点客户的官网页面——新闻页更新（新产线/中标/扩建）、
          招聘页更新（扩张）都是获客前兆，命中工业词自动推信号和群播报。
        </p>
        {/* 添加行 */}
        <div className="mt-3 flex flex-col gap-2 md:flex-row md:items-center">
          <Input
            size="small"
            placeholder="搜企业名加监控"
            value={searchName}
            onChange={setSearchName}
            onEnterPress={searchEnterprise}
            className="!w-52"
          />
          <Select
            size="small"
            placeholder="选企业（搜索后选择）"
            value={wEntId ?? undefined}
            onChange={(v) => setWEntId(Number(v))}
            className="!w-56"
          >
            {hits.map((h) => (
              <Select.Option key={h.id} value={h.id}>{h.name}</Select.Option>
            ))}
          </Select>
          <Input
            size="small"
            placeholder="监控标签（如：官网新闻页）"
            value={wLabel}
            onChange={setWLabel}
            className="!w-44"
          />
          <Input
            size="small"
            placeholder="https://官网页面地址"
            value={wUrl}
            onChange={setWUrl}
            className="!w-64"
          />
          <Button size="small" theme="solid" loading={wBusy} onClick={() => void addWatch()}>
            添加监控
          </Button>
        </div>
        {/* 监控列表 */}
        <div className="mt-3 space-y-2">
          {watches.map((w) => (
            <div key={w.id} className="rounded-lg border border-[var(--border)] px-3.5 py-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="text-[13.5px] font-medium text-[var(--ink)]">{w.label}</span>
                  <a
                    href={`/enterprises/view?id=${w.ent_id}`}
                    className="ml-2 text-[12.5px] text-[var(--accent)] hover:underline"
                  >
                    {w.ent_name}
                  </a>
                  {w.error_last && (
                    <Tag color="red" type="light" size="small" className="!ml-2">抓取失败</Tag>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <Button size="small" onClick={() => void checkWatch(w.id)}>立即检查</Button>
                  <Button size="small" type="tertiary" onClick={() => void delWatch(w.id)}>移除</Button>
                </div>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11.5px] text-[var(--muted)]">
                <span className="truncate" title={w.url}>{w.url}</span>
                <span>检查 {w.check_count} 次 · 上次 {w.last_checked_at?.replace("T", " ") ?? "—"}</span>
                {w.last_changed_at && (
                  <span className="text-[#c2801b]">变更于 {w.last_changed_at.replace("T", " ")}</span>
                )}
              </div>
              {w.snapshot_head && (
                <div className="mt-1 line-clamp-2 text-[11.5px] leading-4 text-[var(--muted)]">
                  快照：{w.snapshot_head}
                </div>
              )}
            </div>
          ))}
          {watches.length === 0 && (
            <div className="py-6 text-center text-[13px] text-[var(--muted)]">
              还没有监控 —— 搜企业名加一个试试（建议先盯竞对官网和大客户新闻页）
            </div>
          )}
        </div>
      </section>
    </AppShell>
  );
}
