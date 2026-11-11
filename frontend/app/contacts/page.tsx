"use client";

/**
 * 联系人中心（SPEC-BQ）：跨企业拨号工作台——"打 10 通电话"的日常作业面。
 * 可拨优先 / 未触达优先 / 企业意图分排序 / 边打边标（快标 7 态）。
 * 头部平台（Apollo/Revenue.io）daily call list 的对位形态。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Input, Modal, Select, Tag, Toast, Tooltip } from "@douyinfe/semi-ui";
import { Download, Keyboard, Phone } from "lucide-react";
import AppShell from "@/components/AppShell";
import CallResultTag, { CALL_RESULTS } from "@/components/CallResultTag";
import { api } from "@/lib/api";

interface ContactRow {
  id: number;
  name: string;
  role: string;
  title: string | null;
  decision_role: string | null;
  verify_state: string;
  email: string | null;
  phones: string[];
  call_result: string | null;
  called_at: string | null;
  source: string;
  evidence_claim: string | null;
  /** SPEC-DQ：号码共享（≥3 家挂靠 = 疑似代理/中介总机） */
  _shared_ent_count?: number;
  enterprise: {
    id: number;
    name: string;
    province: string | null;
    city: string | null;
    industry_name: string | null;
    intent_score: number;
    is_competitor: number;
    customer_role: string | null;
  };
}

interface PrepData {
  contact: {
    id: number; name: string; role: string; title: string | null;
    phones: string[]; decision_role: string | null;
    verify_state: string; call_result: string | null;
    evidence_claim: string | null;
    /** SPEC-DQ：号码共享（≥3 家挂靠 = 疑似代理/中介总机） */
    shared_ent_count?: number;
  };
  enterprise: {
    id: number; name: string; industry_name: string | null;
    province: string | null; city: string | null;
    intent_score: number; customer_role: string | null;
    is_competitor: number;
  };
  signals: { type: string; title: string; evidence: string; pub_date: string; url: string }[];
  product_match: string | null;
  lead: { id: number; status: string; next_action: string | null; next_time: string } | null;
  last_followup: { content: string; created_at: string } | null;
  /** SPEC-CUX CUX3 通话时间线：拨打 + 跟进合流（时间倒序） */
  timeline?: { kind: "call" | "followup"; at: string; label?: string; content?: string }[];
}

interface Worklist {
  stats: { with_phone: number; untouched: number; touched: number; interested: number };
  provinces: Record<string, number>;
  items: ContactRow[];
  total: number;
}

const ROLE_CN: Record<string, string> = {
  legal_rep: "法人",
  buyer_contact: "采购联系人",
  project_contact: "项目联系人",
  map_phone: "地图公开电话",
  proxy: "代理机构",
};

