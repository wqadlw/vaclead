"use client";

/**
 * 联系人中心（SPEC-BQ）：跨企业拨号工作台——"打 10 通电话"的日常作业面。
 * 可拨优先 / 未触达优先 / 企业意图分排序 / 边打边标（快标 7 态）。
 * 头部平台（Apollo/Revenue.io）daily call list 的对位形态。
 */
import { useCallback, useEffect, useState } from "react";
import { Input, Select, Tag, Toast } from "@douyinfe/semi-ui";
import { Phone } from "lucide-react";
import AppShell from "@/components/AppShell";
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

  const load = useCallback(() => {
    const p = new URLSearchParams({ has_phone: "1", limit: "100" });
    if (callStatus) p.set("call_status", callStatus);
    if (decisionRole) p.set("decision_role", decisionRole);
    if (province) p.set("province", province);
    if (q.trim()) p.set("q", q.trim());
    api.get<Worklist>(`/api/contacts?${p}`)
      .then(setData)
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

      {/* 筛选条 */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-[13.5px] font-medium text-[var(--ink)]">
          <Phone size={15} /> 今日拨打名单
        </span>
        <Select value={callStatus || undefined} placeholder="全部状态" showClear onChange={(v) => setCallStatus(String(v ?? ""))} className="!w-32">
          <Select.Option value="untouched">未触达</Select.Option>
          <Select.Option value="touched">已触达</Select.Option>
          <Select.Option value="interested">有意向</Select.Option>
          <Select.Option value="connected">已接通</Select.Option>
          <Select.Option value="no_answer">无人接</Select.Option>
          <Select.Option value="invalid">空号</Select.Option>
        </Select>
        <Select value={decisionRole || undefined} placeholder="全部角色" showClear onChange={(v) => setDecisionRole(String(v ?? ""))} className="!w-32">
          <Select.Option value="decision_maker">决策链</Select.Option>
          <Select.Option value="handler">经办人</Select.Option>
        </Select>
        <Select value={province || undefined} placeholder="全部省份" showClear onChange={(v) => setProvince(String(v ?? ""))} className="!w-32">
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
          className="!w-56"
        />
        <span className="ml-auto text-[12.5px] text-[var(--muted)]">
          共 {data?.total ?? 0} 人 · 排序：可拨优先 → 意图分 → 未拨优先
        </span>
      </div>

      {/* 拨打名单 */}
      <div className="mt-2.5 space-y-2">
        {(data?.items ?? []).map((c) => (
          <div key={c.id} className="rounded-lg border border-[var(--border)] bg-white px-4 py-3">
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
                        className="cursor-pointer rounded border border-[var(--border)] bg-[var(--raised)] px-1.5 py-0.5 font-mono text-[12.5px] tabular-nums text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white"
                        title="点击复制号码"
                        onClick={() => {
                          void navigator.clipboard?.writeText(p);
                          Toast.success(`已复制 ${p}`);
                        }}
                      >
                        {p}
                      </span>
                      <a
                        href={`tel:${p}`}
                        title="点击拨打"
                        className="text-[var(--muted)] transition-colors hover:text-[var(--accent)]"
                      >
                        <Phone size={11} />
                      </a>
                    </span>
                  ))}
                  <a
                    href={`/enterprises/view?id=${c.enterprise.id}`}
                    className="text-[var(--accent)] hover:underline"
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
              <div className="shrink-0">
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
      </div>
    </AppShell>
  );
}
