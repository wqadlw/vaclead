"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Form, Input, InputNumber, message, Select, Space, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import AppShell from "@/components/AppShell";
import EnterpriseDrawer from "@/components/EnterpriseDrawer";
import { api, ApiError } from "@/lib/api";
import type { Enterprise, PageData } from "@/lib/api";
import { INDUSTRY_OPTIONS, TIER_COLOR } from "@/lib/constants";

const PAGE_SIZE = 20;

/** 简化的固定省级行政区列表（筛选用，不要求完整）。 */
const PROVINCE_OPTIONS = [
  "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
  "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
  "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
  "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
];

const SIGNAL_DAY_OPTIONS = [
  { value: 7, label: "近 7 天" },
  { value: 30, label: "近 30 天" },
  { value: 90, label: "近 90 天" },
  { value: 365, label: "近一年" },
];

const SCORE_MIN_OPTIONS = [
  { value: 40, label: "≥ 40 分" },
  { value: 60, label: "≥ 60 分" },
  { value: 80, label: "≥ 80 分" },
];

/** 后端语义：is_competitor=0 排除同行；1 仅看同行；-1 不筛选（含同行，置灰展示）。 */
const COMPETITOR_OPTIONS = [
  { value: -1, label: "含同行" },
  { value: 0, label: "排除同行" },
  { value: 1, label: "仅同行" },
];

interface FilterValues {
  q?: string;
  industry_key?: string;
  province?: string;
  city?: string;
  district?: string;
  capital_min?: number | null;
  capital_max?: number | null;
  signal_days?: number | null;
  score_min?: number | null;
  is_competitor: number;
}

const TIER_BY_KEY: Record<string, number> = {};
for (const opt of INDUSTRY_OPTIONS) TIER_BY_KEY[opt.key] = opt.tier;

function tierColorOf(key: string | null): string {
  return TIER_COLOR[TIER_BY_KEY[key ?? ""] ?? 3] ?? "default";
}

function regionOf(e: Enterprise): string {
  const provCity = [e.province, e.city]
    .map((x) => (x ?? "").trim())
    .filter((x) => x.length > 0);
  if (provCity.length > 0) return provCity.join(" ");
  return (e.district ?? "").trim() || "-";
}

function capitalOf(v: number | null): string {
  return v == null ? "-" : `${v.toLocaleString("zh-CN")} 万`;
}

