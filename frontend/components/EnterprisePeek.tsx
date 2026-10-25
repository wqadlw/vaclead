"use client";

/**
 * 企业快速预览侧板（吸收 twenty side-panel 招牌交互）：
 * 列表点击 → 右侧滑出关键信息（不丢列表上下文）→ 需要深度再「展开完整情报页」。
 * 数据全部现成端点：detail(stat+signals) / contacts。
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, SideSheet, Skeleton, Tag, Toast } from "@douyinfe/semi-ui";
import { Bell, ExternalLink, UserCheck } from "lucide-react";
import { api } from "@/lib/api";
import type { Enterprise } from "@/lib/api";

interface ContactRow {
  id: number;
  name: string;
  role: string;
  title: string | null;
  evidence_claim: string | null;
}

const ROLE_CN: Record<string, string> = {
  legal_rep: "法定代表人",
  采购联系人: "采购联系人",
  项目联系人: "项目联系人",
  代理机构: "代理机构",
  buyer_unit: "采购单位",
  tender_agency: "代理机构",
};

const SOURCE_TAG: Record<string, { label: string; color: "cyan" | "green" | "blue" | "grey" }> = {
  ggzy: { label: "官方中标", color: "cyan" },
  ggzy_buyer: { label: "官方买家", color: "green" },
  ccgp: { label: "政采供应商", color: "blue" },
  ccgp_buyer: { label: "政采买家", color: "green" },
  csv: { label: "CSV", color: "grey" },
};

export default function EnterprisePeek({
  open,
  enterpriseId,
  onClose,
  onOpenFull,
}: {
  open: boolean;
  enterpriseId: number | null;
  onClose: () => void;
  onOpenFull: (id: number) => void;
}) {
  const router = useRouter();
  const [detail, setDetail] = useState<Enterprise | null>(null);
  const [contacts, setContacts] = useState<ContactRow[]>([]);
  const [subscribed, setSubscribed] = useState(false);
  const [converting, setConverting] = useState(false);

  useEffect(() => {
    if (!open || !enterpriseId) return;
    setDetail(null);
    api.get<Enterprise>(`/api/enterprises/${enterpriseId}`).then(setDetail).catch(() => undefined);
    api
      .get<{ items: ContactRow[] }>(`/api/enterprises/${enterpriseId}/contacts`)
      .then((d) => setContacts(d.items))
      .catch(() => undefined);
  }, [open, enterpriseId]);

  const toggleSubscribe = async () => {
    if (!detail) return;
    try {
      if (subscribed) {
        await api.delete(`/api/enterprises/${detail.id}/subscribe`);
        setSubscribed(false);
      } else {
        await api.post(`/api/enterprises/${detail.id}/subscribe`);
        setSubscribed(true);
        Toast.success("已关注——新动态推送顶栏铃铛");
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
      Toast.success("已转为线索（商机管线）");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setConverting(false);
    }
  };

  const stat = detail?.stat;
  const sourceMeta = detail ? SOURCE_TAG[detail.source ?? ""] ?? { label: detail.source || "—", color: "grey" as const } : null;

  return (
    <SideSheet
      title={
        detail ? (
          <span className="flex items-center gap-2">
            <span className="max-w-[260px] truncate text-[15px] font-semibold text-[var(--ink)]">{detail.name}</span>
            <Tag color={sourceMeta!.color} type="light" size="small">{sourceMeta!.label}</Tag>
            {detail.is_competitor === 1 && <Tag color="amber" type="light" size="small">疑似同行</Tag>}
          </span>
        ) : (
          "企业预览"
        )
      }
      visible={open}
      onCancel={onClose}
      width={420}
      footer={
        detail ? (
          <div className="flex justify-between gap-2">
            <Button theme="light" icon={<Bell size={13} />} onClick={toggleSubscribe}>
              {subscribed ? "已关注" : "关注"}
            </Button>
            <div className="flex gap-2">
              {detail.is_competitor !== 1 && (
                <Button theme="solid" icon={<UserCheck size={13} />} loading={converting} onClick={convert}>
                  转线索
                </Button>
              )}
              <Button
                theme="outline"
                icon={<ExternalLink size={13} />}
                onClick={() => onOpenFull(detail.id)}
              >
                完整情报页
              </Button>
            </div>
          </div>
        ) : null
      }
    >
      {!detail ? (
        <Skeleton placeholder={<Skeleton.Paragraph rows={6} />} loading active />
      ) : (
        <div className="space-y-5">
          {/* 分数 + 画像 */}
          <div className="grid grid-cols-4 gap-px overflow-hidden rounded-lg bg-[var(--frame)]">
            {[
              { k: "ICP", v: String(detail.icp_score) },
              { k: "意图", v: String(detail.intent_score) },
              { k: "中标", v: stat ? String(stat.bid_count) : "—" },
              { k: "金额", v: stat && stat.bid_amount_total_wan > 0 ? `${Math.round(stat.bid_amount_total_wan)}万` : "—" },
            ].map((c) => (
              <div key={c.k} className="bg-white px-2.5 py-2.5">
                <div className="text-[11px] text-[var(--muted)]">{c.k}</div>
                <div className="mt-0.5 text-[16px] font-medium tabular-nums text-[var(--ink)]">{c.v}</div>
              </div>
            ))}
          </div>

          {/* 关键档案 */}
          <div className="space-y-1 text-[12.5px] leading-6">
            <div className="flex gap-2">
              <span className="w-16 shrink-0 text-[var(--muted)]">地区</span>
              <span className="text-[var(--ink)]">
                {[detail.province, detail.city, detail.district].filter(Boolean).join(" ") || "待回填"} · {detail.address || "地址待回填"}
              </span>
            </div>
            <div className="flex gap-2">
              <span className="w-16 shrink-0 text-[var(--muted)]">行业</span>
              <span className="text-[var(--ink)]">{detail.industry_name || "未分类"}</span>
            </div>
            {detail.uscc && (
              <div className="flex gap-2">
                <span className="w-16 shrink-0 text-[var(--muted)]">信用代码</span>
                <span className="tabular-nums text-[var(--ink)]">{detail.uscc}</span>
              </div>
            )}
          </div>

          {/* 联系人（前3条带电话） */}
          {contacts.length > 0 && (
            <div>
              <div className="mb-1.5 text-[12.5px] font-medium text-[var(--ink)]">关键联系人（{contacts.length}）</div>
              <div className="space-y-1.5">
                {contacts.slice(0, 3).map((c) => (
                  <div key={c.id} className="rounded border border-[var(--border)] px-2.5 py-1.5">
                    <div className="flex items-center justify-between">
                      <span className="text-[13px] font-medium text-[var(--ink)]">{c.name}</span>
                      <Tag color="cyan" type="light" size="small">{ROLE_CN[c.role] ?? c.role}</Tag>
                    </div>
                    {c.title && <div className="text-[12.5px] tabular-nums text-[var(--accent)]">{c.title}</div>}
                  </div>
                ))}
                {contacts.length > 3 && (
                  <button
                    onClick={() => onOpenFull(detail.id)}
                    className="cursor-pointer border-0 bg-transparent p-0 text-[12px] text-[var(--accent)] hover:underline"
                  >
                    查看全部 {contacts.length} 位联系人 →
                  </button>
                )}
              </div>
            </div>
          )}

          {/* 最近信号（前3条） */}
          {(detail.signals ?? []).length > 0 && (
            <div>
              <div className="mb-1.5 text-[12.5px] font-medium text-[var(--ink)]">最近动态</div>
              <div className="space-y-1.5">
                {detail.signals!.slice(0, 3).map((s) => (
                  <div key={s.id} className="rounded border border-[var(--border)] px-2.5 py-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-[var(--ink)]" title={s.title}>{s.title}</span>
                      <span className="shrink-0 text-[11px] tabular-nums text-[var(--muted)]">{s.pub_date || ""}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={() => onOpenFull(detail.id)}
            className="flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-[var(--border)] bg-white py-2 text-[13px] text-[var(--ink)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
          >
            展开完整情报页（图谱/联系人/信号全量） <ExternalLink size={13} />
          </button>
        </div>
      )}
    </SideSheet>
  );
}
