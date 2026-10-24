"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Button,
  DatePicker,
  Descriptions,
  Form,
  Input,
  message,
  Modal,
  Select,
  Space,
  Tabs,
  Table,
  Tag,
} from "antd";
import AppShell from "@/components/AppShell";
import { api, PageData, Lead, FollowupItem } from "@/lib/api";
import { GRADE_COLOR, LEAD_STATUS, LOST_REASONS } from "@/lib/constants";
import dayjs from "dayjs";

const MY_TABS = ["new", "following", "intent", "won", "lost", "pool"] as const;

export default function LeadsPage() {
  const [tab, setTab] = useState<string>("following");
  const [data, setData] = useState<PageData<Lead> | null>(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);

  // 跟进弹窗状态
  const [followOpen, setFollowOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [lostOpen, setLostOpen] = useState(false);
  const [lostLead, setLostLead] = useState<Lead | null>(null);
  const [lostReason, setLostReason] = useState<string>("no_need");
  const [current, setCurrent] = useState<Lead | null>(null);
  const [followups, setFollowups] = useState<FollowupItem[]>([]);
  const [form] = Form.useForm();

  const confirmLost = async () => {
    if (!lostLead) return;
    await patchStatus(lostLead, "lost", lostReason);
    setLostOpen(false);
  };

  const openLost = (lead: Lead) => {
    setLostLead(lead);
    setLostReason("no_need");
    setLostOpen(true);
  };

  const load = useCallback(
    (t: string, p: number) => {
      setLoading(true);
      api
        .get<PageData<Lead>>(`/api/leads?status=${t}&page=${p}&page_size=20`)
        .then(setData)
        .catch((e) => message.error(e.message))
        .finally(() => setLoading(false));
    },
    [],
  );

  useEffect(() => load(tab, page), [tab, page, load]);

  const patchStatus = async (lead: Lead, status: string, lost_reason?: string) => {
    try {
      await api.patch(`/api/leads/${lead.id}`, { status, lost_reason });
      message.success(`已${status === "pool" ? "退回公海" : "流转到"}${LEAD_STATUS[status]?.label ?? status}`);
      load(tab, page);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const openFollow = (lead: Lead) => {
    setCurrent(lead);
    form.resetFields();
    setFollowOpen(true);
  };

  const submitFollow = async () => {
    const values = await form.validateFields();
    try {
      await api.post(`/api/leads/${current!.id}/followups`, {
        content: values.content,
        next_action: values.next_action || undefined,
        next_time: values.next_time ? values.next_time.format("YYYY-MM-DD HH:mm") : undefined,
      });
      message.success("跟进已记录");
      setFollowOpen(false);
      load(tab, page);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "保存失败");
    }
  };

  const openDetail = async (lead: Lead) => {
    setCurrent(lead);
    setDetailOpen(true);
    try {
      const list = await api.get<FollowupItem[]>(`/api/leads/${lead.id}/followups`);
      setFollowups(list);
    } catch {
      setFollowups([]);
    }
  };

  const transitionButtons = (lead: Lead) => {
    const btns: React.ReactNode[] = [];
    if (lead.status === "new" || lead.status === "pool") {
      btns.push(
        <Button key="claim" size="small" type="primary" onClick={() => patchStatus(lead, "following")}>
          {lead.status === "pool" ? "领取" : "开始跟进"}
        </Button>,
      );
    }
    if (lead.status === "following") {
      btns.push(
        <Button key="intent" size="small" onClick={() => patchStatus(lead, "intent")}>标记意向</Button>,
        <Button key="lost" size="small" danger onClick={() => openLost(lead)}>放弃</Button>,
      );
    }
    if (lead.status === "intent") {
      btns.push(
        <Button key="won" size="small" type="primary" onClick={() => patchStatus(lead, "won")}>成交</Button>,
        <Button key="lost2" size="small" danger onClick={() => openLost(lead)}>放弃</Button>,
      );
    }
    btns.push(
      <Button key="follow" size="small" onClick={() => openFollow(lead)}>跟进</Button>,
      <Button key="detail" size="small" type="link" onClick={() => openDetail(lead)}>记录</Button>,
    );
    return <Space size={4} wrap>{btns}</Space>;
  };

  return (
    <AppShell>
      <Tabs
        activeKey={tab}
        onChange={(k) => { setTab(k); setPage(1); }}
        items={MY_TABS.map((s) => ({
          key: s,
          label: `${LEAD_STATUS[s].label}${data && tab === s ? ` (${data.total})` : ""}`,
        }))}
      />
      <Table<Lead>
        rowKey="id"
        loading={loading}
        dataSource={data?.items ?? []}
        pagination={{ current: page, total: data?.total ?? 0, pageSize: 20, onChange: setPage }}
        columns={[
          {
            title: "企业",
            dataIndex: ["enterprise", "name"],
            render: (v) => <b>{v}</b>,
          },
          {
            title: "行业",
            dataIndex: ["enterprise", "industry_name"],
            render: (v) => v || "-",
          },
          {
            title: "等级",
            dataIndex: "grade",
            render: (v) => (v ? <Tag color={GRADE_COLOR[v]}>{v}</Tag> : "-"),
          },
          {
            title: "ICP/意图",
            render: (_, r) =>
              `${r.enterprise?.icp_score ?? 0} / ${r.enterprise?.intent_score ?? 0}`,
          },
          {
            title: "最近跟进",
            dataIndex: "last_followed_at",
            render: (v) => (v ? String(v).slice(0, 16) : "未跟进"),
          },
          { title: "操作", render: (_, r) => transitionButtons(r), width: 320 },
        ]}
      />

      <Modal
        title={`跟进记录 — ${current?.enterprise?.name ?? ""}`}
        open={followOpen}
        onOk={submitFollow}
        onCancel={() => setFollowOpen(false)}
        okText="保存"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="content"
            label="跟进内容"
            rules={[{ required: true, message: "请填写跟进内容" }]}
          >
            <Input.TextArea rows={4} placeholder="沟通了什么？客户态度如何？" />
          </Form.Item>
          <Form.Item name="next_action" label="下一步动作">
            <Input placeholder="如：发送设备参数表 / 约参观工厂" />
          </Form.Item>
          <Form.Item name="next_time" label="下次跟进时间">
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`跟进历史 — ${current?.enterprise?.name ?? ""}`}
        open={detailOpen}
        footer={null}
        onCancel={() => setDetailOpen(false)}
        width={640}
      >
        {followups.length === 0 && "暂无记录"}
        {followups.map((f) => (
          <Descriptions key={f.id} size="small" column={1} className="mb-3" bordered>
            <Descriptions.Item label="时间">{String(f.created_at).slice(0, 16)}</Descriptions.Item>
            <Descriptions.Item label="内容">{f.content}</Descriptions.Item>
            <Descriptions.Item label="下一步">
              {f.next_action || "-"} {f.next_time ? `（${f.next_time}）` : ""}
            </Descriptions.Item>
          </Descriptions>
        ))}
      </Modal>

      <Modal
        title={`放弃线索 — ${lostLead?.enterprise?.name ?? ""}`}
        open={lostOpen}
        onOk={confirmLost}
        onCancel={() => setLostOpen(false)}
        okText="确认放弃"
        okButtonProps={{ danger: true }}
      >
        <Select
          value={lostReason}
          onChange={setLostReason}
          style={{ width: "100%" }}
          options={Object.entries(LOST_REASONS).map(([value, label]) => ({ value, label }))}
        />
      </Modal>
    </AppShell>
  );
}
