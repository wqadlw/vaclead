"use client";

import { useEffect, useState } from "react";
import { Button, Collapse, Descriptions, SideSheet, Skeleton, Tag, Timeline, Toast } from "@douyinfe/semi-ui";
import { ExternalLink, Zap } from "lucide-react";
import { api } from "@/lib/api";
import type { Enterprise } from "@/lib/api";
import { gradeOf } from "@/lib/constants";

const GRADE_TAG: Record<string, { color: "red" | "orange" | "blue"; label: string }> = {
  S: { color: "red", label: "S 级" },
  A: { color: "orange", label: "A 级" },
  B: { color: "blue", label: "B 级" },
};

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
      width={620}
      title={
        detail ? (
          <span className="flex items-center gap-2">
            <span className="text-[16px] font-semibold text-slate-800">{detail.name}</span>
            {detail.is_competitor === 1 && <Tag color="amber">疑似同行</Tag>}
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
          {/* 工商信息 */}
          <section>
            <h3 className="text-[13px] font-semibold text-slate-400 tracking-wide mb-2.5">工商信息</h3>
            <Descriptions
              size="small"
              align="left"
              data={[
                { key: "统一信用代码", value: detail.uscc || "（暂缺）" },
                { key: "地区", value: [detail.province, detail.city, detail.district].filter(Boolean).join(" ") || "-" },
                { key: "行业", value: detail.industry_name || "未分类" },
                { key: "注册资本", value: detail.registered_capital_wan == null ? "-" : `${detail.registered_capital_wan.toLocaleString("zh-CN")} 万` },
                { key: "经营状态", value: detail.status === "active" ? "在营" : "已注销" },
                { key: "来源", value: ({ poi: "高德 POI", tender: "招标公告", csv: "CSV 导入", manual: "手动录入" } as Record<string, string>)[detail.source as string] ?? detail.source },
              ]}
            />
            {detail.address && <p className="text-[12.5px] text-slate-500 mt-2.5">{detail.address}</p>}
          </section>

          {/* 评分 */}
          <section className="rounded-2xl bg-gradient-to-br from-slate-50 to-indigo-50/60 border border-indigo-100/70 p-5">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-[11.5px] text-slate-400">ICP 匹配分</div>
                <div className="text-[34px] leading-10 font-bold text-indigo-700 tabular-nums">{detail.icp_score}</div>
              </div>
              <div className="text-right">
                <div className="text-[11.5px] text-slate-400">意图分（时间衰减）</div>
                <div className={`text-[34px] leading-10 font-bold tabular-nums ${detail.intent_score > 0 ? "text-rose-500" : "text-slate-300"}`}>
                  {detail.intent_score}
                </div>
              </div>
              <div className="text-right">
                <div className="text-[11.5px] text-slate-400">线索等级</div>
                <div className="mt-1.5">
                  {GRADE_TAG[grade] ? (
                    <Tag color={GRADE_TAG[grade].color} type="solid" size="large">
                      {GRADE_TAG[grade].label}
                    </Tag>
                  ) : (
                    <span className="text-slate-300 text-[15px]">-</span>
                  )}
                  <div className="text-[10.5px] text-slate-400 mt-1">综合 {composite.toFixed(1)}（ICP×0.4＋意图×0.6）</div>
                </div>
              </div>
            </div>
            {detail.icp_reasons.length > 0 && (
              <Collapse className="!mt-4 !bg-white/70 !rounded-xl" defaultActiveKey={["reasons"]}>
                <Collapse.Panel header={`评分理由（${detail.icp_reasons.length} 条）`} itemKey="reasons">
                  <div className="space-y-1.5">
                    {detail.icp_reasons.map((r, i) => (
                      <div key={i} className="flex items-center justify-between text-[13px]">
                        <span className="text-slate-600">{r.rule}</span>
                        <span className="font-semibold text-indigo-600 tabular-nums">+{r.points}</span>
                      </div>
                    ))}
                  </div>
                </Collapse.Panel>
              </Collapse>
            )}
          </section>

          {/* 经营范围 */}
          {detail.business_scope && (
            <section>
              <h3 className="text-[13px] font-semibold text-slate-400 tracking-wide mb-2">经营范围</h3>
              <p className="text-[13px] text-slate-600 leading-6 whitespace-pre-wrap bg-slate-50 rounded-xl p-3.5">
                {detail.business_scope}
              </p>
            </section>
          )}

          {/* 信号 */}
          <section>
            <h3 className="text-[13px] font-semibold text-slate-400 tracking-wide mb-3">
              意图信号（{detail.signals?.length ?? 0} 条）
            </h3>
            {(detail.signals?.length ?? 0) === 0 ? (
              <div className="text-[13px] text-slate-400 bg-slate-50 rounded-xl p-4">
                暂无信号 —— 该企业尚未命中设备采购相关公告
              </div>
            ) : (
              <Timeline mode="left">
                {detail.signals!.map((s) => (
                  <Timeline.Item
                    key={s.id}
                    time={s.pub_date || "日期未知"}
                    dot={<span className="w-2 h-2 rounded-full bg-rose-500 inline-block shadow-[0_0_0_3px_#fecdd3]" />}
                  >
                    <div className="text-[13.5px] font-medium text-slate-700 leading-5">
                      {s.url ? (
                        <a href={s.url} target="_blank" rel="noreferrer" className="hover:text-indigo-600 inline-flex items-center gap-1">
                          {s.title} <ExternalLink size={12} className="text-slate-300" />
                        </a>
                      ) : (
                        s.title
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-1 mt-1.5">
                      <Tag color="red" type="light" size="small">
                        <Zap size={10} className="inline mr-0.5 -mt-0.5" />
                        {s.base_score} 分
                      </Tag>
                      {s.matched_terms.map((t) => (
                        <Tag key={t} color="orange" type="light" size="small">
                          {t}
                        </Tag>
                      ))}
                      <Tag color="grey" type="light" size="small">
                        {s.hit_part === "title" ? "标题命中" : "正文命中"}
                      </Tag>
                    </div>
                    {s.raw_excerpt && (
                      <p className="text-[12px] text-slate-400 leading-5 mt-1.5 bg-slate-50 rounded-lg px-3 py-2">{s.raw_excerpt}</p>
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
