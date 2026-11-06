"use client";

/**
 * 企业情报页（SPEC-D 深度形态）：企查查式全页，替代旧抽屉。
 * 四页签：档案（工商+中标画像）/ 采购关系图谱 / 关键联系人 / 信号时间线。
 * 数据全部来自已验证端点：detail(+stat+signals) / graph / related / contacts。
 */
import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Button, Dropdown, Input, Modal, Skeleton, TabPane, Tabs, Tag, Toast } from "@douyinfe/semi-ui";
import { ArrowLeft, Bell, BellRing, Phone, Trash2, UserCheck } from "lucide-react";
import AppShell from "@/components/AppShell";
import { addToCompare, readCompare } from "@/lib/compare";
import { api, getToken } from "@/lib/api";
import type { Enterprise } from "@/lib/api";

interface GraphEdge {
  name: string;
  role: string;
  amount: number;
  count: number;
  last_date: string | null;
}
interface ContactRow {
  id: number;
  name: string;
  role: string;
  title: string | null;
  email: string | null;
  decision_role: string | null;
  verify_state: string;
  phones?: string[];
  call_result?: string;
  called_at?: string | null;
  source: string;
  evidence_claim: string | null;
}

/** SPEC-AN AN1：触达结果受控枚举（连接类→对话类分层） */
const CALL_RESULTS: { key: string; label: string; color: "grey" | "orange" | "red" | "green" }[] = [
  { key: "pending", label: "未拨", color: "grey" },
  { key: "no_answer", label: "无人接", color: "orange" },
  { key: "voicemail", label: "语音信箱", color: "orange" },
  { key: "invalid", label: "空号", color: "red" },
  { key: "connected", label: "已接通", color: "green" },
  { key: "interested", label: "有意向", color: "green" },
  { key: "not_interested", label: "无需求", color: "grey" },
];

function CallResultTag({ result, onChange }: { result: string; onChange: (r: string) => void }) {
  const cur = CALL_RESULTS.find((x) => x.key === result) ?? CALL_RESULTS[0];
  return (
    <Dropdown
      trigger="click"
      render={
        <div className="flex flex-col py-1">
          {CALL_RESULTS.map((r) => (
            <button
              key={r.key}
              className={`border-0 bg-transparent px-3 py-1.5 text-left text-[12.5px] hover:bg-[var(--raised)] ${r.key === result ? "font-medium text-[var(--accent)]" : "text-[var(--ink)]"}`}
              onClick={() => onChange(r.key)}
            >
              {r.label}
            </button>
          ))}
        </div>
      }
    >
      <Tag color={cur.color} type="light" size="small" className="!cursor-pointer">
        <Phone size={11} className="mr-1 inline" />
        {cur.label}
      </Tag>
    </Dropdown>
  );
}
interface UnitRow {
  id: number;
  product_name: string | null;
  model: string;
  serial: string | null;
  install_date: string | null;
  status: string;
  consumable_json: string;
  notes: string | null;
}
interface NoteRow {
  id: number;
  content: string;
  author: string;
  user_id: number;
  created_at: string;
}
interface RelatedData {
  competitors: { id: number; name: string; via: string; is_competitor: number }[];
  peers: { id: number; name: string; via: string }[];
}

const SEGMENT_LABELS: { key: string; label: string }[] = [
  { key: "size", label: "企业规模" },
  { key: "industry", label: "行业适配" },
  { key: "digital", label: "数字化程度" },
  { key: "pain", label: "采购意图" },
];

const ROLE_CN: Record<string, string> = {
  legal_rep: "法定代表人",
  buyer_unit: "采购单位",
  tender_agency: "代理机构",
  采购联系人: "采购联系人",
  项目联系人: "项目联系人",
  代理机构: "代理机构",
};

const SOURCE_TAG: Record<string, { label: string; color: "cyan" | "green" | "blue" | "grey" }> = {
  ggzy: { label: "官方中标", color: "cyan" },
  ggzy_buyer: { label: "官方买家", color: "green" },
  ccgp: { label: "政采供应商", color: "blue" },
  ccgp_buyer: { label: "政采买家", color: "green" },
  csv: { label: "CSV", color: "grey" },
};

