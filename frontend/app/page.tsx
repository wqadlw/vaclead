"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, Col, Row, Statistic, Table, Tag } from "antd";
import {
  AlertOutlined,
  FileSearchOutlined,
  ThunderboltOutlined,
  SolutionOutlined,
} from "@ant-design/icons";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { LEAD_STATUS } from "@/lib/constants";

interface DashboardData {
  total_enterprises: number;
  total_signals: number;
  signals_7d: number;
  my_leads_by_status: Record<string, number>;
  pending_followups: {
    lead_id: number;
    enterprise_name: string;
    next_action: string | null;
    next_time: string | null;
  }[];
}

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<DashboardData>("/api/dashboard")
      .then(setData)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  const myLeads = data?.my_leads_by_status ?? {};
  const myTotal = Object.values(myLeads).reduce((a, b) => a + b, 0);

  return (
    <AppShell>
      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic
              title="企业总数"
              value={data?.total_enterprises ?? 0}
              prefix={<FileSearchOutlined />}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="信号总数"
              value={data?.total_signals ?? 0}
              prefix={<ThunderboltOutlined />}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="近7天信号"
              value={data?.signals_7d ?? 0}
              prefix={<AlertOutlined />}
              valueStyle={{ color: "#cf1322" }}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="我的线索"
              value={myTotal}
              prefix={<SolutionOutlined />}
              loading={loading}
              suffix={
                <span className="text-xs text-gray-400">
                  {Object.entries(myLeads)
                    .map(([k, v]) => `${LEAD_STATUS[k]?.label ?? k}:${v}`)
                    .join(" ")}
                </span>
              }
            />
          </Card>
        </Col>
      </Row>

      <Card
        title="待跟进事项"
        className="mt-4"
        extra={
          <a onClick={() => router.push("/leads")} className="cursor-pointer">
            去线索看板
          </a>
        }
      >
        <Table<DashboardData["pending_followups"][number]>
          rowKey="lead_id"
          loading={loading}
          dataSource={data?.pending_followups ?? []}
          pagination={{ pageSize: 10 }}
          locale={{ emptyText: "暂无待跟进，去企业筛选页转化线索吧" }}
          columns={[
            { title: "企业", dataIndex: "enterprise_name" },
            { title: "下一步", dataIndex: "next_action", render: (v) => v || "-" },
            {
              title: "约定时间",
              dataIndex: "next_time",
              render: (v) =>
                v ? <Tag color="orange">{String(v).slice(0, 16)}</Tag> : "-",
            },
          ]}
        />
      </Card>
    </AppShell>
  );
}
