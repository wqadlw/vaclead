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
}

export default function EnterpriseDrawer({ open, enterpriseId, onClose, onConverted }: Props) {
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