/** 采购关系图谱（放大版）：中心-轮辐布局，边宽按金额，节点按角色着色 */
function GraphBoard({ center, edges }: { center: string; edges: GraphEdge[] }) {
  const W = 900;
  const H = 420;
  const cx = W / 2;
  const cy = H / 2;
  const R = 150;
  const shown = edges.slice(0, 12);
  const maxAmt = Math.max(...shown.map((e) => e.amount), 1);
  if (shown.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-lg border border-[var(--border)] bg-white text-[13px] text-[var(--muted)]">
        暂无采购关系边 —— 该主体还没有带对手方的中标/采购信号
      </div>
    );
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-lg border border-[var(--border)] bg-white">
      {shown.map((e, i) => {
        const angle = (Math.PI * 2 * i) / shown.length - Math.PI / 2;
        const x = cx + R * Math.cos(angle);
        const y = cy + R * Math.sin(angle);
        const w = 1 + (e.amount / maxAmt) * 4;
        return (
          <g key={e.name}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke="var(--frame)" strokeWidth={w} />
            <circle cx={x} cy={y} r="42" fill="#fff" stroke={e.role === "buyer" ? "var(--accent)" : "#89aab6"} strokeWidth="1.5" />
            <text x={x} y={y - 4} textAnchor="middle" fontSize="10" fill="var(--ink)">
              {e.name.slice(0, 9)}
            </text>
            <text x={x} y={y + 10} textAnchor="middle" fontSize="9" fill="var(--muted)">
              {e.role === "buyer" ? "采购人" : "中标方"}
            </text>
            <text
              x={(cx + x) / 2}
              y={(cy + y) / 2 - 4}
              textAnchor="middle"
              fontSize="9.5"
              fill="var(--accent)"
            >
              {e.amount > 0 ? `${Math.round(e.amount)}万` : `${e.count}次`}
            </text>
          </g>
        );
      })}
      <circle cx={cx} cy={cy} r="44" fill="var(--accent)" />
      <text x={cx} y={cy + 4} textAnchor="middle" fontSize="11" fill="#fff">
        {center.slice(0, 10)}
      </text>
    </svg>
  );
}

function IntelInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const eid = Number(sp?.get("id") ?? 0);

  const [detail, setDetail] = useState<Enterprise | null>(null);
  const [graph, setGraph] = useState<GraphEdge[]>([]);
  const [related, setRelated] = useState<RelatedData | null>(null);
  const [contacts, setContacts] = useState<ContactRow[]>([]);
  // SPEC-AS AS1 装机台账
  const [units, setUnits] = useState<UnitRow[]>([]);
  // SPEC-AT 节能账
  const [roiOpen, setRoiOpen] = useState(false);
  const [roiForm, setRoiForm] = useState({ power_kw: "37", hours_per_day: "16", load_factor: "0.8", price_per_kwh: "0.7", saving_pct: "25" });
  const [roiResult, setRoiResult] = useState<{ annual_save: number; five_year_save: number; baseline_year_cost: number; payback_months: number | null } | null>(null);
  const [loading, setLoading] = useState(true);
  const [subscribed, setSubscribed] = useState(false);
  const [converting, setConverting] = useState(false);
  // SPEC-V V2 标签编辑态
  const [tagsEditing, setTagsEditing] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [tagDraft, setTagDraft] = useState("");
  const [savingTags, setSavingTags] = useState(false);
  // SPEC-V V1 备注态
  const [notes, setNotes] = useState<NoteRow[]>([]);
  const [noteDraft, setNoteDraft] = useState("");

  const load = useCallback(() => {
    if (!eid) return;
    setLoading(true);
    api.get<Enterprise>(`/api/enterprises/${eid}`).then((d) => {
      setDetail(d);
      setTags(d.tags ?? []);
    }).catch(() => undefined).finally(() => setLoading(false));
    api.get<{ edges: GraphEdge[] }>(`/api/enterprises/${eid}/graph`).then((d) => setGraph(d.edges)).catch(() => undefined);
    api.get<RelatedData>(`/api/enterprises/${eid}/related`).then(setRelated).catch(() => undefined);
    api.get<{ items: ContactRow[] }>(`/api/enterprises/${eid}/contacts`).then((d) => setContacts(d.items)).catch(() => undefined);
    api.get<{ items: UnitRow[] }>(`/api/enterprises/${eid}/units`).then((d) => setUnits(d.items)).catch(() => undefined);
    api.get<{ items: NoteRow[] }>(`/api/enterprises/${eid}/notes`).then((d) => setNotes(d.items)).catch(() => undefined);
  }, [eid]);

  useEffect(() => load(), [load]);

  /** SPEC-AN AN1：标记触达结果并局部刷新联系人 */
  const markCallResult = useCallback(async (cid: number, result: string) => {
    try {
      await api.post(`/api/contacts/${cid}/call-result`, { result });
      Toast.success("已标记");
      api
        .get<{ items: ContactRow[] }>(`/api/enterprises/${eid}/contacts`)
        .then((d) => setContacts(d.items))
        .catch(() => undefined);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "标记失败");
    }
  }, [eid]);

  const saveTags = async () => {
    if (!detail) return;
    setSavingTags(true);
    try {
      const d = await api.put<{ tags: string[] }>(`/api/enterprises/${detail.id}/tags`, { tags });
      setDetail({ ...detail, tags: d.tags });
      setTagsEditing(false);
      Toast.success("标签已保存");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSavingTags(false);
    }
  };

  const addNote = async () => {
    if (!detail || !noteDraft.trim()) return;
    try {
      await api.post(`/api/enterprises/${detail.id}/notes`, { content: noteDraft.trim() });
      setNoteDraft("");
      const d = await api.get<{ items: NoteRow[] }>(`/api/enterprises/${detail.id}/notes`);
      setNotes(d.items);
      Toast.success("备注已添加");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "添加失败");
    }
  };

  const delNote = async (nid: number) => {
    try {
      await api.delete(`/api/enterprises/notes/${nid}`);
      setNotes(notes.filter((n) => n.id !== nid));
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  const toggleSubscribe = async () => {
    if (!detail) return;
    try {
      if (subscribed) {
        await api.delete(`/api/enterprises/${detail.id}/subscribe`);
        setSubscribed(false);
        Toast.info("已取消关注");
      } else {
        await api.post(`/api/enterprises/${detail.id}/subscribe`);
        setSubscribed(true);
        Toast.success("已关注——新动态将推送到顶栏铃铛");
      }
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const convert = async () => {
    if (!detail) return;
    setConverting(true);
    try {
      await api.post(`/api/enterprises/${detail.id}/lead`);
      Toast.success("已转为线索");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setConverting(false);
    }
  };

  if (loading || !detail) {
    return (
      <AppShell>
        <Skeleton placeholder={<Skeleton.Paragraph rows={6} />} loading active />
      </AppShell>
    );
  }

  const stat = detail.stat;
  const sourceMeta = SOURCE_TAG[detail.source ?? ""] ?? { label: detail.source || "—", color: "grey" as const };

  return (
    <AppShell>
      {/* 返回 + 头部档案条 */}
      <button
        onClick={() => router.push("/enterprises")}
        className="flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-[13px] text-[var(--muted)] hover:text-[var(--accent)]"
      >
        <ArrowLeft size={14} /> 返回企业列表
      </button>

      {/* SPEC-AT 节能账计算器 */}
      <Modal
        title="真空系统节能账（给老板的账 / 给工程师的口径）"
        visible={roiOpen}
        onCancel={() => setRoiOpen(false)}
        footer={null}
        width={520}
      >
        <div className="grid grid-cols-2 gap-2.5">
          {[
            { key: "power_kw", label: "现有泵功率 (kW)" },
            { key: "hours_per_day", label: "运行小时/天" },
            { key: "load_factor", label: "负载率 (0-1)" },
            { key: "price_per_kwh", label: "电价 (元/度)" },
            { key: "saving_pct", label: "节省率 %（实测区间 8.7-30）" },
          ].map((f) => (
            <label key={f.key} className="text-[12.5px] text-[var(--muted)]">
              {f.label}
              <Input
                size="small"
                value={roiForm[f.key as keyof typeof roiForm]}
                onChange={(v) => setRoiForm((fm) => ({ ...fm, [f.key]: String(v) }))}
                className="mt-0.5"
              />
            </label>
          ))}
        </div>
        <Button
          theme="solid"
          className="!mt-3"
          block
          onClick={async () => {
            try {
              const r = await api.post<{ result: NonNullable<typeof roiResult> }>("/api/proposal/roi", {
                power_kw: Number(roiForm.power_kw),
                hours_per_day: Number(roiForm.hours_per_day),
                load_factor: Number(roiForm.load_factor),
                price_per_kwh: Number(roiForm.price_per_kwh),
                saving_pct: Number(roiForm.saving_pct),
              });
              setRoiResult(r.result);
            } catch (e) {
              Toast.error(e instanceof Error ? e.message : "计算失败");
            }
          }}
        >
          计算
        </Button>
        {roiResult && (
          <div className="mt-3 rounded-lg border border-[var(--border)] p-3">
            <div className="grid grid-cols-2 gap-2 text-center">
              <div>
                <div className="text-[12px] text-[var(--muted)]">预计年节省</div>
                <div className="text-[22px] font-semibold text-[var(--accent)]">
                  {roiResult.annual_save.toLocaleString()} 元
                </div>
              </div>
              <div>
                <div className="text-[12px] text-[var(--muted)]">5 年累计</div>
                <div className="text-[22px] font-semibold text-[var(--ink)]">
                  {roiResult.five_year_save.toLocaleString()} 元
                </div>
              </div>
            </div>
            {roiResult.payback_months && (
              <div className="mt-1 text-center text-[12.5px] text-[var(--muted)]">
                投资回收期约 {roiResult.payback_months} 个月
              </div>
            )}
            <Button
              theme="outline"
              block
              className="!mt-3"
              onClick={() => {
                const qs = new URLSearchParams({
                  eid: String(eid), power_kw: roiForm.power_kw,
                  hours_per_day: roiForm.hours_per_day,
                  load_factor: roiForm.load_factor,
                  price_per_kwh: roiForm.price_per_kwh,
                  saving_pct: roiForm.saving_pct,
                  token: getToken() ?? "", // 新开页无 localStorage API 包装——AI1 的 ?token= 回退
                });
                window.open(`/api/proposal/print?${qs}`, "_blank");
              }}
            >
              打印客户提案（另存 PDF）
            </Button>
            <p className="mt-2 text-[11.5px] leading-4 text-[var(--muted)]">
              节省率为可调假设，按同工况实测案例区间估算（8.7%-30%）——对客户如实说明，现场勘测后出具正式计算书。
            </p>
          </div>
        )}
      </Modal>

      <div className="mt-3 rounded-lg border border-[var(--border)] bg-white px-6 py-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-[20px] font-semibold text-[var(--ink)]">{detail.name}</h1>
              <Tag color={sourceMeta.color} type="light">{sourceMeta.label}</Tag>
              {detail.is_competitor === 1 && <Tag color="amber" type="light">疑似同行</Tag>}
              {typeof window !== "undefined" && localStorage.getItem("vaclead_role") === "admin" && (
                <Button
                  size="small"
                  theme="borderless"
                  type="danger"
                  onClick={() =>
                    Modal.confirm({
                      title: "删除该企业？",
                      content: "进入回收站保留 30 天，期间可恢复。",
                      onOk: async () => {
                        try {
                          await api.delete(`/api/enterprises/${eid}`);
                          Toast.success("已移入回收站");
                          router.push("/enterprises");
                        } catch (e) {
                          Toast.error(e instanceof Error ? e.message : "删除失败");
                        }
                      },
                    })
                  }
                >
                  删除
                </Button>
              )}
              <Button
                size="small"
                theme="outline"
                className="!ml-auto"
                onClick={() => setRoiOpen(true)}
              >
                生成节能账
              </Button>
            </div>
            <div className="mt-1.5 text-[13px] text-[var(--muted)]">
              统一信用代码 {detail.uscc || "—"} · {[detail.province, detail.city, detail.district].filter(Boolean).join(" ") || "地区待回填"} · {detail.address || "地址待回填"}
            </div>
            {/* SPEC-V V2：标签行（编辑态增删，自动补全） */}
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {(detail.tags ?? []).map((t) => (
                <Tag
                  key={t}
                  color="cyan"
                  type="light"
                  size="small"
                  closable={tagsEditing}
                  onClose={() => setTags((detail.tags ?? []).filter((x) => x !== t))}
                >
                  {t}
                </Tag>
              ))}
              {tagsEditing ? (
                <>
                  <Input
                    size="small"
                    placeholder="新标签，回车添加"
                    value={tagDraft}
                    onChange={setTagDraft}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && tagDraft.trim()) {
                        setTags([...(detail.tags ?? []), tagDraft.trim()]);
                        setTagDraft("");
                      }
                    }}
                    style={{ width: 150 }}
                  />
                  <Button size="small" theme="solid" loading={savingTags} onClick={saveTags}>保存</Button>
                  <Button size="small" type="tertiary" onClick={() => { setTagsEditing(false); setTags(detail.tags ?? []); }}>取消</Button>
                </>
              ) : (
                <Button size="small" type="tertiary" theme="borderless" onClick={() => setTagsEditing(true)}>
                  编辑标签
                </Button>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              theme="light"
              onClick={() => {
                const r = addToCompare(detail.id);
                if (r.ok) Toast.success(`已加入对比（${readCompare().length}/4），去「企业对比」页查看`);
                else Toast.warning(r.reason ?? "无法加入");
              }}
            >
              加入对比
            </Button>
            <Button theme="light" icon={subscribed ? <BellRing size={14} /> : <Bell size={14} />} onClick={toggleSubscribe}>
              {subscribed ? "已关注" : "关注"}
            </Button>
            {detail.is_competitor !== 1 && (
              <Button theme="solid" icon={<UserCheck size={14} />} loading={converting} onClick={convert}>
                转为线索
              </Button>
            )}
          </div>
        </div>
        {/* 分数条 */}
        <div className="mt-4 grid grid-cols-4 gap-px overflow-hidden rounded-lg bg-[var(--frame)]">
          {[
            { k: "ICP 匹配分", v: String(detail.icp_score) },
            { k: "意图分（30天半衰）", v: String(detail.intent_score) },
            { k: "中标次数", v: stat ? String(stat.bid_count) : "—" },
            { k: "累计中标金额", v: stat && stat.bid_amount_total_wan > 0 ? `${Math.round(stat.bid_amount_total_wan)} 万` : "—" },
          ].map((c) => (
            <div key={c.k} className="bg-white px-4 py-3">
              <div className="text-[11.5px] text-[var(--muted)]">{c.k}</div>
              <div className="mt-0.5 text-[18px] font-medium tabular-nums text-[var(--ink)]">{c.v}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 四页签 */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white">
        <Tabs type="line" className="!px-4">
          <TabPane tab="档案" itemKey="profile" className="p-5">
            {/* SPEC-U U2：工商信息卡（企查查首屏字段集，数据来源=官方公示） */}
            <div className="mb-4 rounded-lg border border-[var(--border)] px-4 py-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[12.5px] font-medium text-[var(--ink)]">工商信息</span>
                <span className="flex items-center gap-2 text-[11.5px] text-[var(--muted)]">
                  <span>
                    资料完整度 {detail.completeness ?? 0}/4
                    {detail.completeness != null && detail.completeness < 4 && "（缺项可经采集/导入补全）"}
                  </span>
                  <Tag color="grey" type="light" size="small">数据来源：官方公示</Tag>
                </span>
              </div>
              <div className="grid grid-cols-1 gap-x-10 gap-y-2 text-[13px] leading-7 md:grid-cols-2">
                {[
                  ["统一信用代码", detail.uscc || "—"],
                  ["法定代表人", detail.legal_rep || "—"],
                  ["成立日期", detail.established_at || "—"],
                  ["注册资本", detail.registered_capital_wan == null ? "—" : `${detail.registered_capital_wan.toLocaleString("zh-CN")} 万`],
                  ["行业", detail.industry_name || "未分类"],
                  ["所在区域", [detail.province, detail.city, detail.district].filter(Boolean).join(" / ") || "—"],
                  ["地址", detail.address || "—"],
                  ["经营范围", detail.business_scope || "待工商补全"],
                ].map(([k, v]) => (
                  <div key={k as string} className="flex gap-2 border-b border-[var(--rule)] py-1">
                    <span className="w-24 shrink-0 text-[var(--muted)]">{k}</span>
                    <span className="min-w-0 flex-1 text-[var(--ink)]">{v}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 gap-x-10 gap-y-2 text-[13px] leading-7 md:grid-cols-2">
              {[
                ["最近中标", stat?.last_win_date || "—"],
                ["头部采购人", stat?.top_buyer || "—"],
              ].map(([k, v]) => (
                <div key={k as string} className="flex gap-2 border-b border-[var(--rule)] py-1">
                  <span className="w-20 shrink-0 text-[var(--muted)]">{k}</span>
                  <span className="min-w-0 flex-1 text-[var(--ink)]">{v}</span>
                </div>
              ))}
            </div>
            {detail.icp_reasons.length > 0 && (
              <div className="mt-4">
                <div className="mb-1.5 text-[12.5px] text-[var(--muted)]">ICP 评分理由（可解释）</div>
                <div className="flex flex-wrap gap-1.5">
                  {detail.icp_reasons.map((r) => (
                    <Tag key={r.rule} color="cyan" type="light" size="small">
                      {r.rule} +{r.points}
                    </Tag>
                  ))}
                </div>
              </div>
            )}
            {/* 四段评分（SPEC-G G1：opengtm 分段模式——每段独立得分条+理由） */}
            {detail.icp_segments && (
              <div className="mt-5">
                <div className="mb-2 text-[12.5px] text-[var(--muted)]">
                  四段评分（规模/行业/数字化/意图，合计 {SEGMENT_LABELS.reduce(
                    (a, seg) => a + (detail.icp_segments![seg.key]?.score ?? 0), 0,
                  )} 分）
                </div>
                <div className="space-y-2.5">
                  {SEGMENT_LABELS.map((seg) => {
                    const s = detail.icp_segments![seg.key];
                    if (!s) return null;
                    const pct = Math.round((s.score / s.max) * 100);
                    return (
                      <div key={seg.key} className="rounded-lg border border-[var(--border)] px-3.5 py-2.5">
                        <div className="flex items-center justify-between">
                          <span className="text-[13px] font-medium text-[var(--ink)]">{seg.label}</span>
                          <span className="text-[12.5px] tabular-nums text-[var(--muted)]">
                            {s.score}/{s.max}
                          </span>
                        </div>
                        <div className="mt-1.5 h-1.5 rounded-full bg-[var(--raised)]">
                          <div
                            className={`h-full rounded-full ${pct >= 70 ? "bg-[var(--accent)]" : "bg-[#89aab6]"}`}
                            style={{ width: `${Math.max(pct, 2)}%` }}
                          />
                        </div>
                        {s.reasons.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1.5">
                            {s.reasons.map((r, i) => (
                              <Tag key={i} color="cyan" type="light" size="small">
                                {r.rule} +{r.points}
                              </Tag>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* SPEC-V V1：企业备注（主观工作笔记，区别于客观信号时间线） */}
            <div className="mt-6">
              <div className="mb-2 text-[12.5px] text-[var(--muted)]">
                备注（团队共享可见；仅本人或管理员可删除）
              </div>
              <div className="mb-3 flex items-start gap-2">
                <Input
                  placeholder="记录与该客户的沟通上下文，如「展会认识，关注蚀刻机产能」"
                  value={noteDraft}
                  onChange={setNoteDraft}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && noteDraft.trim()) addNote();
                  }}
                  showClear
                />
                <Button theme="solid" disabled={!noteDraft.trim()} onClick={addNote}>添加</Button>
              </div>
              <div className="space-y-2">
                {notes.map((n) => (
                  <div key={n.id} className="rounded-lg border border-[var(--border)] px-3.5 py-2.5">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="text-[13.5px] leading-5 text-[var(--ink)]">{n.content}</div>
                        <div className="mt-1 text-[11.5px] text-[var(--muted)]">
                          {n.author} · {(n.created_at || "").replace("T", " ").slice(0, 16)}
                        </div>
                      </div>
                      <Button
                        size="small"
                        type="danger"
                        theme="borderless"
                        icon={<Trash2 size={13} />}
                        onClick={() => delNote(n.id)}
                      />
                    </div>
                  </div>
                ))}
                {notes.length === 0 && (
                  <div className="py-3 text-center text-[12.5px] text-[var(--muted)]">暂无备注</div>
                )}
              </div>
            </div>
          </TabPane>

          <TabPane tab="采购关系图谱" itemKey="graph" className="p-5">
            <GraphBoard center={detail.name} edges={graph} />
            {related && (related.competitors.length > 0 || related.peers.length > 0) && (
              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <div className="mb-1.5 text-[12.5px] font-medium text-[var(--negative)]">共享采购人的其他供应商（竞争情报）</div>
                  {related.competitors.map((c) => (
                    <button key={c.id} onClick={() => router.push(`/enterprises/view?id=${c.id}`)} className="mr-1.5 mb-1 cursor-pointer rounded border border-[var(--border)] bg-white px-2 py-1 text-[12px] text-[var(--ink)] hover:border-[var(--accent)]">
                      {c.name} <span className="text-[var(--muted)]">via {c.via}</span>
                    </button>
                  ))}
                </div>
                <div>
                  <div className="mb-1.5 text-[12.5px] font-medium text-[var(--muted)]">共享中标方的其他采购人</div>
                  {related.peers.map((c) => (
                    <button key={c.id} onClick={() => router.push(`/enterprises/view?id=${c.id}`)} className="mr-1.5 mb-1 cursor-pointer rounded border border-[var(--border)] bg-white px-2 py-1 text-[12px] text-[var(--ink)] hover:border-[var(--accent)]">
                      {c.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </TabPane>

          <TabPane tab="关键联系人" itemKey="contacts" className="p-5">
            {contacts.length === 0 ? (
              <div className="py-8 text-center text-[13px] text-[var(--muted)]">
                暂无联系人 —— 每日 04:08 回填任务会从政采公告详情页自动补充法定公开联系方式
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {contacts.map((c) => (
                  <div key={c.id} className="rounded-lg border border-[var(--border)] px-4 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <span className="text-[14px] font-medium text-[var(--ink)]">{c.name}</span>
                        {/* SPEC-J J2：决策角色标记 */}
                        {c.decision_role === "decision_maker" && (
                          <Tag color="orange" type="light" size="small">决策链</Tag>
                        )}
                      </span>
                      <span className="flex shrink-0 items-center gap-1">
                        {/* SPEC-J J2：验证态徽章（公告法定公开=verified） */}
                        {c.verify_state === "verified" && (
                          <Tag color="green" type="light" size="small">公告验证</Tag>
                        )}
                        <Tag color="cyan" type="light" size="small">{ROLE_CN[c.role] ?? c.role}</Tag>
                      </span>
                    </div>
                    {/* SPEC-AH AH3：规范化可拨号码 chips（可复制）+ SPEC-AN AN1 触达结果标记 */}
                    {((c.phones ?? []).length > 0 || c.call_result) && (
                      <div className="mt-1.5 flex flex-wrap items-center gap-1">
                        {(c.phones ?? []).map((p) => (
                          <span
                            key={p}
                            className="cursor-pointer rounded border border-[var(--border)] bg-[var(--raised)] px-1.5 py-0.5 font-mono text-[12.5px] tabular-nums text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white"
                            title="点击复制号码"
                            onClick={() => {
                              void navigator.clipboard?.writeText(p);
                              Toast.success(`已复制 ${p}`);
                            }}
                          >
                            {p}
                          </span>
                        ))}
                        <CallResultTag
                          result={(c.call_result as string) ?? "pending"}
                          onChange={(r) => void markCallResult(c.id, r)}
                        />
                      </div>
                    )}
                    {c.title && <div className="mt-1 text-[12px] leading-4 text-[var(--muted)]">{c.title}</div>}
                    {c.email && (
                      <div className="mt-1 text-[13px] text-[var(--ink)]">
                        <a href={`mailto:${c.email}`} className="hover:text-[var(--accent)]">{c.email}</a>
                      </div>
                    )}
                    {c.evidence_claim && (
                      <div className="mt-1 border-l-2 border-[var(--accent)] pl-2 text-[11.5px] leading-4 text-[var(--muted)]">
                        {c.evidence_claim}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </TabPane>

          {/* SPEC-AS AS1 装机台账（CMMS 模型：装机=资产负债表，耗材时钟=定时订单） */}
          <TabPane tab="装机设备" itemKey="units" className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[12.5px] text-[var(--muted)]">
                每台装机自带耗材时钟（密封 12-24 月/滤芯 2000-4000h）——到期自动生成回访待办
              </span>
              <Button
                size="small"
                theme="outline"
                onClick={() => {
                  const model = window.prompt("设备型号：");
                  if (!model) return;
                  const install = window.prompt("安装日期（YYYY-MM-DD，可空）：") || null;
                  api
                    .post(`/api/enterprises/${eid}/units`, { model, install_date: install, consumable_json: "{}" })
                    .then(() => load())
                    .catch((e) => Toast.error(e instanceof Error ? e.message : "新增失败"));
                }}
              >
                + 新增装机
              </Button>
            </div>
            {units.length === 0 ? (
              <div className="py-8 text-center text-[13px] text-[var(--muted)]">
                暂无装机记录 —— 成交交付后在这里登记，耗材时钟开始产出回访清单
              </div>
            ) : (
              <div className="space-y-2">
                {units.map((u) => (
                  <div key={u.id} className="flex items-center gap-3 rounded-lg border border-[var(--border)] px-4 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[13.5px] font-medium text-[var(--ink)]">{u.product_name ?? u.model}</span>
                        <Tag color={u.status === "active" ? "green" : "grey"} type="light" size="small">
                          {u.status === "active" ? "在用" : u.status === "replaced" ? "已替换" : "退役"}
                        </Tag>
                      </div>
                      <div className="mt-0.5 text-[12px] text-[var(--muted)]">
                        {u.model} {u.serial ? `· SN ${u.serial}` : ""} {u.install_date ? `· 安装 ${u.install_date}` : ""}
                      </div>
                    </div>
                    {u.status === "active" && (
                      <Button
                        size="small"
                        theme="borderless"
                        type="danger"
                        onClick={async () => {
                          try {
                            await api.patch(`/api/units/${u.id}`, { status: "retired" });
                            void load();
                          } catch {
                            Toast.error("操作失败");
                          }
                        }}
                      >
                        退役
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </TabPane>

          <TabPane tab="信号时间线" itemKey="signals" className="p-5">
            {(detail.signals ?? []).length === 0 ? (
              <div className="py-8 text-center text-[13px] text-[var(--muted)]">暂无信号</div>
            ) : (
              <div className="space-y-3">
                {detail.signals!.map((s) => (
                  <div key={s.id} className="rounded-lg border border-[var(--border)] px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-[var(--ink)]" title={s.title}>
                        {s.url ? (
                          <a href={s.url} target="_blank" rel="noreferrer" className="hover:text-[var(--accent)]">{s.title}</a>
                        ) : (
                          s.title
                        )}
                      </span>
                      <span className="shrink-0 text-[12px] tabular-nums text-[var(--muted)]">{s.pub_date || "日期未知"}</span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {s.type === "tender_win" && <Tag color="cyan" type="light" size="small">中标</Tag>}
                      {s.type === "tender_buy" && <Tag color="green" type="light" size="small">公开采购</Tag>}
                      {s.type === "tender" && <Tag color="blue" type="light" size="small">政采公告</Tag>}
                      <Tag color="red" type="light" size="small">{s.base_score} 分</Tag>
                      {s.matched_terms.map((t) => (
                        <Tag key={t} color="orange" type="light" size="small">{t}</Tag>
                      ))}
                    </div>
                    {s.evidence_claim && (
                      <div className="mt-1.5 border-l-2 border-[var(--accent)] pl-2.5 text-[12.5px] leading-5 text-[var(--ink)]">
                        {s.evidence_claim}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </TabPane>
        </Tabs>
      </div>
    </AppShell>
  );
}

export default function EnterpriseIntelPage() {
  return (
    <Suspense fallback={<AppShell><Skeleton placeholder={<Skeleton.Paragraph rows={6} />} loading active /></AppShell>}>
      <IntelInner />
    </Suspense>
  );
}
