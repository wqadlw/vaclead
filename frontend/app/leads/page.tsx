"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, DatePicker, Form, Modal, Select, SideSheet, Table, Tabs, Tag, Timeline, Toast } from "@douyinfe/semi-ui";
import { motion } from "framer-motion";
import { PhoneCall } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import type { FollowupItem, Lead, PageData } from "@/lib/api";
import { GRADE_COLOR, LEAD_STATUS, LOST_REASONS } from "@/lib/constants";

const MY_TABS = ["following", "new", "intent", "won", "lost", "pool"] as const;

const GRADE_TAG: Record<string, "red" | "orange" | "blue"> = { S: "red", A: "orange", B: "blue" };

export default function LeadsPage() {
  const [tab, setTab] = useState<string>("following");
  const [data, setData] = useState<PageData<Lead> | null>(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);

  const [followOpen, setFollowOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [lostOpen, setLostOpen] = useState(false);
  const [current, setCurrent] = useState<Lead | null>(null);
  const [lostLead, setLostLead] = useState<Lead | null>(null);
  const [lostReason, setLostReason] = useState<string>("no_need");
  const [followups, setFollowups] = useState<FollowupItem[]>([]);

  const load = useCallback(
    (t: string, p: number) => {
      setLoading(true);
      api
        .get<PageData<Lead>>(`/api/leads?status=${t}&page=${p}&page_size=20`)
        .then(setData)
        .catch((e) => Toast.error(e.message))
        .finally(() => setLoading(false));
    },
    [],
  );

  useEffect(() => load(tab, page), [tab, page, load]);

  const patchStatus = async (lead: Lead, status: string, lost_reason?: string) => {
    try {
      await api.patch(`/api/leads/${lead.id}`, { status, lost_reason });
      Toast.success(status === "pool" ? "已退回公海" : `已流转到「${LEAD_STATUS[status]?.label ?? status}」`);
      load(tab, page);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const submitFollow = async (values: { content: string; next_action?: string; next_time?: string }) => {
    if (!current) return;
    try {
      await api.post(`/api/leads/${current.id}/followups`, {
        content: values.content,
        next_action: values.next_action || undefined,
        next_time: values.next_time || undefined,
      });
      Toast.success("跟进已记录");
      setFollowOpen(false);
      load(tab, page);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    }
  };

  const openHistory = async (lead: Lead) => {
    setCurrent(lead);
    setHistoryOpen(true);
    try {
      setFollowups(await api.get<FollowupItem[]>(`/api/leads/${lead.id}/followups`));
    } catch {
      setFollowups([]);
    }
  };

  const actions = (lead: Lead) => {
    const btns: React.ReactNode[] = [];
    if (lead.status === "new" || lead.status === "pool") {
      btns.push(
        <Button key="claim" size="small" theme="solid" onClick={() => patchStatus(lead, "following")}>
          {lead.status === "pool" ? "领取" : "开始跟进"}
        </Button>,
      );
    }
    if (lead.status === "following") {
      btns.push(
        <Button key="intent" size="small" onClick={() => patchStatus(lead, "intent")}>
          标记意向
        </Button>,
        <Button
          key="lost"
          size="small"
          type="danger"
          theme="borderless"
          onClick={() => {
            setLostLead(lead);
            setLostReason("no_need");
            setLostOpen(true);
          }}
        >
          放弃
        </Button>,
      );
    }
    if (lead.status === "intent") {
      btns.push(
        <Button key="won" size="small" theme="solid" onClick={() => patchStatus(lead, "won")}>
          成交
        </Button>,
        <Button
          key="lost2"
          size="small"
          type="danger"
          theme="borderless"
          onClick={() => {
            setLostLead(lead);
            setLostReason("no_need");
            setLostOpen(true);
          }}
        >
          放弃
        </Button>,
      );
    }
    btns.push(
      <Button key="follow" size="small" theme="light" icon={<PhoneCall size={13} />} onClick={() => { setCurrent(lead); setFollowOpen(true); }}>
        跟进
      </Button>,
      <Button key="detail" size="small" theme="borderless" type="tertiary" onClick={() => openHistory(lead)}>
        记录
      </Button>,
    );
    return <div className="flex items-center gap-1.5 flex-wrap">{btns}</div>;
  };

  const columns = [
    {
      title: "企业",
      dataIndex: ["enterprise", "name"],
      render: (v: string) => <span className="font-medium text-slate-700">{v}</span>,
    },
    { title: "行业", dataIndex: ["enterprise", "industry_name"], width: 130, render: (v: string) => v || "-" },
    {
      title: "等级",
      dataIndex: "grade",
      width: 70,
      render: (v: string) => (v ? <Tag color={GRADE_TAG[v]} type="solid">{v}</Tag> : <span className="text-slate-300">-</span>),
    },
    {
      title: "ICP / 意图",
      width: 110,
      render: (_: unknown, r: Lead) => (
        <span className="tabular-nums text-slate-600">
          {r.enterprise?.icp_score ?? 0} <span className="text-slate-300">/</span>{" "}
          <span className={r.enterprise?.intent_score ? "text-rose-500" : ""}>{r.enterprise?.intent_score ?? 0}</span>
        </span>
      ),
    },
    {
      title: "最近跟进",
      dataIndex: "last_followed_at",
      width: 130,
      render: (v: string) => (v ? <span className="text-slate-500 text-[12.5px]">{String(v).slice(0, 16).replace("T", " ")}</span> : <span className="text-slate-300">未跟进</span>),
    },
    { title: "操作", width: 300, render: (_: unknown, r: Lead) => actions(r) },
  ];

  return (
    <AppShell>
      <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}>
        <div className="rounded-2xl bg-white border border-slate-100 shadow-[0_1px_2px_rgba(15,23,42,.03),0_8px_24px_-16px_rgba(15,23,42,.12)] overflow-hidden">
          <Tabs
            type="button"
            activeKey={tab}
            onChange={(k) => {
              setTab(k as string);
              setPage(1);
            }}
            className="!px-4 !pt-3"
            tabList={MY_TABS.map((s) => ({ tab: LEAD_STATUS[s].label, itemKey: s }))}
          />
          <Table<Lead>
            rowKey="id"
            columns={columns as never}
            dataSource={data?.items ?? []}
            loading={loading}
            size="middle"
            empty={
              tab === "pool"
                ? "公海暂时没有可领取的线索"
                : "该状态下暂无线索 —— 去企业筛选页转化高分客户"
            }
            pagination={{
              currentPage: page,
              pageSize: 20,
              total: data?.total ?? 0,
              onPageChange: (p: number) => setPage(p),
            }}
          />
        </div>
      </motion.div>

      {/* 跟进弹窗 */}
      <Modal
        title={`跟进 — ${current?.enterprise?.name ?? ""}`}
        visible={followOpen}
        onCancel={() => setFollowOpen(false)}
        footer={null}
        width={480}
      >
        <Form onSubmit={submitFollow} labelPosition="top">
          <Form.TextArea
            field="content"
            label="跟进内容"
            rows={4}
            rules={[{ required: true, message: "请填写跟进内容" }]}
            placeholder="沟通了什么？客户态度如何？"
          />
          <Form.Input field="next_action" label="下一步动作" placeholder="如：发送设备参数表 / 约参观工厂" />
          <Form.DatePicker
            field="next_time"
            label="下次跟进时间"
            type="dateTime"
            style={{ width: "100%" }}
          />
          <Button htmlType="submit" theme="solid" block className="!mt-2">
            保存跟进
          </Button>
        </Form>
      </Modal>

      {/* 跟进历史 */}
      <SideSheet
        title={`跟进历史 — ${current?.enterprise?.name ?? ""}`}
        visible={historyOpen}
        onCancel={() => setHistoryOpen(false)}
        width={480}
      >
        {followups.length === 0 ? (
          <div className="text-[13px] text-slate-400">暂无记录</div>
        ) : (
          <Timeline mode="left" className="mt-2">
            {followups.map((f) => (
              <Timeline.Item key={f.id} time={String(f.created_at).slice(0, 16).replace("T", " ")}>
                <div className="text-[13.5px] text-slate-700 leading-6">{f.content}</div>
                {(f.next_action || f.next_time) && (
                  <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                    {f.next_action && <Tag color="blue" type="light">{f.next_action}</Tag>}
                    {f.next_time && <Tag color="orange" type="light">{String(f.next_time).slice(0, 16)}</Tag>}
                  </div>
                )}
              </Timeline.Item>
            ))}
          </Timeline>
        )}
      </SideSheet>

      {/* 放弃原因 */}
      <Modal
        title={`放弃线索 — ${lostLead?.enterprise?.name ?? ""}`}
        visible={lostOpen}
        onOk={() => {
          if (lostLead) void patchStatus(lostLead, "lost", lostReason);
          setLostOpen(false);
        }}
        onCancel={() => setLostOpen(false)}
        okText="确认放弃"
        okButtonProps={{ type: "danger", theme: "solid" }}
      >
        <Select
          style={{ width: "100%" }}
          optionList={Object.entries(LOST_REASONS).map(([value, label]) => ({ value, label }))}
          value={lostReason}
          onChange={(v) => setLostReason(v as string)}
        />
      </Modal>
    </AppShell>
  );
}