export default function EnterprisesPage() {
  const [form] = Form.useForm<FilterValues>();
  const [filters, setFilters] = useState<FilterValues>({ is_competitor: -1 });
  const [page, setPage] = useState(1);
  const [refreshKey, setRefreshKey] = useState(0);
  const [data, setData] = useState<PageData<Enterprise> | null>(null);
  const [loading, setLoading] = useState(false);
  const [drawerId, setDrawerId] = useState<number | null>(null);
  const [convertingId, setConvertingId] = useState<number | null>(null);
  const lastSubmitAt = useRef(0);

  const fetchData = useCallback(async (f: FilterValues, p: number) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      const q = f.q?.trim();
      if (q) params.set("q", q);
      if (f.industry_key) params.set("industry_key", f.industry_key);
      const province = f.province?.trim();
      if (province) params.set("province", province);
      const city = f.city?.trim();
      if (city) params.set("city", city);
      const district = f.district?.trim();
      if (district) params.set("district", district);
      if (typeof f.capital_min === "number") params.set("capital_min", String(f.capital_min));
      if (typeof f.capital_max === "number") params.set("capital_max", String(f.capital_max));
      if (typeof f.signal_days === "number") params.set("signal_days", String(f.signal_days));
      if (typeof f.score_min === "number") params.set("score_min", String(f.score_min));
      params.set("is_competitor", String(f.is_competitor));
      params.set("page", String(p));
      params.set("page_size", String(PAGE_SIZE));
      const res = await api.get<PageData<Enterprise>>(`/api/enterprises?${params.toString()}`);
      setData(res);
    } catch (e) {
      message.error(`查询失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  }, []);

  // 首次挂载加载；筛选/页码变化或手动刷新时重新请求（筛选控件本身不自动触发）
  useEffect(() => {
    void fetchData(filters, page);
  }, [fetchData, filters, page, refreshKey]);

  // Enter 同时触发 Input.Search 的 onSearch 与表单原生提交，此处做去抖防止重复请求
  const triggerSearch = () => {
    const now = Date.now();
    if (now - lastSubmitAt.current < 300) return;
    lastSubmitAt.current = now;
    form.submit();
  };

  const onFinish = (values: FilterValues) => {
    setFilters(values);
    setPage(1);
    setRefreshKey((k) => k + 1);
  };

  const convertToLead = async (record: Enterprise) => {
    setConvertingId(record.id);
    try {
      await api.post(`/api/enterprises/${record.id}/lead`, {});
      message.success(`已转为线索：${record.name}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        message.error(`转线索失败（撞单）：${e.message}`);
      } else {
        message.error(`转线索失败：${e instanceof Error ? e.message : "未知错误"}`);
      }
    } finally {
      setConvertingId(null);
    }
  };

  const columns: ColumnsType<Enterprise> = [
    {
      title: "企业名称",
      dataIndex: "name",
      key: "name",
      ellipsis: true,
      render: (_, record) => (
        <Button
          type="link"
          size="small"
          className="!p-0"
          onClick={() => setDrawerId(record.id)}
          title={record.name}
        >
          {record.name}
        </Button>
      ),
    },
    {
      title: "行业",
      dataIndex: "industry_name",
      key: "industry",
      width: 180,
      render: (_, record) => {
        const industryTag = record.industry_name ? (
          <Tag color={tierColorOf(record.industry_key)}>{record.industry_name}</Tag>
        ) : (
          <span className="text-gray-400">-</span>
        );
        if (record.is_competitor === 1) {
          return (
            <Space size={4} wrap>
              <Tag color="warning">疑似同行</Tag>
              {record.industry_name ? industryTag : null}
            </Space>
          );
        }
        return industryTag;
      },
    },
    {
      title: "地区",
      key: "region",
      width: 150,
      ellipsis: true,
      render: (_, record) => regionOf(record),
    },
    {
      title: "注册资本",
      key: "capital",
      width: 110,
      align: "right",
      render: (_, record) => capitalOf(record.registered_capital_wan),
    },
    {
      title: "ICP 分",
      dataIndex: "icp_score",
      width: 85,
      align: "right",
      render: (v: number) => Math.round(v),
    },
    {
      title: "意图分",
      dataIndex: "intent_score",
      width: 85,
      align: "right",
      render: (v: number) => Math.round(v),
    },
    {
      title: "信号数",
      key: "signal_count",
      width: 80,
      align: "right",
      render: (_, record) =>
        record.signal_count ? <span className="font-semibold">{record.signal_count}</span> : 0,
    },
    {
      title: "操作",
      key: "action",
      width: 96,
      render: (_, record) => (
        <Button
          size="small"
          loading={convertingId === record.id}
          onClick={() => void convertToLead(record)}
        >
          转线索
        </Button>
      ),
    },
  ];

  return (
    <AppShell>
      <div className="flex flex-col gap-4">
        <div className="rounded border border-gray-200 bg-white p-4">
          <Form
            form={form}
            layout="inline"
            initialValues={{ is_competitor: -1 }}
            onFinish={onFinish}
            className="flex flex-wrap items-center gap-y-3"
          >
            <Form.Item name="industry_key" label="行业" className="!mb-0 !mr-3">
              <Select
                allowClear
                showSearch
                placeholder="全部行业"
                optionFilterProp="label"
                style={{ width: 150 }}
                options={INDUSTRY_OPTIONS.map((o) => ({ value: o.key, label: o.name }))}
              />
            </Form.Item>
            <Form.Item name="province" label="省份" className="!mb-0 !mr-3">
              <Select
                allowClear
                showSearch
                placeholder="全部"
                optionFilterProp="label"
                style={{ width: 120 }}
                options={PROVINCE_OPTIONS.map((p) => ({ value: p, label: p }))}
              />
            </Form.Item>
            <Form.Item name="city" label="城市" className="!mb-0 !mr-3">
              <Input allowClear placeholder="如：苏州" style={{ width: 110 }} />
            </Form.Item>
            <Form.Item name="district" label="区县" className="!mb-0 !mr-3">
              <Input allowClear placeholder="如：昆山市" style={{ width: 110 }} />
            </Form.Item>
            <Form.Item label="注册资本（万）" className="!mb-0 !mr-3">
              <Space size={4}>
                <Form.Item name="capital_min" noStyle>
                  <InputNumber min={0} placeholder="最低" style={{ width: 96 }} />
                </Form.Item>
                <span className="text-gray-400">~</span>
                <Form.Item name="capital_max" noStyle>
                  <InputNumber min={0} placeholder="最高" style={{ width: 96 }} />
                </Form.Item>
              </Space>
            </Form.Item>
            <Form.Item name="signal_days" label="信号天数" className="!mb-0 !mr-3">
              <Select
                allowClear
                placeholder="不限"
                style={{ width: 110 }}
                options={SIGNAL_DAY_OPTIONS}
              />
            </Form.Item>
            <Form.Item name="score_min" label="最低评分" className="!mb-0 !mr-3">
              <Select
                allowClear
                placeholder="不限"
                style={{ width: 100 }}
                options={SCORE_MIN_OPTIONS}
              />
            </Form.Item>
            <Form.Item name="is_competitor" label="疑似同行" className="!mb-0 !mr-3">
              <Select style={{ width: 110 }} options={COMPETITOR_OPTIONS} />
            </Form.Item>
            <Form.Item name="q" label="关键词" className="!mb-0 !mr-3 flex-1 min-w-[220px]">
              <Input.Search
                placeholder="企业名 / 经营范围关键词（FTS）"
                allowClear
                onSearch={triggerSearch}
              />
            </Form.Item>
            <Form.Item className="!mb-0">
              <Button type="primary" htmlType="submit" loading={loading}>
                查询
              </Button>
            </Form.Item>
          </Form>
        </div>

        <div className="rounded border border-gray-200 bg-white p-4">
          <Table<Enterprise>
            rowKey="id"
            size="middle"
            columns={columns}
            dataSource={data?.items ?? []}
            loading={loading}
            scroll={{ x: 960 }}
            rowClassName={(record) => (record.is_competitor === 1 ? "opacity-50 grayscale" : "")}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total: data?.total ?? 0,
              showSizeChanger: false,
              showTotal: (t) => `共 ${t} 家企业`,
              onChange: (p) => setPage(p),
            }}
          />
        </div>
      </div>

      <EnterpriseDrawer
        open={drawerId !== null}
        enterpriseId={drawerId}
        onClose={() => setDrawerId(null)}
        onConverted={() => void fetchData(filters, page)}
      />
    </AppShell>
  );
}
