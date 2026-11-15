"use client";

/**
 * 客户群专属作战页（SPEC-SEG2 附录 A 后续，2026-11-15 用户："点名单应跳专门页面"）：
 * 每个客户群一个专属工作页——指标条 + 该群联系人工作表（未触达/已触达/高意图/全部
 * 四页签）+ 右栏群情报（信号动态/官网直查深链/群定义速览）。
 * 接口全部现成：/api/segments（群定义与统计）/ /api/contacts?segment=&call_status=
 * &intent_min=（工作表）/ /api/signals?industry_key=（群信号）/ /api/outlinks/collect
 * （官网直查）。后端零改动。静态导出用查询参数（/profile/view?code=xxx）+ Suspense
 * （企业详情页同款模式）。
 */
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button, Input, Select, Spin, Tag, Toast } from "@douyinfe/semi-ui";
import { ArrowLeft, ExternalLink, Phone, RefreshCw } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface SegmentDef {
  id: number;
  name: string;
  code: string;
  keywords?: string[] | null;
  industry_keys?: string[] | null;
  business_lines?: string[] | null;
  priority?: string | null;
  enabled?: number | boolean;
  notes?: string | null;
  enterprise_count?: number | null;
  dialable_count?: number | null;
  dialable_untouched?: number | null;
  touched?: number | null;
  intent_high?: number | null;
  following?: number | null;
  new_7d?: number | null;
}

interface ContactRow {
  id: number;
  name: string;
  role?: string | null;
  title?: string | null;
  email?: string | null;
  phones?: string[] | null;
  call_result?: string | null;
  evidence_claim?: string | null;
  enterprise: {
    id: number;
    name: string;
    province?: string | null;
    city?: string | null;
    industry_name?: string | null;
    intent_score?: number | null;
    customer_role?: string | null;
  };
}

function strList(v: unknown): string[] {
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === "string" && x.length > 0);
  if (typeof v === "string" && v.trim()) {
    try {
      const parsed = JSON.parse(v);
      return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
    } catch {
      return [];
    }
  }
  return [];
}

/** phones_json → 号码数组（容错） */
function phonesOf(raw: unknown): string[] {
  if (typeof raw !== "string" || !raw.trim()) return [];
  try {
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string" && x.trim().length > 0) : [];
  } catch {
    return [];
  }
}

/** 电话 chips：点击复制 + tel: 拨打 */
function PhoneChips({ phones }: { phones: string[] }) {
  const copy = (p: string) => {
    navigator.clipboard
      .writeText(p)
      .then(() => Toast.success(`已复制 ${p}`))
      .catch(() => Toast.error("复制失败，请手动选择"));
  };
  return (
    <div className="flex flex-wrap gap-1">
      {phones.map((p) => (
        <span key={p} className="flex items-center gap-0.5">
          <button
            className="rounded border border-[var(--border)] bg-white px-1.5 py-0.5 font-mono text-[12px] text-[var(--ink)] hover:border-[var(--accent)]"
            onClick={() => copy(p)}
            title={`点击复制 ${p}`}
          >
            {p}
          </button>
          <a
            href={`tel:${p.replace(/[^0-9+]/g, "")}`}
            className="rounded border border-[var(--border)] px-1 py-0.5 text-[var(--accent)] hover:border-[var(--accent)]"
            title={`拨打 ${p}`}
          >
            <Phone size={11} />
          </a>
        </span>
      ))}
    </div>
  );
}

type TabKey = "untouched" | "touched" | "intent" | "all";

const TABS: { key: TabKey; label: string; desc: string }[] = [
  { key: "untouched", label: "未触达可打", desc: "有电话、还没人打过" },
  { key: "touched", label: "已触达", desc: "打过/标记过结果" },
  { key: "intent", label: "高意图", desc: "意图分 ≥60 优先打" },
  { key: "all", label: "全部名单", "desc": "本群全部企业联系人" } as { key: TabKey; label: string; desc: string },
];