export default function ContactsPage() {
  // SPEC-CUX2：真分页状态——meta（stats/provinces/total）+ items 跨页累积；
  // 旧版"一次拉 100 条 + visibleCount 本地切片"随 2000 采样上限一并废弃
  const [meta, setMeta] = useState<{
    stats: Worklist["stats"];
    provinces: Record<string, number>;
    total: number;
  } | null>(null);
  const [items, setItems] = useState<ContactRow[]>([]);
  const [loading, setLoading] = useState(false);
  // SPEC-CUX2：批量标记选择集（跨页保留，翻页不清空；筛选切换时清空）
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [batchResult, setBatchResult] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);
  const [callStatus, setCallStatus] = useState("untouched");
  const [decisionRole, setDecisionRole] = useState("");
  const [province, setProvince] = useState("");
  const [q, setQ] = useState("");
  const [marking, setMarking] = useState<number | null>(null);
  // SPEC-CH 通话驾驶舱
  const [prepId, setPrepId] = useState<number | null>(null);
  const [prep, setPrep] = useState<PrepData | null>(null);
  const [prepLoading, setPrepLoading] = useState(false);
  const [fuText, setFuText] = useState("");
  const [fuBusy, setFuBusy] = useState(false);
  // SPEC-CUX CUX1 键盘流选中行（索引夹取见下方 effect）；CUX3 时间线折叠态
  const [selIdx, setSelIdx] = useState(0);
  const [tlOpen, setTlOpen] = useState(false);
  // SPEC-CUX CUX2 排序切换：order 从 URL 挂载恢复（白名单校验，非法回退智能推荐）
  const [order, setOrder] = useState(() => {
    if (typeof window === "undefined") return "";
    const v = new URLSearchParams(window.location.search).get("order") ?? "";
    return ["newest", "intent", "recent_called"].includes(v) ? v : "";
  });

  const openPrep = async (cid: number) => {
    setPrepId(cid);
    setPrep(null);
    setFuText("");
    setTlOpen(false);
    setPrepLoading(true);
    try {
      const d = await api.get<PrepData>(`/api/contacts/${cid}/prep`);
      setPrep(d);
    } catch {
      Toast.error("加载通话准备失败");
      setPrepId(null);
    } finally {
      setPrepLoading(false);
    }
  };

  const submitFollowup = async () => {
    if (!prep?.lead || !fuText.trim()) return;
    setFuBusy(true);
    try {
      await api.post(`/api/leads/${prep.lead.id}/followups`, { content: fuText.trim() });
      Toast.success("跟进已记录");
      setFuText("");
      openPrep(prep.contact.id);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "记录失败");
    } finally {
      setFuBusy(false);
    }
  };

  const convertFromPrep = async () => {
    if (!prep) return;
    try {
      await api.post(`/api/enterprises/${prep.enterprise.id}/lead`, {});
      Toast.success("已转线索，可直接写跟进");
      openPrep(prep.contact.id);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "转线索失败");
    }
  };

  // ---- SPEC-CUX2：真分页（offset/limit 进 SQL，2000 采样上限已删）----
  // 2026-11-11 用户批评"连分页也没有"——隐形无限滚动改**显式分页条**
  // （HubSpot/Attio CRM 列表惯例：‹ › + "1–50 / 共 N"），不再触底隐形加载。
  const PAGE_SIZE = 50;
  const [page, setPage] = useState(1);
  const genRef = useRef(0);
  const inflightRef = useRef<number | null>(null);

  const buildParams = useCallback(
    (offset: number) => {
      const p = new URLSearchParams({
        has_phone: "1",
        limit: String(PAGE_SIZE),
        offset: String(offset),
      });
      if (callStatus) p.set("call_status", callStatus);
      if (decisionRole) p.set("decision_role", decisionRole);
      if (province) p.set("province", province);
      if (q.trim()) p.set("q", q.trim());
      if (order) p.set("order", order);
      return p;
    },
    [callStatus, decisionRole, province, q, order],
  );

  const load = useCallback(() => {
    const gen = ++genRef.current;
    inflightRef.current = gen;
    setLoading(true);
    api.get<Worklist>(`/api/contacts?${buildParams(0)}`)
      .then((d) => {
        if (gen !== genRef.current) return; // 已被更新的请求作废
        setMeta({ stats: d.stats, provinces: d.provinces, total: d.total });
        setItems(d.items);
        setSelected(new Set());
        setPage(1);
      })
      .catch(() => undefined)
      .finally(() => {
        if (inflightRef.current === gen) inflightRef.current = null;
        if (gen === genRef.current) setLoading(false);
      });
  }, [buildParams]);
  useEffect(load, [load]);

  // 翻页（显式分页条）：换页重置选中与键盘选中态
  const goToPage = useCallback(
    (next: number) => {
      if (!meta) return;
      const totalPages = Math.max(1, Math.ceil(meta.total / PAGE_SIZE));
      const target = Math.min(Math.max(1, next), totalPages);
      if (target === page) return;
      const gen = ++genRef.current;
      inflightRef.current = gen;
      setLoading(true);
      api.get<Worklist>(`/api/contacts?${buildParams((target - 1) * PAGE_SIZE)}`)
        .then((d) => {
          if (gen !== genRef.current) return;
          setMeta({ stats: d.stats, provinces: d.provinces, total: d.total });
          setItems(d.items);
          setSelected(new Set());
          setPage(target);
        })
        .catch(() => undefined)
        .finally(() => {
          if (inflightRef.current === gen) inflightRef.current = null;
          if (gen === genRef.current) setLoading(false);
        });
    },
    [buildParams, meta, page],
  );
  const totalPages = meta ? Math.max(1, Math.ceil(meta.total / PAGE_SIZE)) : 1;
  const rangeFrom = meta ? (page - 1) * PAGE_SIZE + 1 : 0;
  const rangeTo = meta ? Math.min(page * PAGE_SIZE, meta.total) : 0;

  // SPEC-CUX CUX2：order 轻量同步 URL（replaceState 静默写，参照 enterprises 页惯例；
  // 保留 search 里既有的其它参数，分享/刷新/返回排序不丢）
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    if (order) sp.set("order", order);
    else sp.delete("order");
    const qs = sp.toString();
    if (window.location.search !== (qs ? `?${qs}` : "")) {
      window.history.replaceState(null, "", qs ? `/contacts?${qs}` : "/contacts");
    }
  }, [order]);

  const mark = async (cid: number, result: string) => {
    setMarking(cid);
    try {
      await api.post(`/api/contacts/${cid}/call-result`, { result });
      if (result === "interested") Toast.success("已标有意向——回工作台可一键转线索");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "标记失败");
    } finally {
      setMarking(null);
    }
  };

  // SPEC-CUX2：批量多选辅助（当页全选/反选由列表头 checkbox 承担）
  const allSelected = items.length > 0 && items.every((i) => selected.has(i.id));
  const someSelected = selected.size > 0 && !allSelected;
  const toggleRow = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(items.map((i) => i.id)));

  // SPEC-CUX2：批量标记提交 → POST /contacts/batch-call-result → 成功 Toast(changed)
  // + 行内状态即时刷新（本地 map 更新 call_result，不整页重拉）
  const runBatch = async () => {
    if (!batchResult || selected.size === 0) return;
    setBatchBusy(true);
    try {
      const d = await api.post<{ changed: number }>("/api/contacts/batch-call-result", {
        ids: Array.from(selected),
        result: batchResult,
      });
      Toast.success(`已批量标记 ${d.changed} 人`);
      const result = batchResult;
      setItems((prev) =>
        prev.map((it) => (selected.has(it.id) ? { ...it, call_result: result } : it)),
      );
      setSelected(new Set());
      setBatchResult("");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "批量标记失败");
    } finally {
      setBatchBusy(false);
    }
  };

  // SPEC-CUX CUX1 键盘流（Close 拨号器惯例）：J/K/↓/↑ 导航、Enter 打开选中行驾驶舱。
  // 守卫照抄 KeyboardShortcuts 同款：输入框/文本域/下拉/按钮聚焦时全部跳过（不干扰打字）；
  // 驾驶舱开着时让位（Esc 关闭已有），修饰键组合一律不劫持。
  useEffect(() => {
    const rows = items; // SPEC-CUX2：真分页后键盘流直接作用于已累积的全部行
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.tagName === "BUTTON" ||
          target.isContentEditable);
      if (inField || e.ctrlKey || e.metaKey || e.altKey || prepId !== null) return;
      if (rows.length === 0) return;
      if (e.key === "j" || e.key === "J" || e.key === "ArrowDown") {
        e.preventDefault();
        setSelIdx((i) => Math.min(i + 1, rows.length - 1));
      } else if (e.key === "k" || e.key === "K" || e.key === "ArrowUp") {
        e.preventDefault();
        setSelIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        const row = rows[selIdx] ?? rows[0];
        if (row) {
          e.preventDefault();
          void openPrep(row.id);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, selIdx, prepId]);

  // SPEC-CUX CUX1：筛选/加载更多后选中越界自动夹取（越界回 0）
  useEffect(() => {
    setSelIdx((i) => (i >= items.length ? 0 : i));
  }, [items]);

  const st = meta?.stats;
  // SPEC-CUX CUX3：时间线渲染列表（默认最近 5 条，>5 折叠可展开）
  const tl = prep?.timeline ?? [];

  return (
    <AppShell>
      {/* KPI 作业面卡 */}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-[var(--border)] md:grid-cols-4">
        {[
          { k: "可拨联系人", v: st ? String(st.with_phone) : "—", sub: "号码库规范化口径" },
          { k: "未触达", v: st ? String(st.untouched) : "—", sub: "还没打过的金矿", amber: true },
          { k: "已触达", v: st ? String(st.touched) : "—", sub: "打过的都在沉淀" },
          { k: "有意向", v: st ? String(st.interested) : "—", sub: "立即转线索", red: true },
        ].map((c) => (
          <div key={c.k} className="bg-white px-4 py-3">
            <div className="text-[11.5px] text-[var(--muted)]">{c.k}</div>
            <div className={`mt-0.5 text-[18px] font-medium tabular-nums ${c.amber ? "text-[#c2801b]" : c.red ? "text-[var(--positive)]" : "text-[var(--ink)]"}`}>
              {c.v}
            </div>
            {c.sub && <div className="mt-0.5 truncate text-[11.5px] text-[var(--muted)]">{c.sub}</div>}
          </div>
        ))}
      </div>

      {/* 筛选条：<md 折成「标题行 + 横向滚动筛选行（不折行）」；md+ 经 contents 并回原单行 */}
      <div className="mt-4 flex flex-nowrap items-center gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [-&::-webkit-scrollbar]:hidden md:flex-wrap md:overflow-visible">
        <span className="flex shrink-0 items-center gap-1.5 text-[13.5px] font-medium text-[var(--ink)]">
          <Phone size={15} /> 今日拨打名单
        </span>
        <div className="flex w-full flex-nowrap items-center gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden max-md:order-last md:contents">
          <Select value={callStatus || undefined} placeholder="全部状态" showClear onChange={(v) => setCallStatus(String(v ?? ""))} className="!w-32 shrink-0">
            <Select.Option value="untouched">未触达</Select.Option>
            <Select.Option value="touched">已触达</Select.Option>
            <Select.Option value="interested">有意向</Select.Option>
            <Select.Option value="connected">已接通</Select.Option>
            <Select.Option value="no_answer">无人接</Select.Option>
            <Select.Option value="invalid">空号</Select.Option>
          </Select>
          <Select value={decisionRole || undefined} placeholder="全部角色" showClear onChange={(v) => setDecisionRole(String(v ?? ""))} className="!w-32 shrink-0">
            <Select.Option value="decision_maker">决策链</Select.Option>
            <Select.Option value="handler">经办人</Select.Option>
          </Select>
          <Select value={province || undefined} placeholder="全部省份" showClear onChange={(v) => setProvince(String(v ?? ""))} className="!w-32 shrink-0">
            {Object.keys(meta?.provinces ?? {}).map((p) => (
              <Select.Option key={p} value={p}>{p}</Select.Option>
            ))}
          </Select>
          {/* SPEC-CUX CUX2 排序切换：服务端 order 白名单（空=智能推荐默认序） */}
          <Select value={order || undefined} placeholder="智能推荐" showClear onChange={(v) => setOrder(String(v ?? ""))} className="!w-32 shrink-0">
            <Select.Option value="newest">最新入库</Select.Option>
            <Select.Option value="intent">意图最高</Select.Option>
            <Select.Option value="recent_called">最近拨打</Select.Option>
          </Select>
          <Input
            prefix={<Phone size={13} className="text-slate-400" />}
            placeholder="搜联系人/企业名"
            value={q}
            onChange={setQ}
            showClear
            className="!w-56 shrink-0"
          />
          <Button
            size="small"
            icon={<Download size={13} />}
            className="shrink-0"
            onClick={() => {
              const p = new URLSearchParams({ has_phone: "1", limit: "5000" });
              if (callStatus) p.set("call_status", callStatus);
              if (decisionRole) p.set("decision_role", decisionRole);
              if (province) p.set("province", province);
              if (q.trim()) p.set("q", q.trim());
              window.open(`/api/contacts/export?${p}`, "_blank");
            }}
          >
            导出名单
          </Button>
        </div>
        <span className="hidden items-center gap-1.5 text-[12.5px] text-[var(--muted)] md:inline-flex md:ml-auto">
          {/* SPEC-CUX CUX1：键盘流提示（hover 显示） */}
          <Tooltip content="J/K 导航 · Enter 打开">
            <Keyboard size={14} className="cursor-help" />
          </Tooltip>
          共 {meta?.total ?? 0} 人 · 排序：{order
            ? { newest: "最新入库", intent: "意图最高", recent_called: "最近拨打" }[order]
            : "可拨优先 → 意图分 → 未拨优先"}
        </span>
      </div>

      {/* SPEC-CUX2：列表头多选 + 批量标记栏——当页全选/反选 → 选中 N 人 →
          七态下拉 → 确认；checkbox 触屏目标 h-5 w-5 起步（移动端达标） */}
      {meta && items.length > 0 && (
        <div className="mt-2.5 flex flex-wrap items-center gap-3 rounded-lg border border-[var(--border)] bg-white px-4 py-2">
          <label className="flex cursor-pointer select-none items-center gap-2 text-[12.5px] text-[var(--muted)]">
            <input
              type="checkbox"
              className="h-5 w-5 cursor-pointer accent-[var(--accent)]"
              aria-label="全选/反选当页联系人"
              checked={allSelected}
              ref={(el) => {
                if (el) el.indeterminate = someSelected;
              }}
              onChange={toggleAll}
            />
            全选当页（{items.length} 人）
          </label>
          {selected.size > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-l border-[var(--border)] pl-3">
              <span className="text-[13px] font-medium text-[var(--ink)]">
                已选 {selected.size} 人
              </span>
              <Select
                value={batchResult || undefined}
                placeholder="标记为…"
                showClear
                className="!w-32"
                onChange={(v) => setBatchResult(String(v ?? ""))}
              >
                {CALL_RESULTS.map((r) => (
                  <Select.Option key={r.key} value={r.key}>
                    {r.label}
                  </Select.Option>
                ))}
              </Select>
              <Button
                size="small"
                theme="solid"
                loading={batchBusy}
                disabled={!batchResult}
                onClick={() => void runBatch()}
              >
                确认标记
              </Button>
              <Button size="small" theme="borderless" onClick={() => setSelected(new Set())}>
                取消
              </Button>
            </div>
          )}
        </div>
      )}

      {/* 拨打名单 */}
      <div className="mt-2.5 space-y-2">
        {!meta && (
          <div className="space-y-2" aria-hidden>
            {/* SPEC-UX 骨架屏：数据 3s+ 加载期的空白感知（animate-pulse 纯 CSS） */}
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="animate-pulse rounded-lg border border-[var(--border)] bg-white px-4 py-3">
                <div className="h-4 w-40 rounded bg-[var(--raised)]" />
                <div className="mt-2 h-3.5 w-56 rounded bg-[var(--raised)]" />
                <div className="mt-2 h-3 w-full max-w-[420px] rounded bg-[var(--raised)]" />
              </div>
            ))}
          </div>
        )}
        {meta && items.map((c, i) => (
          <div
            key={c.id}
            className={`cursor-pointer rounded-lg border px-4 py-3 transition-colors ${
              i === selIdx
                ? // SPEC-CUX CUX1：选中行 accent 左边条（内阴影实现不挤压布局）+ raised 底色
                  "border-[var(--accent)] bg-[var(--raised)] shadow-[inset_3px_0_0_var(--accent)]"
                : "border-[var(--border)] bg-white hover:border-[var(--accent)]"
            }`}
            onClick={() => void openPrep(c.id)}
          >
            <div className="flex items-start justify-between gap-3">
              {/* SPEC-CUX2：批量多选 checkbox（触屏目标 h-5 w-5；stopPropagation
                  防误开驾驶舱） */}
              <input
                type="checkbox"
                className="mt-0.5 h-5 w-5 shrink-0 cursor-pointer accent-[var(--accent)]"
                aria-label={`选中 ${c.name}`}
                checked={selected.has(c.id)}
                onClick={(ev) => ev.stopPropagation()}
                onChange={() => toggleRow(c.id)}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[14px] font-medium text-[var(--ink)]">{c.name}</span>
                  <Tag color="cyan" type="light" size="small">{ROLE_CN[c.role] ?? c.role}</Tag>
                  {c.decision_role === "decision_maker" && (
                    <Tag color="orange" type="light" size="small">决策链</Tag>
                  )}
                  {c.verify_state === "verified" && (
                    <Tag color="green" type="light" size="small">公告验证</Tag>
                  )}
                  {(c._shared_ent_count ?? 0) >= 3 && (
                    <span title="该号码同时挂在多家企业名下，可能是招标代理/中介总机">
                      <Tag color="amber" type="light" size="small">疑似代理 · 挂{c._shared_ent_count}家</Tag>
                    </span>
                  )}
                  {c.title && <span className="text-[12px] text-[var(--muted)]">{c.title}</span>}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-[13px]">
                  {c.phones.map((p) => (
                    <span key={p} className="inline-flex items-center gap-0.5">
                      <span
                        className="cursor-pointer rounded border border-[var(--border)] bg-[var(--raised)] px-2 py-1 font-mono text-[12.5px] tabular-nums text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white md:px-1.5 md:py-0.5"
                        title="点击复制号码"
                        onClick={(ev) => {
                          ev.stopPropagation();
                          void navigator.clipboard?.writeText(p);
                          Toast.success(`已复制 ${p}`);
                        }}
                      >
                        {p}
                      </span>
                      <a
                        href={`tel:${p}`}
                        title="点击拨打"
                        className="inline-flex h-8 w-8 items-center justify-center rounded text-[var(--muted)] transition-colors hover:bg-[var(--raised)] hover:text-[var(--accent)]"
                        onClick={(ev) => ev.stopPropagation()}
                      >
                        <Phone size={14} />
                      </a>
                    </span>
                  ))}
                  <a
                    href={`/enterprises/view?id=${c.enterprise.id}`}
                    className="inline-flex items-center self-stretch py-2 text-[var(--accent)] hover:underline"
                  >
                    {c.enterprise.name}
                  </a>
                  <span className="text-[12px] tabular-nums text-[var(--muted)]">
                    意图 {c.enterprise.intent_score}
                  </span>
                  {(c.enterprise.province || c.enterprise.city) && (
                    <span className="text-[12px] text-[var(--muted)]">
                      {[c.enterprise.province, c.enterprise.city].filter(Boolean).join("·")}
                    </span>
                  )}
                </div>
                {c.evidence_claim && (
                  <div className="mt-1 border-l-2 border-[var(--accent)] pl-2 text-[12px] leading-4 text-[var(--muted)]">
                    {c.evidence_claim}
                  </div>
                )}
              </div>
              <div className="shrink-0" onClick={(ev) => ev.stopPropagation()}>
                <CallResultTag
                  result={(c.call_result as string) ?? "pending"}
                  onChange={(r) => void mark(c.id, r)}
                />
              </div>
            </div>
          </div>
        ))}
        {meta && items.length === 0 && (
          <div className="py-16 text-center text-[13px] text-[var(--muted)]">
            当前筛选下没有可拨联系人 —— 换个状态/省份，或去「标讯中心」让 04:08 回填任务补号码
          </div>
        )}
        {/* SPEC-CUX2（2026-11-11 用户批评"连分页也没有"）：显式分页条——
            HubSpot/Attio CRM 列表惯例，替代隐形无限滚动 */}
        {meta && items.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-[var(--rule)] pt-3">
            <span className="text-[12.5px] tabular-nums text-[var(--muted)]">
              {rangeFrom.toLocaleString()}–{rangeTo.toLocaleString()} · 共 {meta.total.toLocaleString()} 人
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="small"
                disabled={page <= 1 || loading}
                onClick={() => goToPage(page - 1)}
              >
                上一页
              </Button>
              <span className="text-[12.5px] tabular-nums text-[var(--ink)]">
                第 {page} / {totalPages} 页
              </span>
              <Button
                size="small"
                disabled={page >= totalPages || loading}
                onClick={() => goToPage(page + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        )}
        {loading && items.length > 0 && (
          <div className="py-4 text-center text-[12.5px] text-[var(--muted)]" role="status">
            加载中…
          </div>
        )}
      </div>

      {/* SPEC-CH 通话驾驶舱：为什么现在打 / 怎么聊 / 动作区 */}
      {/* 通话驾驶舱：居中作战面板（2026-11-10 用户定稿弃侧边栏）——桌面双栏
          （左情报右动作），移动端全站 Modal 兜底自动收窄单列 */}
      <Modal
        visible={prepId !== null}
        onCancel={() => setPrepId(null)}
        footer={null}
        closeOnEsc
        width={920}
        style={{ maxWidth: "calc(100vw - 24px)" }}
        bodyStyle={{ padding: 0 }}
      >
        {prepLoading || !prep ? (
          <div className="py-14 text-center text-[13px] text-[var(--muted)]">加载中…</div>
        ) : (
          <div className="min-w-0 overflow-hidden">
            {/* 头部：人物区（姓名大字 + 电话即拨打） */}
            <div className="flex min-w-0 max-w-full flex-col gap-3 overflow-hidden border-b border-[var(--border)] px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[19px] font-semibold text-[var(--ink)]">{prep.contact.name}</span>
                  <Tag color="cyan" type="light" size="small">{ROLE_CN[prep.contact.role] ?? prep.contact.role}</Tag>
                  {prep.contact.decision_role === "decision_maker" && <Tag color="orange" type="light" size="small">决策链</Tag>}
                  {prep.contact.verify_state === "verified" && <Tag color="green" type="light" size="small">公告验证</Tag>}
                  {(prep.contact.shared_ent_count ?? 0) >= 3 && (
                    <span title="该号码同时挂在多家企业名下，可能是招标代理/中介总机">
                      <Tag color="amber" type="light" size="small">疑似代理 · 挂{prep.contact.shared_ent_count}家</Tag>
                    </span>
                  )}
                  {prep.contact.call_result && prep.contact.call_result !== "pending" && (
                    <Tag color="grey" type="light" size="small">
                      {CALL_RESULTS.find((x) => x.key === prep.contact.call_result)?.label ?? "已拨"}
                    </Tag>
                  )}
                </div>
                <div className="mt-1 w-full min-w-0 break-all text-[12.5px] leading-5 text-[var(--muted)]">
                  <a href={`/enterprises/view?id=${prep.enterprise.id}`} className="text-[var(--accent)] hover:underline">
                    {prep.enterprise.name}
                  </a>
                  {" · "}{[prep.enterprise.industry_name, prep.enterprise.province, prep.enterprise.city].filter(Boolean).join(" · ")}
                  {" · 意图 "}{prep.enterprise.intent_score}
                </div>
              </div>
              {prep.contact.phones[0] && (
                <a
                  href={`tel:${prep.contact.phones[0]}`}
                  className="flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg bg-[#131416] px-5 text-[14px] font-medium text-white transition-opacity active:opacity-70"
                >
                  <Phone size={15} /> 拨打 {prep.contact.phones[0]}
                </a>
              )}
            </div>

            {/* 双栏：左情报（为什么现在打 + 怎么聊）/ 右动作（快标 + 跟进 + 转线索） */}
            <div className="grid grid-cols-1 gap-0 md:grid-cols-[1fr_280px]">
              <div className="space-y-5 px-5 py-4 md:border-r md:border-[var(--border)]">
                {/* 为什么现在打 */}
                <div>
                  <div className="mb-2 text-[13.5px] font-medium text-[var(--ink)]">为什么现在打</div>
                  {prep.signals.length === 0 ? (
                    <div className="text-[12.5px] text-[var(--muted)]">暂无公开动态——可从企业近况切入</div>
                  ) : (
                    <div className="space-y-2.5">
                      {prep.signals.map((s, i) => (
                        <div key={i} className="border-l-2 border-[var(--accent)] pl-3">
                          <div className="flex items-center gap-2">
                            <span className="text-[11px] tabular-nums text-[var(--muted)]">{s.pub_date}</span>
                            {s.url && (
                              <a href={s.url} target="_blank" rel="noreferrer" className="text-[11px] text-[var(--accent)] hover:underline">原文 ↗</a>
                            )}
                          </div>
                          <div className="text-[13px] leading-5 text-[var(--ink)]">{s.title}</div>
                          {s.evidence && (
                            <div className="text-[11.5px] leading-4 text-[var(--muted)]">{s.evidence.slice(0, 100)}</div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* 怎么聊 */}
                <div>
                  <div className="mb-2 text-[13.5px] font-medium text-[var(--ink)]">怎么聊</div>
                  {prep.product_match ? (
                    <div className="rounded-lg bg-[var(--accent-weak,rgba(94,106,210,0.08))] px-3.5 py-2.5 text-[13px] leading-5 text-[var(--ink)]">
                      「{prep.product_match}」适配其{prep.enterprise.industry_name ?? ""}工艺——从它现在的工序痛点切入，报型号与节能账。
                    </div>
                  ) : (
                    <div className="text-[12.5px] text-[var(--muted)]">
                      暂无产品适配记录——通用开场：确认设备现状与真空工艺需求。
                    </div>
                  )}
                  {prep.last_followup && (
                    <div className="mt-2 rounded-lg bg-[var(--raised)] px-3 py-2 text-[12px] leading-4 text-[var(--muted)]">
                      上次跟进（{prep.last_followup.created_at.replace("T", " ").slice(0, 16)}）：{prep.last_followup.content.slice(0, 60)}
                    </div>
                  )}
                  {/* SPEC-CUX CUX3 通话时间线：拨打 + 跟进合流，最近 5 条，>5 折叠可展开 */}
                  {tl.length > 0 && (
                    <div className="mt-3">
                      <div className="mb-1.5 text-[13.5px] font-medium text-[var(--ink)]">通话时间线</div>
                      <div className="space-y-1.5">
                        {(tlOpen ? tl : tl.slice(0, 5)).map((t, i) => (
                          <div key={i} className="flex items-start gap-2 text-[12.5px] leading-5">
                            <span className="shrink-0">{t.kind === "call" ? "☎" : "💬"}</span>
                            <span className="min-w-0 flex-1 break-words text-[var(--ink)]">
                              {t.kind === "call" ? (t.label ?? "已拨") : (t.content ?? "")}
                            </span>
                            <span className="shrink-0 tabular-nums text-[11.5px] text-[var(--muted)]">
                              {(t.at ?? "").replace("T", " ")}
                            </span>
                          </div>
                        ))}
                      </div>
                      {tl.length > 5 && (
                        <Button size="small" theme="borderless" className="!mt-1 !px-0" onClick={() => setTlOpen((v) => !v)}>
                          {tlOpen ? "收起" : `展开全部 ${tl.length} 条`}
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* 动作栏 */}
              <div className="border-t border-[var(--border)] bg-[var(--raised)] px-5 py-4 md:border-t-0">
                <div className="text-[13px] font-medium text-[var(--ink)]">打完之后</div>
                <div className="mt-2.5 flex items-center gap-2">
                  <span className="text-[12px] text-[var(--muted)]">标记结果</span>
                  <div onClick={(ev) => ev.stopPropagation()}>
                    <CallResultTag
                      result={(prep.contact.call_result as string) ?? "pending"}
                      onChange={(r) => {
                        void mark(prep.contact.id, r).then(() => {
                          setPrep({ ...prep, contact: { ...prep.contact, call_result: r } });
                        });
                      }}
                    />
                  </div>
                </div>
                {prep.lead ? (
                  <div className="mt-3">
                    <textarea
                      value={fuText}
                      onChange={(e) => setFuText(e.target.value)}
                      placeholder="打完记一句：聊了什么/下一步…"
                      rows={4}
                      className="w-full rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-[12.5px] outline-none focus:border-[var(--accent)]"
                    />
                    <div className="mt-1.5 flex justify-end">
                      <Button size="small" theme="solid" loading={fuBusy} disabled={!fuText.trim()} onClick={() => void submitFollowup()}>
                        记跟进
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="mt-4">
                    <p className="text-[12px] leading-5 text-[var(--muted)]">意向不错？转成线索开始正式跟进：</p>
                    <Button size="small" theme="outline" className="!mt-2" onClick={() => void convertFromPrep()}>
                      转为线索
                    </Button>
                  </div>
                )}
                <div className="mt-4 border-t border-[var(--border)] pt-3 text-[11.5px] leading-4 text-[var(--muted)]">
                  挂断就标结果——推荐引擎按接通/空号自动调排序。
                </div>
              </div>
            </div>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
