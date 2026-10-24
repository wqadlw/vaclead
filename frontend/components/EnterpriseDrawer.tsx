"use client";

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Drawer,
  Empty,
  message,
  Space,
  Spin,
  Tag,
  Timeline,
  Typography,
} from "antd";
import { api, ApiError } from "@/lib/api";
import type { Enterprise, SignalItem } from "@/lib/api";
import { GRADE_COLOR, gradeOf } from "@/lib/constants";

/** GET /api/enterprises/{id} 返回企业全字段 + signals[]；比列表接口多 uscc/status/source 等字段。 */
interface EnterpriseDetail extends Enterprise {
  uscc?: string | null;
  status?: string | null;
  source?: string | null;
}

interface EnterpriseDrawerProps {
  open: boolean;
  enterpriseId: number | null;
  onClose: () => void;
  /** 转线索成功后回调（父级用于刷新列表）。 */
  onConverted: () => void;
}

const SOURCE_LABELS: Record<string, string> = {
  poi: "高德 POI",
  tender: "招标公告",
  csv: "CSV 导入",
  manual: "手工录入",
};

const STATUS_LABELS: Record<string, string> = {
  active: "在营",
  closed: "已注销",
};

const SIGNAL_TYPE_LABELS: Record<string, string> = {
  tender: "招标公告",
  eia: "环评公示",
  registry_change: "工商变更",
};

function fmtScore(v: number): string {
  return String(Math.round(v));
}

function regionOf(e: EnterpriseDetail): string {
  return [e.province, e.city, e.district]
    .map((x) => (x ?? "").trim())
    .filter((x) => x.length > 0)
    .join(" ");
}

function capitalOf(v: number | null): string {
  return v == null ? "-" : `${v.toLocaleString("zh-CN")} 万`;
}

function SignalBlock({ s }: { s: SignalItem }) {
  return (
    <div className="flex flex-col gap-1 pb-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-gray-500">{s.pub_date ?? "未知日期"}</span>
        <Tag color="geekblue" className="!m-0">
          {SIGNAL_TYPE_LABELS[s.type] ?? s.type}
        </Tag>
      </div>
      {s.url ? (
        <a href={s.url} target="_blank" rel="noreferrer" className="text-sm font-medium break-all">
          {s.title}
        </a>
      ) : (
        <span className="text-sm font-medium">{s.title}</span>
      )}
      {(s.matched_terms ?? []).length > 0 ? (
        <Space size={4} wrap>
          {(s.matched_terms ?? []).map((t, i) => (
            <Tag key={`${i}-${t}`} color="orange" className="!m-0">
              {t}
            </Tag>
          ))}
        </Space>
      ) : null}
      {s.raw_excerpt ? (
        <div className="rounded border border-gray-100 bg-gray-50 p-2 text-xs leading-5 text-gray-600">
          {s.raw_excerpt}
        </div>
      ) : null}
    </div>
  );
}

