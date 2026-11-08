"use client";

/**
 * 联系人中心（SPEC-BQ）：跨企业拨号工作台——"打 10 通电话"的日常作业面。
 * 可拨优先 / 未触达优先 / 企业意图分排序 / 边打边标（快标 7 态）。
 * 头部平台（Apollo/Revenue.io）daily call list 的对位形态。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, Select, SideSheet, Tag, Toast } from "@douyinfe/semi-ui";
import { Download, Phone } from "lucide-react";
import AppShell from "@/components/AppShell";
import LoadMoreSensor from "@/components/LoadMoreSensor";
import CallResultTag from "@/components/CallResultTag";
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
  const [data, setData] = useState<Worklist | null>(null);
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

  const openPrep = async (cid: number) => {
    setPrepId(cid);
    setPrep(null);
    setFuText("");
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

  const [visibleCount, setVisibleCount] = useState(30);

  const load = useCallback(() => {
    const p = new URLSearchParams({ has_phone: "1", limit: "100" });
    if (callStatus) p.set("call_status", callStatus);
    if (decisionRole) p.set("decision_role", decisionRole);
    if (province) p.set("province", province);
    if (q.trim()) p.set("q", q.trim());
    api.get<Worklist>(`/api/contacts?${p}`)
      .then((d) => { setData(d); setVisibleCount(30); })
      .catch(() => undefined);
  }, [callStatus, decisionRole, province, q]);
  useEffect(load, [load]);

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

  const st = data?.stats;

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
            {Object.keys(data?.provinces ?? {}).map((p) => (
              <Select.Option key={p} value={p}>{p}</Select.Option>
            ))}
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
        <span className="hidden text-[12.5px] text-[var(--muted)] md:inline md:ml-auto">
          共 {data?.total ?? 0} 人 · 排序：可拨优先 → 意图分 → 未拨优先
        </span>
      </div>

      {/* 拨打名单 */}
      <div className="mt-2.5 space-y-2">
        {(data?.items ?? []).slice(0, visibleCount).map((c) => (
          <div
            key={c.id}
            className="cursor-pointer rounded-lg border border-[var(--border)] bg-white px-4 py-3 transition-colors hover:border-[var(--accent)]"
            onClick={() => void openPrep(c.id)}
          >
            <div className="flex items-start justify-between gap-3">
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
        {(data?.items ?? []).length === 0 && (
          <div className="py-16 text-center text-[13px] text-[var(--muted)]">
            当前筛选下没有可拨联系人 —— 换个状态/省份，或去「标讯中心」让 04:08 回填任务补号码
          </div>
        )}
        {(data?.items ?? []).length > visibleCount && (
          <LoadMoreSensor onLoadMore={() => setVisibleCount((n) => n + 50)} />
        )}
      </div>

      {/* SPEC-CH 通话驾驶舱：为什么现在打 / 怎么聊 / 动作区 */}
      <SideSheet
        title={prep ? `${prep.contact.name} · 通话准备` : "通话准备"}
        visible={prepId !== null}
        onCancel={() => setPrepId(null)}
        width={460}
        bodyStyle={{ padding: "16px 20px" }}
      >
        {prepLoading || !prep ? (
          <div className="py-10 text-center text-[13px] text-[var(--muted)]">加载中…</div>
        ) : (
          <div className="space-y-5">
            {/* 企业档案行 */}
            <div className="rounded-lg border border-[var(--border)] px-3.5 py-3">
              <a href={`/enterprises/view?id=${prep.enterprise.id}`} className="text-[14.5px] font-medium text-[var(--accent)] hover:underline">
                {prep.enterprise.name}
              </a>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-[var(--muted)]">
                <Tag color="cyan" type="light" size="small">{ROLE_CN[prep.contact.role] ?? prep.contact.role}</Tag>
                {prep.contact.decision_role === "decision_maker" && <Tag color="orange" type="light" size="small">决策链</Tag>}
                {prep.contact.verify_state === "verified" && <Tag color="green" type="light" size="small">公告验证</Tag>}
                <span>{[prep.enterprise.industry_name, prep.enterprise.province, prep.enterprise.city].filter(Boolean).join(" · ")}</span>
                <span>· 意图 {prep.enterprise.intent_score}</span>
              </div>
            </div>

            {/* 为什么现在打 */}
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-[var(--ink)]">为什么现在打</div>
              {prep.signals.length === 0 ? (
                <div className="text-[12.5px] text-[var(--muted)]">暂无公开动态——可从企业近况切入</div>
              ) : (
                <div className="space-y-2">
                  {prep.signals.map((s, i) => (
                    <div key={i} className="border-l-2 border-[var(--accent)] pl-2.5">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[11px] tabular-nums text-[var(--muted)]">{s.pub_date}</span>
                        {s.url && (
                          <a href={s.url} target="_blank" rel="noreferrer" className="text-[11px] text-[var(--accent)] hover:underline">原文 ↗</a>
                        )}
                      </div>
                      <div className="text-[12.5px] leading-4 text-[var(--ink)]">{s.title}</div>
                      {s.evidence && (
                        <div className="text-[11.5px] leading-4 text-[var(--muted)]">{s.evidence.slice(0, 80)}</div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 怎么聊 */}
            <div>
              <div className="mb-1.5 text-[13px] font-medium text-[var(--ink)]">怎么聊</div>
              {prep.product_match ? (
                <div className="rounded-lg bg-[var(--accent-weak,rgba(94,106,210,0.08))] px-3 py-2 text-[12.5px] leading-5 text-[var(--ink)]">
                  「{prep.product_match}」适配其{prep.enterprise.industry_name ?? ""}工艺——从它现在的工序痛点切入，报型号与节能账。
                </div>
              ) : (
                <div className="text-[12.5px] text-[var(--muted)]">
                  暂无产品适配记录——通用开场：确认设备现状与真空工艺需求。
                </div>
              )}
              {prep.last_followup && (
                <div className="mt-1.5 text-[11.5px] text-[var(--muted)]">
                  上次跟进（{prep.last_followup.created_at.replace("T", " ")}）：{prep.last_followup.content.slice(0, 50)}
                </div>
              )}
            </div>

            {/* 动作区 */}
            <div className="rounded-lg border border-[var(--border)] px-3.5 py-3">
              <div className="text-[13px] font-medium text-[var(--ink)]">动作</div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {prep.contact.phones[0] && (
                  <a
                    href={`tel:${prep.contact.phones[0]}`}
                    className="flex h-9 items-center gap-1.5 rounded-lg bg-[#131416] px-4 text-[13.5px] font-medium text-white"
                  >
                    <Phone size={14} /> 拨打 {prep.contact.phones[0]}
                  </a>
                )}
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
                    rows={2}
                    className="w-full rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-[12.5px] outline-none focus:border-[var(--accent)]"
                  />
                  <div className="mt-1.5 flex justify-end">
                    <Button size="small" theme="solid" loading={fuBusy} disabled={!fuText.trim()} onClick={() => void submitFollowup()}>
                      记跟进
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="mt-3 flex items-center justify-between gap-2">
                  <span className="text-[12px] text-[var(--muted)]">意向不错？转成线索开始正式跟进：</span>
                  <Button size="small" theme="outline" onClick={() => void convertFromPrep()}>
                    转线索
                  </Button>
                </div>
              )}
            </div>
          </div>
        )}
      </SideSheet>
    </AppShell>
  );
}
