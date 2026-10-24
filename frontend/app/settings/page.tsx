"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Badge,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  message,
  Table,
  Tabs,
  Tag,
} from "antd";
import AppShell from "@/components/AppShell";
import { api, PageData } from "@/lib/api";
import { INDUSTRY_OPTIONS } from "@/lib/constants";

interface SettingsData {
  amap_key_masked: string | null;
  zhiliaoke_key_masked: string | null;
  agent_backends: Record<string, string>;
}

interface AgentRunRow {
  id: number;
  agent_name: string;
  backend: string;
  decision_summary: string;
  duration_ms: number;
  task_id: string | null;
  created_at: string;
}

const DEVICE_TERMS_PREVIEW = [
  "真空泵", "真空机组", "罗茨泵", "分子泵", "旋片泵", "螺杆真空泵", "爪式真空泵",
  "涡旋真空泵", "扩散泵", "真空镀膜机", "磁控溅射", "蒸发镀膜", "PVD设备", "CVD设备",
  "冻干机", "真空冷冻干燥机", "真空干燥箱", "真空烧结炉", "真空热处理炉", "真空腔体",
  "真空检漏", "真空包装机", "真空获得设备", "真空阀门",
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [runs, setRuns] = useState<PageData<AgentRunRow> | null>(null);
  const [runPage, setRunPage] = useState(1);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const loadSettings = useCallback(() => {
    api.get<SettingsData>("/api/settings").then(setSettings).catch(() => undefined);
  }, []);

  const loadRuns = useCallback(
    (p: number) => {
      api
        .get<PageData<AgentRunRow>>(`/api/agent-runs?page=${p}&page_size=15`)
        .then(setRuns)
        .catch(() => undefined);
    },
    [],
  );

  useEffect(() => loadSettings(), [loadSettings]);
  useEffect(() => loadRuns(runPage), [runPage, loadRuns]);

  const saveKeys = async (values: { amap_key?: string; zhiliaoke_key?: string }) => {
    setSaving(true);
    try {
      await api.put("/api/settings", values);
      message.success("已保存");
      form.resetFields();
      loadSettings();
    } catch (e) {
      message.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppShell>
      <Tabs
        items={[
          {
            key: "keys",
            label: "数据源 Key",
            children: (
              <Card title="外部服务密钥" style={{ maxWidth: 560 }}>
                <Descriptions size="small" className="mb-4">
                  <Descriptions.Item label="高德 Key">
                    {settings?.amap_key_masked ? (
                      <Tag color="green">{settings.amap_key_masked}</Tag>
                    ) : (
                      <Tag>未配置</Tag>
                    )}
                  </Descriptions.Item>
                  <Descriptions.Item label="知了标讯 Key">
                    {settings?.zhiliaoke_key_masked ? (
                      <Tag color="green">{settings.zhiliaoke_key_masked}</Tag>
                    ) : (
                      <Tag>未配置</Tag>
                    )}
                  </Descriptions.Item>
                </Descriptions>
                <Form form={form} layout="vertical" onFinish={saveKeys}>
                  <Form.Item
                    name="amap_key"
                    label="高德 Web 服务 Key（多 Key 用英文逗号分隔）"
                    extra="申请指引见 docs/SETUP.md"
                  >
                    <Input.Password placeholder="留空表示不修改" />
                  </Form.Item>
                  <Form.Item name="zhiliaoke_key" label="知了标讯 API Key">
                    <Input.Password placeholder="留空表示不修改" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={saving}>
                    保存
                  </Button>
                </Form>
              </Card>
            ),
          },
          {
            key: "vocab",
            label: "词表预览",
            children: (
              <Card title="真空设备词表（config/vacuum.yaml）">
                <p className="mb-2 font-medium">设备词（{DEVICE_TERMS_PREVIEW.length}+）</p>
                <div className="mb-4">
                  {DEVICE_TERMS_PREVIEW.map((t) => (
                    <Tag key={t} color="geekblue">{t}</Tag>
                  ))}
                </div>
                <p className="mb-2 font-medium">下游行业（按优先级）</p>
                <Descriptions size="small" bordered column={1}>
                  {INDUSTRY_OPTIONS.map((i) => (
                    <Descriptions.Item key={i.key} label={`${i.name}（tier ${i.tier}）`}>
                      <Tag color={i.tier === 1 ? "geekblue" : i.tier === 2 ? "cyan" : "default"}>
                        {i.key}
                      </Tag>
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </Card>
            ),
          },
          {
            key: "agents",
            label: "Agent 日志",
            children: (
              <Card title="Agent 工作日志">
                <Table<AgentRunRow>
                  rowKey="id"
                  size="small"
                  dataSource={runs?.items ?? []}
                  pagination={{
                    current: runPage,
                    total: runs?.total ?? 0,
                    pageSize: 15,
                    onChange: setRunPage,
                  }}
                  columns={[
                    { title: "Agent", dataIndex: "agent_name", width: 140 },
                    {
                      title: "后端",
                      dataIndex: "backend",
                      width: 80,
                      render: (v) => (
                        <Badge status={v === "rule" ? "default" : "processing"} text={v} />
                      ),
                    },
                    { title: "决策摘要", dataIndex: "decision_summary", ellipsis: true },
                    { title: "耗时", dataIndex: "duration_ms", width: 90, render: (v) => `${v}ms` },
                    {
                      title: "时间",
                      dataIndex: "created_at",
                      width: 150,
                      render: (v) => String(v).slice(0, 19).replace("T", " "),
                    },
                  ]}
                />
              </Card>
            ),
          },
          {
            key: "backends",
            label: "后端切换",
            children: (
              <Card title="Agent 后端实现（当前全部为规则引擎）">
                <Descriptions size="small" bordered column={1} style={{ maxWidth: 480 }}>
                  {Object.entries(settings?.agent_backends ?? { all: "rule" }).map(
                    ([name, backend]) => (
                      <Descriptions.Item key={name} label={name}>
                        <Tag color={backend === "rule" ? "blue" : "purple"}>{backend}</Tag>
                      </Descriptions.Item>
                    ),
                  )}
                </Descriptions>
                <p className="text-gray-400 text-xs mt-3">
                  LLM 后端为 P2 预留位：配置 GLM/DeepSeek 等 OpenAI 兼容 Key 后可切换 signal_parser / icp_judge / query_translator。
                </p>
              </Card>
            ),
          },
        ]}
      />
    </AppShell>
  );
}
