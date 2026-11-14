"use client";

/**
 * 企业情报页（SPEC-D 深度形态）：企查查式全页，替代旧抽屉。
 * 四页签：档案（工商+中标画像）/ 采购关系图谱 / 关键联系人 / 信号时间线。
 * 数据全部来自已验证端点：detail(+stat+signals) / graph / related / contacts。
 */
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Button, Dropdown, Input, Modal, Skeleton, TabPane, Tabs, Tag, Toast, Select } from "@douyinfe/semi-ui";
import { ArrowLeft, Bell, BellRing, Trash2, UserCheck } from "lucide-react";
import AppShell from "@/components/AppShell";
import JourneyTimeline, { type JourneyItem } from "@/components/JourneyTimeline";
import ContactDialPanel, { type ContactRow } from "@/components/ContactDialPanel";

// SPEC-CN 搬运书签：在风鸟公司页点一下，抓工商字段回平台自动入库。
// 只读当前页文本+手动单家触发（合规：等效复制粘贴，非爬虫）。
function makeBookmarkletSrc(origin: string): string {
  const js = `(function(){
var LAB={法定代表人:"法人",法人:"法人",负责人:"法人","统一社会信用代码":"信用代码",信用代码:"信用代码",纳税人识别号:"信用代码",注册资本:"注册资本",成立日期:"成立日期",注册日期:"成立日期",企业电话:"电话",联系电话:"电话",电话:"电话",企业地址:"地址",注册地址:"地址",地址:"地址",经营范围:"经营范围"};
var out=[],cur=null,ph="",name="";
var lines=(document.body.innerText||"").split(/\\n+/);
var titleRaw=(document.title||"").replace(/\\s*[-—–_|]+\\s*(工商信息查询|企业信用查询|企业查询平台|企业查询|风鸟).*$/,"").trim();
if(titleRaw.length>=6)name=titleRaw;
for(var i=0;i<lines.length;i++){
var ln=lines[i].trim();
if(!ln)continue;
if(!name&&ln.length>=6&&ln.length<=40&&!LAB[ln.replace(/\\s/g,"")]&&!ln.match(/\\d{3}/)){name=ln}
if(cur){out.push(cur+" "+ln.slice(0,300));cur=null;continue}
var m=ln.match(/^([一-龥A-Za-z（）()]{2,12})[：\\s]\\s*(.+)$/);
if(m&&LAB[m[1]]){out.push(LAB[m[1]]+" "+m[2].trim().slice(0,300));continue}
var m2=ln.match(/^([一-龥]{2,12})$/);
if(m2&&LAB[m2[1]]){cur=LAB[m2[1]];continue}
if(!ph){var pm=ln.match(/1[3-9]\\d{9}|0\\d{2,3}[-—]?\\d{7,8}(?:[-—]\\d{3,4})?/);if(pm)ph=pm[0]}
}
if(ph&&out.every(function(s){return s.indexOf("电话")!==0}))out.push("电话 "+ph);
var q=encodeURIComponent(out.join("\\n").slice(0,1800));
var qn=encodeURIComponent(name.slice(0,40));
if(!qn){alert("没抓到公司名——请在公司详情页再点一次书签");return}
document.location.href="${origin}/enterprises/view?name="+qn+"&gongshang="+q;
})()`;
  return "javascript:" + encodeURIComponent(js);
}

interface SimilarItem {
  id: number;
  name: string;
  industry_name: string | null;
  province: string | null;
  city: string | null;
  intent_score: number;
  reasons: string[];
}
// CallResultTag（七态快标）已随拨号作战面板收进 @/components/ContactDialPanel
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
// ContactRow 类型由 @/components/ContactDialPanel 导出（拨号作战面板共享）