const SIGNAL_TYPE_CN: Record<string, string> = {
  tender_win: "中标",
  tender_buy: "公开采购",
  tender: "政采公告",
  proposal: "项目备案",
  web_change: "官网变更",
};

function SegmentWorkInner() {
  const sp = useSearchParams();
  const code = decodeURIComponent(String(sp.get("code") ?? ""));
  const router = useRouter();

  const [def, setDef] = useState<SegmentDef | null>(null);
  const [defLoading, setDefLoading] = useState(true);
  const [tab, setTab] = useState<TabKey>("untouched");
  const [page, setPage] = useState(1);
  const [contacts, setContacts] = useState<ContactRow[] | null>(null);
  const [contactsTotal, setContactsTotal] = useState<number | null>(null);
  const [contactsLoading, setContactsLoading] = useState(false);
  const PAGE_SIZE = 20; // 用户拍板：一页 20 条
  // 分类筛选（用户："太多了不好找"）：省份 facet + 企业名搜索
  const [province, setProvince] = useState("");
  const [q, setQ] = useState("");
  const [qs, setQs] = useState(""); // 防抖后的 q
  const [provinces, setProvinces] = useState<Record<string, number>>({});
  const [isAdmin, setIsAdmin] = useState(false);
  const [signals, setSignals] = useState<
    { id: number; type_cn: string; title: string; date: string; ent_name: string; url: string }[]
  >([]);
  const [signalsLoading, setSignalsLoading] = useState(false);

  useEffect(() => setIsAdmin(localStorage.getItem("vaclead_role") === "admin"), []);

  const loadDef = useCallback(() => {
    setDefLoading(true);
    api
      .get<{ items?: SegmentDef[] }>("/api/segments")
      .then((d) => {
        const items = Array.isArray(d) ? d : ((d as { items?: SegmentDef[] }).items ?? []);
        setDef(items.find((x) => x.code === code) ?? null);
      })
      .catch(() => setDef(null))
      .finally(() => setDefLoading(false));
  }, [code]);
  useEffect(() => loadDef(), [loadDef]);

  const industryKeys = useMemo(() => strList(def?.industry_keys), [def]);
  const keywords = useMemo(() => strList(def?.keywords), [def]);

  /** 工作表：按页签拉该群联系人（分页 20/页 + 省份/搜索筛选） */
  const loadContacts = useCallback(() => {
    setContactsLoading(true);
    const p = new URLSearchParams({ segment: code, page: String(page), page_size: String(PAGE_SIZE) });
    if (tab === "untouched") p.set("call_status", "untouched");
    if (tab === "touched") p.set("call_status", "touched");
    if (tab === "intent") p.set("intent_min", "60");
    if (province) p.set("province", province);
    if (qs.trim()) p.set("q", qs.trim());
    api
      .get<{ items?: ContactRow[]; total?: number; provinces?: Record<string, number> }>(`/api/contacts?${p}`)
      .then((d) => {
        setContacts(Array.isArray(d?.items) ? d.items : []);
        setContactsTotal(typeof d?.total === "number" ? d.total : null);
        if (d?.provinces && typeof d.provinces === "object") setProvinces(d.provinces);
      })
      .catch(() => {
        setContacts([]);
        setContactsTotal(null);
      })
      .finally(() => setContactsLoading(false));
  }, [code, tab, page, province, qs]);
  useEffect(() => loadContacts(), [loadContacts]);

  /** 搜索防抖 400ms */
  useEffect(() => {
    const t = setTimeout(() => {
      setQs(q);
      setPage(1);
    }, 400);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => setPage(1), [province]);

  /** 切页签回到第一页 */
  const switchTab = (k: TabKey) => {
    setTab(k);
    setPage(1);
  };

  /** 群信号：按群行业逐个拉近 90 天，合并去重按日期倒序取前 6 */
  useEffect(() => {
    if (industryKeys.length === 0) {
      setSignals([]);
      return;
    }
    setSignalsLoading(true);
    Promise.all(
      industryKeys.map((ik) =>
        api
          .get<{ items?: { id: number; type_cn?: string; title: string; pub_date?: string; ent_name?: string; url?: string }[] }>(
            `/api/signals?industry_key=${encodeURIComponent(ik)}&days=90&page_size=6`,
          )
          .then((d) => (Array.isArray(d?.items) ? d.items : []))
          .catch(() => []),
      ),
    )
      .then((batches) => {
        const seen = new Set<number>();
        const merged = batches
          .flat()
          .filter((s) => (seen.has(s.id) ? false : (seen.add(s.id), true)))
          .sort((a, b) => String(b.pub_date ?? "").localeCompare(String(a.pub_date ?? "")))
          .slice(0, 6)
          .map((s) => ({
            id: s.id,
            type_cn: s.type_cn ?? "",
            title: s.title,
            date: String(s.pub_date ?? "").slice(0, 10),
            ent_name: s.ent_name ?? "",
            url: s.url ?? "",
          }));
        setSignals(merged);
      })
      .finally(() => setSignalsLoading(false));
  }, [industryKeys]);

  /** 官网直查深链：群首关键词带出站 */
  const [outlinks, setOutlinks] = useState<{ key: string; label: string; url: string }[]>([]);
  useEffect(() => {
    const kw = (keywords[0] ?? def?.name ?? "").trim();
    if (!kw) return;
    api
      .get<{ items?: { key: string; label: string; url: string }[] }>(
        `/api/outlinks/collect?keyword=${encodeURIComponent(kw)}`,
      )
      .then((d) => setOutlinks(Array.isArray(d?.items) ? d.items : []))
      .catch(() => setOutlinks([]));
  }, [keywords, def?.name]);

  const stats = def
    ? [
        { k: "企业总数", v: def.enterprise_count },
        { k: "可拨", v: def.dialable_count },
        { k: "能打未触达", v: def.dialable_untouched, hl: true },
        { k: "已触达", v: def.touched },
        { k: "高意图", v: def.intent_high },
        { k: "跟进中", v: def.following },
      ]
    : [];

  return (
    <AppShell>
      {/* 页头 */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="small"
          theme="borderless"
          icon={<ArrowLeft size={13} />}
          onClick={() => router.push("/profile")}
        >
          排行
        </Button>
        <h1 className="text-[20px] font-semibold text-[var(--ink)]">{def?.name ?? code}</h1>
        {def?.priority && (
          <Tag color={def.priority === "P0" ? "red" : def.priority === "P1" ? "blue" : "grey"} type="light" size="small">
            {def.priority}
          </Tag>
        )}
        <div className="ml-auto flex items-center gap-2">
          {isAdmin && (
            <Button size="small" theme="borderless" onClick={() => router.push("/profile")}>
              编辑定义
            </Button>
          )}
          <Button size="small" icon={<RefreshCw size={13} />} onClick={() => { loadDef(); loadContacts(); }}>
            刷新
          </Button>
        </div>
      </div>
      {def?.notes && <p className="mt-1 text-[12.5px] text-[var(--muted)]">{def.notes}</p>}

      {/* 指标条 */}
      <div className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-lg bg-[var(--border)] md:grid-cols-6">
        {defLoading ? (
          <div className="col-span-full flex justify-center bg-white py-6">
            <Spin />
          </div>
        ) : (
          stats.map((s) => (
            <div key={s.k} className="bg-white px-4 py-3">
              <div className="text-[12px] text-[var(--muted)]">{s.k}</div>
              <div
                className={`mt-0.5 text-[22px] font-semibold leading-7 tabular-nums ${
                  s.hl && typeof s.v === "number" && s.v > 0 ? "text-[var(--accent)]" : "text-[var(--ink)]"
                }`}
              >
                {typeof s.v === "number" ? s.v.toLocaleString("zh-CN") : "—"}
              </div>
            </div>
          ))
        )}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[1fr_340px] xl:items-start">
        {/* 左：联系人工作表（四页签） */}
        <div className="rounded-lg border border-[var(--border)] bg-white">
          <div className="flex flex-wrap items-center gap-1 border-b border-[var(--rule)] px-3 pt-2.5">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => switchTab(t.key)}
                title={t.desc}
                className={`rounded-t-md px-3 py-2 text-[13px] ${
                  tab === t.key
                    ? "border border-b-0 border-[var(--rule)] bg-white font-medium text-[var(--accent)]"
                    : "text-[var(--muted)] hover:text-[var(--ink)]"
                }`}
              >
                {t.label}
              </button>
            ))}
            <span className="ml-auto pb-1.5 pr-1 text-[11.5px] text-[var(--muted)]">
              {TABS.find((t) => t.key === tab)?.desc}
              {contactsTotal != null ? ` · 共 ${contactsTotal.toLocaleString("zh-CN")} 条` : ""}
            </span>
          </div>
          {/* 分类筛选（用户："太多了不好找"）：省份 facet + 企业名搜索 */}
          <div className="flex flex-wrap items-center gap-2 border-b border-[var(--rule)] px-3 py-2">
            <Input
              value={q}
              placeholder="搜企业名/联系人"
              showClear
              className="!w-52"
              onChange={(v) => setQ(v ?? "")}
            />
            <Select
              value={province || undefined}
              placeholder="全部省份"
              showClear
              filter
              className="!w-36"
              optionList={Object.entries(provinces)
                .sort((a, b) => b[1] - a[1])
                .map(([p, n]) => ({ value: p, label: `${p} (${n})` }))}
              onChange={(v) => {
                setProvince(String(v ?? ""));
                setPage(1);
              }}
            />
            {(province || qs.trim()) && (
              <button
                className="text-[12px] text-[var(--muted)] hover:text-[var(--ink)]"
                onClick={() => {
                  setQ("");
                  setProvince("");
                }}
              >
                清除筛选
              </button>
            )}
          </div>
          <div className="p-3">
            {contactsLoading ? (
              <div className="flex justify-center py-8">
                <Spin />
              </div>
            ) : (contacts ?? []).length === 0 ? (
              <div className="py-10 text-center text-[13px] text-[var(--muted)]">
                {tab === "untouched" ? "本群暂无未触达的可拨联系人" : "暂无数据"}
              </div>
            ) : (
              <div className="divide-y divide-[var(--rule)]">
                {(contacts ?? []).map((c) => {
                  const ent = c.enterprise ?? ({} as ContactRow["enterprise"]);
                  const loc = [ent.city, ent.province].filter(Boolean).join(" · ");
                  return (
                    <div key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2.5">
                      {/* 企业名为主（联系人名常是"地图公开电话"类角色标签） */}
                      <div className="min-w-0 basis-64">
                        <div className="truncate text-[14px] font-medium text-[var(--ink)]" title={ent.name}>
                          {ent.name || "—"}
                        </div>
                        <div className="truncate text-[11.5px] text-[var(--muted)]">
                          {c.name || "—"}
                          {ent.industry_name ? ` · ${ent.industry_name}` : ""}
                          {loc ? ` · ${loc}` : ""}
                        </div>
                      </div>
                      <PhoneChips phones={c.phones ?? []} />
                      <div className="ml-auto flex items-center gap-2">
                        {typeof ent.intent_score === "number" && ent.intent_score > 0 && (
                          <span className="text-[11.5px] tabular-nums text-[var(--muted)]" title="意图分">
                            意图 {ent.intent_score}
                          </span>
                        )}
                        <a
                          href={`/enterprises/view?id=${ent.id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-0.5 text-[12px] text-[var(--accent)] hover:underline"
                        >
                          详情 <ExternalLink size={11} />
                        </a>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {/* 分页（用户拍板：一页 20 条） */}
            {contactsTotal != null && contactsTotal > 0 && (
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--rule)] px-1 pt-2.5">
                <span className="text-[12px] text-[var(--muted)]">
                  共 {contactsTotal.toLocaleString("zh-CN")} 条 · 第 {page}/{Math.max(1, Math.ceil(contactsTotal / PAGE_SIZE))} 页（每页 {PAGE_SIZE} 条）
                </span>
                <div className="flex items-center gap-1.5">
                  <Button
                    size="small"
                    disabled={page <= 1}
                    loading={contactsLoading}
                    onClick={() => setPage((v) => Math.max(1, v - 1))}
                  >
                    上一页
                  </Button>
                  <Button
                    size="small"
                    disabled={page >= Math.ceil(contactsTotal / PAGE_SIZE)}
                    loading={contactsLoading}
                    onClick={() => setPage((v) => v + 1)}
                  >
                    下一页
                  </Button>
                  <button
                    className="text-[12px] text-[var(--accent)] hover:underline"
                    onClick={() => router.push(`/contacts?segment=${encodeURIComponent(code)}`)}
                  >
                    联系人中心继续 →
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* 右：群情报 */}
        <div className="space-y-4 xl:sticky xl:top-14">
          {/* 信号动态 */}
          <div className="rounded-lg border border-[var(--border)] bg-white p-4">
            <div className="text-[14px] font-medium text-[var(--ink)]">群内信号动态</div>
            <div className="mt-0.5 text-[11.5px] text-[var(--muted)]">近 90 天 · 按群行业归属聚合</div>
            {signalsLoading ? (
              <div className="flex justify-center py-6">
                <Spin />
              </div>
            ) : signals.length === 0 ? (
              <div className="py-6 text-center text-[12.5px] text-[var(--muted)]">
                {industryKeys.length === 0 ? "本群未定义行业归属——编辑定义补行业后可见信号" : "近 90 天无信号"}
              </div>
            ) : (
              <div className="mt-2 space-y-2">
                {signals.map((s) => (
                  <div key={s.id} className="border-l-2 border-[var(--accent)] pl-2">
                    <div className="text-[11.5px] text-[var(--muted)]">
                      {s.date} · {s.type_cn}
                      {s.ent_name ? ` · ${s.ent_name}` : ""}
                    </div>
                    <div className="truncate text-[12.5px] text-[var(--ink)]" title={s.title}>
                      {s.url ? (
                        <a href={s.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
                          {s.title} ↗
                        </a>
                      ) : (
                        s.title
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 官网直查（群关键词带出站） */}
          {outlinks.length > 0 && (
            <div className="rounded-lg border border-[var(--border)] bg-white p-4">
              <div className="text-[14px] font-medium text-[var(--ink)]">官网直查</div>
              <div className="mt-0.5 text-[11.5px] text-[var(--muted)]">
                用群关键词到各源人工核对——关键词：{(keywords[0] ?? "").slice(0, 20)}
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {outlinks.map((o) => (
                  <a
                    key={o.key}
                    href={o.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-md border border-[var(--border)] px-2 py-0.5 text-[12px] text-[var(--accent)] transition-colors hover:border-[var(--accent)]"
                  >
                    {o.label} ↗
                  </a>
                ))}
              </div>
            </div>
          )}

          {/* 群定义速览 */}
          <div className="rounded-lg border border-[var(--border)] bg-white p-4">
            <div className="text-[14px] font-medium text-[var(--ink)]">群定义速览</div>
            <div className="mt-2 space-y-1.5 text-[12.5px]">
              <div>
                <span className="text-[var(--muted)]">买昊志什么：</span>
                {strList(def?.business_lines).join("、") || "—"}
              </div>
              <div>
                <span className="text-[var(--muted)]">采集词：</span>
                {keywords.join(" / ") || "—"}
              </div>
              <div>
                <span className="text-[var(--muted)]">行业归属：</span>
                {industryKeys.join("、") || "未定义"}
              </div>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

export default function SegmentWorkPage() {
  return (
    <Suspense fallback={<div className="flex justify-center py-16"><Spin /></div>}>
      <SegmentWorkInner />
    </Suspense>
  );
}