function DetailBody({ detail }: { detail: EnterpriseDetail }) {
  const signals: SignalItem[] = detail.signals ?? [];
  const reasons = detail.icp_reasons ?? [];
  // lead_qualify Agent 契约：综合分 = ICP*0.4 + intent*0.6，S≥80 / A≥60 / B≥40
  const composite = detail.icp_score * 0.4 + detail.intent_score * 0.6;
  const grade = gradeOf(composite);

  return (
    <div className="flex flex-col gap-6">
      <Descriptions title="工商信息" bordered size="small" column={2} labelStyle={{ width: 110 }}>
        <Descriptions.Item label="名称" span={2}>
          <span className="font-medium">{detail.name}</span>
        </Descriptions.Item>
        <Descriptions.Item label="统一信用代码">{detail.uscc?.trim() || "（暂缺）"}</Descriptions.Item>
        <Descriptions.Item label="地区">{regionOf(detail) || "-"}</Descriptions.Item>
        <Descriptions.Item label="行业">{detail.industry_name || "-"}</Descriptions.Item>
        <Descriptions.Item label="注册资本">{capitalOf(detail.registered_capital_wan)}</Descriptions.Item>
        <Descriptions.Item label="经营状态">
          {detail.status ? STATUS_LABELS[detail.status] ?? detail.status : "-"}
        </Descriptions.Item>
        <Descriptions.Item label="来源">
          {detail.source ? SOURCE_LABELS[detail.source] ?? detail.source : "-"}
        </Descriptions.Item>
      </Descriptions>

      <div className="rounded border border-gray-200 p-4">
        <div className="mb-3 text-sm font-medium text-gray-700">评分</div>
        <div className="flex flex-wrap items-start gap-8">
          <div>
            <div className="text-xs text-gray-500">ICP 匹配分</div>
            <div className="text-4xl font-bold leading-tight text-gray-800">
              {fmtScore(detail.icp_score)}
            </div>
          </div>
          <div className="min-w-[240px] flex-1">
            <Collapse
              size="small"
              items={[
                {
                  key: "icp_reasons",
                  label: `评分理由（${reasons.length} 条）`,
                  children:
                    reasons.length > 0 ? (
                      <ul className="m-0 flex list-none flex-col gap-1 p-0">
                        {reasons.map((r, i) => (
                          <li key={i} className="flex items-center justify-between gap-2 text-sm">
                            <span>{r.rule}</span>
                            <Tag color={r.points >= 0 ? "green" : "red"} className="!m-0">
                              {r.points >= 0 ? `+${r.points}` : r.points}
                            </Tag>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span className="text-sm text-gray-400">暂无评分理由</span>
                    ),
                },
              ]}
            />
          </div>
          <div>
            <div className="text-xs text-gray-500">意图分（时间衰减）</div>
            <div className="flex items-baseline gap-2">
              <div className="text-4xl font-bold leading-tight text-gray-800">
                {fmtScore(detail.intent_score)}
              </div>
              <Tag color={GRADE_COLOR[grade] ?? "default"} className="!m-0">
                {grade} 级
              </Tag>
            </div>
            <div className="mt-1 text-xs text-gray-400">
              综合分 {fmtScore(composite)}（ICP×0.4 + 意图×0.6）
            </div>
          </div>
        </div>
        {detail.is_competitor === 1 ? (
          <div className="mt-3">
            <Tag color="warning">疑似同行</Tag>
            <span className="text-xs text-gray-400">名称命中设备词且经营范围含制造/销售，谨慎跟进</span>
          </div>
        ) : null}
      </div>

      <div>
        <div className="mb-2 text-sm font-medium text-gray-700">经营范围</div>
        <Typography.Paragraph type="secondary" className="!mb-0 whitespace-pre-wrap">
          {detail.business_scope?.trim() || "（暂缺）"}
        </Typography.Paragraph>
      </div>

      <div>
        <div className="mb-3 text-sm font-medium text-gray-700">意图信号（{signals.length} 条）</div>
        {signals.length > 0 ? (
          <Timeline
            items={signals.map((s) => ({
              color: "blue",
              children: <SignalBlock s={s} />,
            }))}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无信号" />
        )}
      </div>
    </div>
  );
}

export default function EnterpriseDrawer({
  open,
  enterpriseId,
  onClose,
  onConverted,
}: EnterpriseDrawerProps) {
  const [detail, setDetail] = useState<EnterpriseDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [converting, setConverting] = useState(false);

  useEffect(() => {
    if (!open || enterpriseId == null) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDetail(null);
    api
      .get<EnterpriseDetail>(`/api/enterprises/${enterpriseId}`)
      .then((res) => {
        if (!cancelled) setDetail(res);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "未知错误");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, enterpriseId]);

  const convertToLead = async () => {
    if (enterpriseId == null || detail == null) return;
    setConverting(true);
    try {
      await api.post(`/api/enterprises/${enterpriseId}/lead`, {});
      message.success(`已转为线索：${detail.name}`);
      onConverted();
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        message.error(`转线索失败（撞单）：${e.message}`);
      } else {
        message.error(`转线索失败：${e instanceof Error ? e.message : "未知错误"}`);
      }
    } finally {
      setConverting(false);
    }
  };

  return (
    <Drawer
      open={open}
      width={640}
      onClose={onClose}
      title={
        detail ? (
          <Space>
            <span>{detail.name}</span>
            {detail.is_competitor === 1 ? <Tag color="warning">疑似同行</Tag> : null}
          </Space>
        ) : (
          "企业详情"
        )
      }
      footer={
        detail ? (
          <Space className="flex w-full justify-end">
            <Button onClick={onClose}>关闭</Button>
            <Button type="primary" loading={converting} onClick={() => void convertToLead()}>
              转线索
            </Button>
          </Space>
        ) : null
      }
    >
      {loading ? (
        <div className="flex justify-center py-16">
          <Spin />
        </div>
      ) : error ? (
        <Alert type="error" showIcon message="企业详情加载失败" description={error} />
      ) : detail ? (
        <DetailBody detail={detail} />
      ) : null}
    </Drawer>
  );
}
