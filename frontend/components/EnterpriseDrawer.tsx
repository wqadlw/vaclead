"use client";

import { useEffect, useState } from "react";
import { Button, Collapse, Descriptions, SideSheet, Skeleton, Tag, Timeline, Toast } from "@douyinfe/semi-ui";
import { ExternalLink } from "lucide-react";
import { api } from "@/lib/api";
import type { Enterprise } from "@/lib/api";
import { gradeOf } from "@/lib/constants";

interface Props {
  open: boolean;
  enterpriseId: number | null;
  onClose: () => void;
  onConverted?: () => void;
  onOpen?: (id: number) => void;
}

export default function EnterpriseDrawer({ open, enterpriseId, onClose, onConverted, onOpen }: Props) {
  const [detail, setDetail] = useState<Enterprise | null>(null);
  const [loading, setLoading] = useState(false);
  const [converting, setConverting] = useState(false);

  useEffect(() => {
    if (enterpriseId === null) return;
    let cancelled = false;
    setLoading(true);
    setDetail(null);
    api
      .get<Enterprise>(`/api/enterprises/${enterpriseId}`)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e) => Toast.error(e instanceof Error ? e.message : "加载失败"))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enterpriseId]);

  const convert = async () => {
    if (detail === null) return;
    setConverting(true);
    try {
      await api.post(`/api/enterprises/${detail.id}/lead`);
      Toast.success("已转为线索，去线索看板跟进吧");
      onConverted?.();
      onClose();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setConverting(false);
    }
  };

  const [subscribed, setSubscribed] = useState(false);
  const [similar, setSimilar] = useState<{ id: number; name: string; intent_score: number }[]>([]);
  const [graph, setGraph] = useState<{ edges: { name: string; role: string; amount: number; count: number }[] } | null>(null);
  const [related, setRelated] = useState<{ competitors: { id: number; name: string; via: string }[]; peers: { id: number; name: string; via: string }[] } | null>(null);
  const [contacts, setContacts] = useState<{ id: number; name: string; role: string; title?: string; evidence_claim?: string }[]>([]);

  useEffect(() => {
    if (!open || !detail) return;
    api.get<{ items: { id: number; name: string; intent_score: number }[] }>(
      `/api/enterprises/${detail.id}/similar`,
    ).then((d) => setSimilar(d.items)).catch(() => undefined);
    api.get<{ edges: { name: string; role: string; amount: number; count: number }[] }>(
      `/api/enterprises/${detail.id}/graph`,
    ).then(setGraph).catch(() => undefined);
    api.get<{ competitors: { id: number; name: string; via: string }[]; peers: { id: number; name: string; via: string }[] }>(
      `/api/enterprises/${detail.id}/related`,
    ).then(setRelated).catch(() => undefined);
    api.get<{ items: { id: number; name: string; role: string; title?: string; evidence_claim?: string }[] }>(
      `/api/enterprises/${detail.id}/contacts`,
    ).then((d) => setContacts(d.items)).catch(() => undefined);
  }, [open, detail]);

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
        Toast.success("已关注——新中标/新公告将推送到顶栏铃铛");
      }
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const composite = detail ? detail.icp_score * 0.4 + detail.intent_score * 0.6 : 0;
  const grade = gradeOf(composite);

  return (
    <SideSheet
      visible={open}
      onCancel={onClose}
      width={600}
      title={
        detail ? (
          <span className="flex items-center gap-2">
            <span className="text-[16px] font-semibold text-[var(--ink)]">{detail.name}</span>
            {detail.is_competitor === 1 && <Tag color="amber" type="light">疑似同行</Tag>}
          </span>
        ) : (
          "企业详情"
        )
      }
      footer={
        detail && detail.is_competitor !== 1 ? (
          <div className="flex justify-end gap-2">
            <Button theme={subscribed ? "outline" : "light"} onClick={toggleSubscribe}>
              {subscribed ? "已关注" : "关注此企业"}
            </Button>
            <Button onClick={onClose}>关闭</Button>
            <Button theme="solid" loading={converting} onClick={convert}>
              转为线索
            </Button>
          </div>
        ) : null
      }
      bodyStyle={{ padding: "20px 24px" }}
    >
      {loading || detail === null ? (
        <div className="space-y-4 pt-2">
          <Skeleton placeholder={<Skeleton.Paragraph rows={5} />} loading active />
          <Skeleton placeholder={<Skeleton.Paragraph rows={3} />} loading active />
        </div>
      ) : (
        <div className="space-y-6">
          <section>
            <h3 className="mb-2.5 text-[12.5px] font-normal text-[var(--muted)]">工商信息</h3>
            <Descriptions
              size="small"
              align="left"
              data={[
                { key: "统一信用代码", value: detail.uscc || "—" },
                { key: "地区", value: [detail.province, detail.city, detail.district].filter(Boolean).join(" ") || "—" },
                { key: "行业", value: detail.industry_name || "未分类" },
                {
                  key: "注册资本",
                  value: detail.registered_capital_wan == null ? "—" : `${detail.registered_capital_wan.toLocaleString("zh-CN")} 万`,
                },
                { key: "经营状态", value: detail.status === "active" ? "在营" : "已注销" },
                {
                  key: "来源",
                  value:
                    ({ poi: "高德 POI", tender: "招标公告", csv: "CSV 导入", manual: "手动录入" } as Record<string, string>)[
                      detail.source as string
                    ] ?? detail.source,
                },
              ]}
            />
            {detail.address && <p className="mt-2.5 text-[12.5px] text-[var(--muted)]">{detail.address}</p>}
          </section>

          {/* 采购关系图谱（SPEC-D，独有数据） */}
          {graph && graph.edges.length > 0 && (
            <section>
              <h3 className="mb-2.5 text-[12.5px] font-normal text-[var(--muted)]">采购关系图（官方成交数据反抽）</h3>
              <svg viewBox="0 0 560 200" className="w-full rounded-lg border border-[var(--border)] bg-white">
                <line x1="280" y1="100" x2="80" y2="40" stroke="var(--frame)" strokeWidth="1.5" />
                <line x1="280" y1="100" x2="80" y2="160" stroke="var(--frame)" strokeWidth="1.5" />
                <line x1="280" y1="100" x2="480" y2="100" stroke="var(--frame)" strokeWidth="1.5" />
                <circle cx="280" cy="100" r="34" fill="var(--accent)" />
                <text x="280" y="104" textAnchor="middle" fill="#fff" fontSize="10">本企业</text>
                {graph.edges.slice(0, 3).map((e, i) => {
                  const pos = [{x: 80, y: 40}, {x: 480, y: 100}, {x: 80, y: 160}][i];
                  const label = e.role === "buyer" ? `采购 ${Math.round(e.amount)}万` : `供应 ${Math.round(e.amount)}万`;
                  return (
                    <g key={e.name}>
                      <circle cx={pos.x} cy={pos.y} r="26" fill="#fff" stroke="var(--accent)" />
                      <text x={pos.x} y={pos.y + 4} textAnchor="middle" fill="var(--ink)" fontSize="9">
                        {e.name.slice(0, 8)}
                      </text>
                      <text x={(280 + pos.x) / 2} y={(100 + pos.y) / 2 - 6} textAnchor="middle" fill="var(--muted)" fontSize="9">{label}</text>
                    </g>
                  );
                })}
              </svg>
              <div className="mt-1 text-[11px] text-[var(--muted)]">
                共 {graph.edges.length} 条关系边 · {graph.edges.filter((e) => e.role === "buyer").length} 家采购人 · {graph.edges.filter((e) => e.role === "supplier").length} 家供应商
              </div>
            </section>
          )}

          {/* 关键联系人（SPEC-D） */}
          {contacts.length > 0 && (
            <section>
              <h3 className="mb-2.5 text-[12.5px] font-normal text-[var(--muted)]">关键联系人（全部带溯源）</h3>
              <div className="space-y-1.5">
                {contacts.map((c) => (
                  <div key={c.id} className="rounded-lg border border-[var(--border)] bg-white px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium text-[var(--ink)]">{c.name}</span>
                      <Tag color="cyan" type="light" size="small">{c.title || c.role}</Tag>
                    </div>
                    {c.evidence_claim && (
                      <div className="mt-0.5 text-[11.5px] leading-4 text-[var(--muted)]">{c.evidence_claim}</div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* 关联企业（SPEC-D） */}
          {related && (related.competitors.length > 0 || related.peers.length > 0) && (
            <section>
              <h3 className="mb-2.5 text-[12.5px] font-normal text-[var(--muted)]">关联企业（共享采购关系的情报网）</h3>
              {related.competitors.length > 0 && (
                <div className="mb-2">
                  <div className="mb-1 text-[11.5px] text-[var(--negative)]">共享同一采购人的其他供应商（竞争情报）</div>
                  {related.competitors.map((c) => (
                    <button key={c.id} onClick={() => onOpen?.(c.id)} className="mr-1.5 mb-1 cursor-pointer rounded border border-[var(--border)] bg-white px-2 py-1 text-[11.5px] text-[var(--ink)] hover:border-[var(--accent)]">
                      {c.name} <span className="text-[var(--muted)]">via {c.via}</span>
                    </button>
                  ))}
                </div>
              )}
              {related.peers.length > 0 && (
                <div>
                  <div className="mb-1 text-[11.5px] text-[var(--muted)]">共享同一供应商的其他采购人</div>
                  {related.peers.map((c) => (
                    <button key={c.id} onClick={() => onOpen?.(c.id)} className="mr-1.5 mb-1 cursor-pointer rounded border border-[var(--border)] bg-white px-2 py-1 text-[11.5px] text-[var(--ink)] hover:border-[var(--accent)]">
                      {c.name}
                    </button>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* 相似企业推荐（SPEC-C F3） */}
          {similar.length > 0 && (
            <section>
              <h3 className="mb-2.5 text-[12.5px] font-normal text-[var(--muted)]">相似企业</h3>
              <div className="flex flex-wrap gap-2">
                {similar.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => onOpen?.(s.id)}
                    className="cursor-pointer rounded-lg border border-[var(--border)] bg-white px-3 py-1.5 text-left hover:border-[var(--accent)]"
                  >
                    <div className="max-w-[180px] truncate text-[12.5px] font-medium text-[var(--ink)]" title={s.name}>{s.name}</div>
                    <div className="text-[11px] text-[var(--muted)]">意图 {s.intent_score}</div>
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* 中标画像（官方成交数据聚合，全部可溯源） */}
          {detail.stat && (
            <section>
              <h3 className="mb-2.5 text-[12.5px] font-normal text-[var(--muted)]">中标画像</h3>
              <div className="grid grid-cols-4 gap-px overflow-hidden rounded-lg bg-[var(--frame)]">
                {[
                  { k: "中标次数", v: String(detail.stat.bid_count) },
                  {
                    k: "累计金额",
                    v:
                      detail.stat.bid_amount_total_wan > 0
                        ? `${Math.round(detail.stat.bid_amount_total_wan).toLocaleString("zh-CN")} 万`
                        : "—",
                  },
                  { k: "最近中标", v: detail.stat.last_win_date || "—" },
                  { k: "头部采购人", v: detail.stat.top_buyer || "—" },
                ].map((cell) => (
                  <div key={cell.k} className="bg-white px-3 py-2.5">
                    <div className="text-[11.5px] text-[var(--muted)]">{cell.k}</div>
                    <div
                      className="mt-0.5 truncate text-[14.5px] font-medium tabular-nums text-[var(--ink)]"
                      title={cell.v}
                    >
                      {cell.v}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* 评分：浅灰槽，青蓝给主数据 */}
          <section className="rounded-lg bg-[var(--raised)] p-5">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-[12.5px] text-[var(--muted)]">ICP 匹配分</div>
                <div className="mt-0.5 text-[30px] font-normal leading-9 tabular-nums text-[var(--accent)]">
                  {detail.icp_score}
                </div>
              </div>
              <div className="text-right">
                <div className="text-[12.5px] text-[var(--muted)]">意图分（30 天半衰）</div>
                <div
                  className={`mt-0.5 text-[30px] font-normal leading-9 tabular-nums ${
                    detail.intent_score > 0 ? "text-[var(--negative)]" : "text-[#b6b9bb]"
                  }`}
                >
                  {detail.intent_score}
                </div>
              </div>
              <div className="text-right">
                <div className="text-[12.5px] text-[var(--muted)]">线索等级</div>
                <div className="mt-1 text-[20px] font-medium leading-7 text-[var(--ink)]">
                  {grade !== "-" ? grade : "—"}
                </div>
                <div className="text-[11px] text-[#8a8d8f]">综合 {composite.toFixed(1)}</div>
              </div>
            </div>
            {detail.icp_reasons.length > 0 && (
              <Collapse className="!mt-4 !bg-white" defaultActiveKey={["reasons"]}>
                <Collapse.Panel header={`评分理由（${detail.icp_reasons.length} 条）`} itemKey="reasons">
                  <div className="space-y-1.5">
                    {detail.icp_reasons.map((r, i) => (
                      <div key={i} className="flex items-center justify-between text-[13px]">
                        <span className="text-[var(--muted)]">{r.rule}</span>
                        <span className="tabular-nums font-medium text-[var(--ink)]">+{r.points}</span>
                      </div>
                    ))}
                  </div>
                </Collapse.Panel>
              </Collapse>
            )}
          </section>

          {detail.business_scope && (
            <section>
              <h3 className="mb-2 text-[12.5px] font-normal text-[var(--muted)]">经营范围</h3>
              <p className="whitespace-pre-wrap rounded-lg bg-[var(--raised)] p-3.5 text-[13px] leading-6 text-[var(--ink)]">
                {detail.business_scope}
              </p>
            </section>
          )}

          <section>
            <h3 className="mb-3 text-[12.5px] font-normal text-[var(--muted)]">
              意图信号（{detail.signals?.length ?? 0}）
            </h3>
            {(detail.signals?.length ?? 0) === 0 ? (
              <div className="rounded-lg bg-[var(--raised)] p-4 text-[13px] text-[var(--muted)]">
                暂无命中 —— 该企业尚未出现在设备采购相关公告中
              </div>
            ) : (
              <Timeline mode="left">
                {detail.signals!.map((s) => (
                  <Timeline.Item
                    key={s.id}
                    time={s.pub_date || "日期未知"}
                    dot={<span className="inline-block h-2 w-2 rounded-full bg-[var(--negative)]" />}
                  >
                    <div className="text-[13.5px] font-medium leading-5 text-[var(--ink)]">
                      {s.url ? (
                        <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-[var(--accent)]">
                          {s.title} <ExternalLink size={12} className="text-[#9ca0a3]" />
                        </a>
                      ) : (
                        s.title
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      {s.type === "tender_win" && <Tag color="cyan" type="light" size="small">中标</Tag>}
                      {s.type === "tender_buy" && <Tag color="green" type="light" size="small">公开采购</Tag>}
                      {s.type === "tender" && <Tag color="blue" type="light" size="small">政采公告</Tag>}
                      <Tag color="red" type="light" size="small">{s.base_score} 分</Tag>
                      {s.matched_terms.map((t) => (
                        <Tag key={t} color="orange" type="light" size="small">{t}</Tag>
                      ))}
                      <Tag color="grey" type="light" size="small">{s.hit_part === "title" ? "标题命中" : "正文命中"}</Tag>
                    </div>
                    {s.evidence_claim && (
                      <p className="mt-1.5 border-l-2 border-[var(--accent)] pl-2.5 text-[12.5px] leading-5 text-[var(--ink)]">
                        {s.evidence_claim}
                      </p>
                    )}
                    {s.raw_excerpt && !s.evidence_claim && (
                      <p className="mt-1.5 rounded-md bg-[var(--raised)] px-3 py-2 text-[12px] leading-5 text-[var(--muted)]">
                        {s.raw_excerpt}
                      </p>
                    )}
                  </Timeline.Item>
                ))}
              </Timeline>
            )}
          </section>
        </div>
      )}
    </SideSheet>
  );
}