/** SPEC-AN AN1：触达结果受控枚举（连接类→对话类分层） */
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
  // SPEC-EXT：外部情报直达矩阵
  const [extLinks, setExtLinks] = useState<{
    categories: { category: string; sources: { key: string; label: string; what: string; url: string }[] }[];
  } | null>(null);
  // SPEC-AS AS1 装机台账
  const [units, setUnits] = useState<UnitRow[]>([]);
  // SPEC-AT 节能账
  const [roiOpen, setRoiOpen] = useState(false);
  const [roiForm, setRoiForm] = useState({ power_kw: "37", hours_per_day: "16", load_factor: "0.8", price_per_kwh: "0.7", saving_pct: "25" });
  const [roiResult, setRoiResult] = useState<{ annual_save: number; five_year_save: number; baseline_year_cost: number; payback_months: number | null } | null>(null);
  const [loading, setLoading] = useState(true);
  const [subscribed, setSubscribed] = useState(false);
  const [converting, setConverting] = useState(false);
  // SPEC-RECON：深度调查（政采网自动侦察该企业公开记录）
  const [reconBusy, setReconBusy] = useState(false);
  // SPEC-PROV：字段级溯源解析（工商卡逐字段标注来源/时间）
  const prov = useMemo(() => {
    try {
      return JSON.parse((detail?.prov_json as string) || "{}") as Record<
        string,
        { source: string; by?: string; at?: string }
      >;
    } catch {
      return {};
    }
  }, [detail]);
  // SPEC-V V2 标签编辑态
  const [tagsEditing, setTagsEditing] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [tagDraft, setTagDraft] = useState("");
  const [savingTags, setSavingTags] = useState(false);
  // SPEC-V V1 备注态
  const [notes, setNotes] = useState<NoteRow[]>([]);
  const [noteDraft, setNoteDraft] = useState("");
  // SPEC-BT 客户旅程（八源合流时间线）
  const [journey, setJourney] = useState<JourneyItem[] | null>(null);
  // SPEC-CN 搬运书签
  const [bmOpen, setBmOpen] = useState(false);
  // SPEC-CM 风鸟粘贴（整块复制→解析入库）
  const [gsPaste, setGsPaste] = useState("");
  const [gsBusy, setGsBusy] = useState(false);
  const [gsResult, setGsResult] = useState("");
  // SPEC-CL 手工记联系人（风鸟/企查查查到直接填）
  const [cName, setCName] = useState("");
  const [cPhone, setCPhone] = useState("");
  const [cTitle, setTitle] = useState("");
  const [cRole, setCRole] = useState("buyer_contact");
  const [cBusy, setCBusy] = useState(false);
  // SPEC-CD 找相似（look-alike 可解释榜）
  const [similar, setSimilar] = useState<SimilarItem[] | null>(null);
  const [simConverting, setSimConverting] = useState<number | null>(null);

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
    api.get<{ items: JourneyItem[] }>(`/api/enterprises/${eid}/timeline?limit=120`).then((d) => setJourney(d.items)).catch(() => undefined);
    api.get<{ items: SimilarItem[] }>(`/api/enterprises/${eid}/similar`).then((d) => setSimilar(d.items)).catch(() => undefined);
    api.get<{ categories: { category: string; sources: { key: string; label: string; what: string; url: string }[] }[] }>(
      `/api/enterprises/${eid}/external-links`,
    ).then((d) => setExtLinks({ categories: d.categories })).catch(() => undefined);
  }, [eid]);

  useEffect(() => load(), [load]);

  // SPEC-CN：书签搬运回流——URL 带 gongshang 参数则自动填框并解析入库
  useEffect(() => {
    const payload = sp?.get("gongshang");
    const nameParam = sp?.get("name");
    if (!payload && !nameParam) return;
    if (!payload) return;
    const decoded = decodeURIComponent(payload);
    setGsPaste(decoded);
    const t = setTimeout(() => {
      void (async () => {
        let targetEid = eid;
        if (!targetEid && nameParam) {
          try {
            const found = await api.get<{ items: { id: number; name: string }[] }>(
              `/api/enterprises?q=${encodeURIComponent(nameParam)}&page_size=10`,
            );
            const exact = found.items.find((x) => x.name === nameParam);
            if (exact) targetEid = exact.id;
          } catch {
            /* 定位失败走下方无 id 分支 */
          }
        }
        if (!targetEid) {
          Toast.error(
            `书签搬运：平台没找到「${nameParam}」——请确认已采集该企业`,
          );
          window.history.replaceState({}, "", "/enterprises");
          router.push("/enterprises");
          return;
        }
        try {
          const r = await api.post<{ filled: Record<string, string>; phone_added: boolean }>(
            `/api/enterprises/${targetEid}/gongshang-paste`,
            { text: decoded },
          );
          const keys = Object.keys(r.filled);
          Toast.success(
            keys.length > 0
              ? `书签搬运完成：已补 ${keys.join("、")}` + (r.phone_added ? "（含电话）" : "")
              : "书签搬运完成：字段都已有值",
          );
        } catch (e) {
          Toast.error(e instanceof Error ? e.message : "书签数据解析失败");
        }
        setGsPaste("");
        window.history.replaceState({}, "", `/enterprises/view?id=${targetEid}`);
        load();
      })();
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eid]);

  // SPEC-CM：风鸟粘贴解析入库
  const importGsPaste = async () => {
    if (!gsPaste.trim()) return;
    setGsBusy(true);
    try {
      const r = await api.post<{ filled: Record<string, string>; skipped: string[]; phone_added: boolean }>(
        `/api/enterprises/${eid}/gongshang-paste`,
        { text: gsPaste },
      );
      const filledKeys = Object.keys(r.filled);
      setGsResult(
        filledKeys.length > 0
          ? `已补：${filledKeys.join("、")}` + (r.phone_added ? "（含电话）" : "")
          : "没有需要补的——所选字段都已有值",
      );
      setGsPaste("");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "解析失败");
    } finally {
      setGsBusy(false);
    }
  };

  // SPEC-CL：手工记一条联系人（进可拨库与推荐）
  const addContact = async () => {
    if (!cName.trim()) return;
    setCBusy(true);
    try {
      await api.post(`/api/enterprises/${eid}/contacts`, {
        name: cName.trim(), phone: cPhone.trim(), title: cTitle.trim(), role: cRole,
      });
      Toast.success("已记入联系人——号码进可拨库与推荐");
      setCName(""); setCPhone(""); setTitle("");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "记录失败");
    } finally {
      setCBusy(false);
    }
  };

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

  // SPEC-MULTI S2：工商存续状态（注销/吊销/迁出/清算 = 线索死刑，红标警示）
  const bizStatus = (detail.biz_status ?? "").trim();
  const bizDead = ["注销", "吊销", "迁出", "清算"].some((k) => bizStatus.includes(k));
  const bizStatusAt = (detail.biz_status_at ?? "").trim();
  const bizDeadFlag = (detail.tags ?? []).includes("biz_dead");
  // SPEC-MULTI S2：工商卡新增行——有值才显示；prov_json 溯源字段照 prov 徽章机制渲染
  const formerNames: string[] = (() => {
    try {
      const arr: unknown = JSON.parse((detail.former_names as string) || "[]");
      return Array.isArray(arr)
        ? arr.filter((x): x is string => typeof x === "string" && x.trim() !== "")
        : [];
    } catch {
      return [];
    }
  })();
  const gsRows: [string, string, string][] = [
    ["统一信用代码", detail.uscc || "—", "uscc"],
    ["法定代表人", detail.legal_rep || "—", "legal_rep"],
    ["成立日期", detail.established_at || "—", "established_at"],
    ["注册资本", detail.registered_capital_wan == null ? "—" : `${detail.registered_capital_wan.toLocaleString("zh-CN")} 万`, "registered_capital_wan"],
    ["行业", detail.industry_name || "未分类", ""],
    ["所在区域", [detail.province, detail.city, detail.district].filter(Boolean).join(" / ") || "—", ""],
    ["地址", detail.address || "—", ""],
    ["经营范围", detail.business_scope || "—", "business_scope"],
  ];
  if (detail.staff_size) gsRows.push(["人员规模", detail.staff_size, "staff_size"]);
  if (detail.paid_in_capital_wan != null)
    gsRows.push(["实缴资本", `${detail.paid_in_capital_wan.toLocaleString("zh-CN")} 万`, "paid_in_capital_wan"]);
  if (formerNames.length > 0) gsRows.push(["曾用名", formerNames.join("，"), "former_names"]);

  return (
    <AppShell>
      {/* 返回 + 头部档案条 */}
      <button
        onClick={() => router.back()}
        className="-ml-2 flex cursor-pointer items-center gap-1 rounded border-0 bg-transparent px-2 py-2.5 text-[13px] text-[var(--muted)] hover:bg-[var(--raised)] hover:text-[var(--accent)]"
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

      <div className="mt-3 rounded-lg border border-[var(--border)] bg-white px-4 py-4 sm:px-6 sm:py-5">
        <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
          <div className="min-w-0 w-full sm:w-auto">
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
              <Button
                size="small"
                theme="outline"
                title="打单前一页纸：他们买什么/为什么现在打/打给谁/谈资"
                onClick={() => {
                  const qs = new URLSearchParams({
                    token: getToken() ?? "", // 新开页无 localStorage API 包装——AI1 的 ?token= 回退
                  });
                  window.open(`/api/enterprises/${eid}/brief/print?${qs}`, "_blank");
                }}
              >
                作战简报
              </Button>
              <Button
                size="small"
                theme="outline"
                loading={reconBusy}
                title="自动去政采网调查该企业全部公开记录（中标/采购），新发现入库"
                onClick={async () => {
                  setReconBusy(true);
                  try {
                    const d = await api.post<{ found: number; new_signals: number; new_contacts: number; by_type: Record<string, number> }>(
                      `/api/enterprises/${eid}/recon`,
                      { days: 365, max_pages: 2 },
                    );
                    if (d.new_signals > 0) {
                      Toast.success(`侦察完成：发现 ${d.found} 条公开记录，新入库 ${d.new_signals} 条（含采购人 ${d.new_contacts} 个）——情报晨报已更新`);
                      load(); // 详情/信号时间线刷新
                    } else {
                      Toast.info(`侦察完成：政采网近一年未发现「${detail.name}」的公开记录`);
                    }
                  } catch (e) {
                    Toast.error(e instanceof Error ? e.message : "侦察失败");
                  } finally {
                    setReconBusy(false);
                  }
                }}
              >
                深度调查
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
          <div className="flex w-full shrink-0 flex-wrap items-center gap-2 sm:w-auto">
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
        {/* 分数条（移动 2×2，md+ 四列一行） */}
        <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-[var(--frame)] md:grid-cols-4">
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
              <div className="mb-2 flex flex-col gap-1.5 sm:flex-row sm:items-center sm:justify-between">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-[12.5px] font-medium text-[var(--ink)]">工商信息</span>
                  {/* SPEC-MULTI S2：工商存续状态徽章——存续/在业=青，注销/吊销/迁出/清算=红 */}
                  {bizStatus && (
                    <Tag color={bizDead ? "red" : "cyan"} type="light" size="small">{bizStatus}</Tag>
                  )}
                  {bizStatus && bizStatusAt && (
                    <span className="text-[11px] text-[var(--muted)]">核验于 {bizStatusAt.slice(0, 10)}</span>
                  )}
                  {bizDeadFlag && (
                    <span className="text-[11.5px] font-medium text-[var(--negative)]">
                      该企业工商状态异常，线索质量存疑
                    </span>
                  )}
                </span>
                <span className="flex flex-wrap items-center gap-2 text-[11.5px] text-[var(--muted)]">
                  <span>
                    资料完整度 {detail.completeness ?? 0}/4
                    {detail.completeness != null && detail.completeness < 4 && "（缺项可经采集/导入补全）"}
                  </span>
                  <Tag className="!shrink-0" color="grey" type="light" size="small">数据来源：官方公示</Tag>
                </span>
              </div>
              <div className="grid grid-cols-1 gap-x-10 gap-y-2 text-[13px] leading-7 md:grid-cols-2">
                {gsRows.map(([k, v, pk]) => {
                  const provRec = prov[pk];
                  return (
                    <div key={k} className="flex gap-2 border-b border-[var(--rule)] py-1">
                      <span className="w-24 shrink-0 text-[var(--muted)]">{k}</span>
                      <span className="min-w-0 flex-1 text-[var(--ink)]">
                        {v}
                        {/* SPEC-PROV：字段级来源标注（谁搬的/何时——消灭字段黑箱） */}
                        {provRec && (
                          <span
                            className="ml-2 whitespace-nowrap text-[11px] text-[var(--muted)]"
                            title={`来源：${provRec.source}${provRec.by ? " · 操作人：" + provRec.by : ""} · 时间：${provRec.at}`}
                          >
                            （{provRec.source} {(provRec.at || "").slice(0, 10)}）
                          </span>
                        )}
                      </span>
                    </div>
                  );
                })}
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

            {/* SPEC-CM：从风鸟粘贴工商资料——吃原始"字段名 值"格式，仅补空 */}
            {/* SPEC-EXT：企业情报直达系统——权威源深链矩阵（不爬不存，查到经
                粘贴框/批量回填入库，查→搬→存→用四步闭环） */}
            {extLinks && (
              <div className="mt-6 rounded-lg border border-[var(--border)] bg-white px-4 py-3">
                <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[12.5px] font-medium text-[var(--ink)]">外部情报直达</span>
                  <span className="text-[11.5px] text-[var(--muted)]">
                    查到工商/电话后 → 贴进下方粘贴框，或走拓客任务页批量回填
                  </span>
                </div>
                <div className="mt-2.5 space-y-2.5">
                  {extLinks.categories.map((cat) => (
                    <div key={cat.category}>
                      <div className="mb-1.5 text-[11px] font-medium tracking-wide text-[var(--muted)]">
                        {cat.category}
                      </div>
                      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                        {cat.sources.map((s) => (
                          <a
                            key={s.key}
                            href={s.url}
                            target="_blank"
                            rel="noreferrer"
                            className="group rounded-lg border border-[var(--border)] px-3 py-2 transition-colors hover:border-[var(--accent)]"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-[13px] font-medium text-[var(--ink)]">{s.label}</span>
                              <span className="text-[var(--accent)] transition-transform group-hover:translate-x-0.5">↗</span>
                            </div>
                            <div className="mt-0.5 text-[11.5px] leading-4 text-[var(--muted)]">{s.what}</div>
                          </a>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="mt-6 rounded-lg border border-[var(--border)] bg-[var(--raised)] px-4 py-3">
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <span className="text-[12.5px] font-medium text-[var(--ink)]">从风鸟补工商资料</span>
                <span className="flex items-center gap-3">
                  <button
                    onClick={() => setBmOpen(true)}
                    className="cursor-pointer border-0 bg-transparent p-0 text-[12px] text-[var(--accent)] hover:underline"
                    title="安装浏览器书签：在风鸟页点一下自动搬运回平台"
                  >
                    搬运书签
                  </button>
                  <a
                    href={`https://www.riskbird.com/ent/${encodeURIComponent(detail.name)}?eid=${eid}`}
                    className="text-[12px] text-[var(--accent)] hover:underline"
                    title="跳转到风鸟此公司页（书签搬运后自动跳回平台）"
                  >
                    一键打开风鸟搜此公司 ↗
                  </a>
                </span>
              </div>
              <div className="mb-2 text-[11.5px] leading-4 text-[var(--muted)]">
                点上方链接 → 在风鸟打开这家公司的"工商信息"表格 → 整块选中复制 →
                粘到下面 → 只补空缺字段，已有值不动
              </div>
              <textarea
                value={gsPaste}
                onChange={(e) => setGsPaste(e.target.value)}
                placeholder={"粘贴示例（风鸟页复制即得）：\n法定代表人 张三\n注册资本 1200万人民币\n成立日期 2015-03-01\n企业电话 0512-66881234"}
                rows={4}
                className="w-full rounded-lg border border-[var(--border)] bg-white px-3 py-2 font-mono text-[12px] outline-none focus:border-[var(--accent)]"
              />
              <div className="mt-2 flex items-center justify-between gap-2">
                <span className="min-w-0 flex-1 truncate text-[11.5px] text-[var(--positive)]">{gsResult}</span>
                <Button size="small" theme="solid" loading={gsBusy} disabled={!gsPaste.trim()} onClick={() => void importGsPaste()}>
                  解析入库
                </Button>
              </div>
            </div>

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
            {/* SPEC-CL：查到联系方式直接记（风鸟/企查查查询后） */}
            <div className="mb-4 rounded-lg border border-[var(--border)] bg-[var(--raised)] px-4 py-3">
              <div className="mb-2 text-[12.5px] text-[var(--muted)]">
                查到联系方式？直接记进来——号码立即进入可拨库与推荐
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Input size="small" placeholder="姓名 *" value={cName} onChange={setCName} className="!w-28" />
                <Input size="small" placeholder="电话" value={cPhone} onChange={setCPhone} className="!w-40" />
                <Input size="small" placeholder="职务" value={cTitle} onChange={setTitle} className="!w-32" />
                <Select size="small" value={cRole} onChange={(v) => setCRole(String(v))} className="!w-32">
                  <Select.Option value="buyer_contact">采购联系人</Select.Option>
                  <Select.Option value="legal_rep">法定代表人</Select.Option>
                  <Select.Option value="project_contact">项目联系人</Select.Option>
                  <Select.Option value="proxy">代理机构</Select.Option>
                </Select>
                <Button size="small" theme="solid" loading={cBusy} disabled={!cName.trim()} onClick={() => void addContact()}>
                  记一条
                </Button>
              </div>
            </div>
            {/* 拨号作战面板：可拨优先排序 / tel: 拨打 / CallResultTag 快标 / hover 编辑删除 */}
            <ContactDialPanel
              contacts={contacts}
              onRefresh={load}
              onMarkResult={(cid, result) => void markCallResult(cid, result)}
            />
          </TabPane>

          {/* SPEC-AS AS1 装机台账（CMMS 模型：装机=资产负债表，耗材时钟=定时订单） */}
          <TabPane tab="装机设备" itemKey="units" className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[12.5px] text-[var(--muted)]">
                任何品牌的真空泵都可入账（含莱宝/普发等大品牌——维修/配件/泵油是服务商机）；
                每台自带耗材时钟（密封 12-24 月/滤芯 2000-4000h），到期自动生成回访待办
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

          {/* SPEC-BT：信号时间线升级为客户旅程（八源合流） */}
          <TabPane tab="客户旅程" itemKey="signals" className="p-5">
            {journey === null ? (
              <div className="py-8 text-center text-[13px] text-[var(--muted)]">加载中…</div>
            ) : (
              <JourneyTimeline items={journey} />
            )}
          </TabPane>

          {/* SPEC-CD：找相似（look-alike 可解释榜） */}
          <TabPane tab="找相似" itemKey="similar" className="p-5">
            {similar === null ? (
              <div className="py-8 text-center text-[13px] text-[var(--muted)]">加载中…</div>
            ) : similar.length === 0 ? (
              <div className="py-8 text-center text-[13px] text-[var(--muted)]">
                暂无相似企业 —— 给本企业补行业/地区数据后，同类客户会出现在这里
              </div>
            ) : (
              <div className="space-y-2.5">
                <div className="text-[12.5px] text-[var(--muted)]">
                  从这一个好客户，找到一群同类客户（同行业 &gt; 同省 &gt; 同市 &gt; 可拨 &gt; 近期有信号；已排除在管与同行）
                </div>
                {similar.map((s) => (
                  <div key={s.id} className="flex items-start justify-between gap-3 rounded-lg border border-[var(--border)] px-4 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <a href={`/enterprises/view?id=${s.id}`} className="truncate text-[14px] font-medium text-[var(--accent)] hover:underline">
                          {s.name}
                        </a>
                        {s.reasons.map((r) => (
                          <Tag key={r} color="cyan" type="light" size="small">{r}</Tag>
                        ))}
                      </div>
                      <div className="mt-0.5 text-[12.5px] text-[var(--muted)]">
                        {[s.industry_name, s.province, s.city].filter(Boolean).join(" · ") || "—"} · 意图 {s.intent_score}
                      </div>
                    </div>
                    <Button
                      size="small"
                      theme="solid"
                      loading={simConverting === s.id}
                      onClick={async () => {
                        setSimConverting(s.id);
                        try {
                          await api.post(`/api/enterprises/${s.id}/lead`, {});
                          Toast.success("已转线索");
                        } catch (e) {
                          Toast.error(e instanceof Error ? e.message : "转线索失败");
                        } finally {
                          setSimConverting(null);
                        }
                      }}
                    >
                      转线索
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </TabPane>
        </Tabs>
      </div>
      {/* SPEC-CN 搬运书签安装 Modal */}
      <Modal
        title="安装「风鸟搬运」书签"
        visible={bmOpen}
        onCancel={() => setBmOpen(false)}
        footer={null}
        width={520}
      >
        <div className="space-y-3 text-[13px] leading-6 text-[var(--ink)]">
          <p>
            把下面的链接<b>拖到浏览器书签栏</b>（拖不动就右键收藏）。以后在
            风鸟打开任意一家公司页，点一下书签——页面上的工商字段自动搬回
            平台该公司的情报页并入库。
          </p>
          <div className="rounded-lg border border-[var(--border)] bg-[var(--raised)] px-3 py-3">
            <a
              href={makeBookmarkletSrc(window.location.origin)}
              onClick={(e) => e.preventDefault()}
              className="inline-block cursor-grab rounded-md bg-[#131416] px-3 py-1.5 text-[13px] font-medium text-white"
              title="按住拖到书签栏"
            >
              📌 风鸟搬运 → vaclead
            </a>
            <div className="mt-2 text-[11.5px] text-[var(--muted)]">
              必须从平台点「一键打开风鸟」进入（链接带公司编号），直接开风鸟没有回传目标。
            </div>
          </div>
          <ol className="ml-4 list-decimal space-y-1 text-[12.5px] text-[var(--muted)]">
            <li>平台点「一键打开风鸟搜此公司」→ 风鸟登录态打开公司页</li>
            <li>点书签栏的「风鸟搬运」→ 自动抓工商字段</li>
            <li>自动跳回平台情报页并入库，显示补了什么</li>
          </ol>
          <p className="text-[11.5px] text-[var(--muted)]">
            边界：只在风鸟公司页手动点一下才抓当前这一家——不做批量自动化
            （合规红线）。
          </p>
        </div>
      </Modal>
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
