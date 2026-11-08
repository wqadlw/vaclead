"use client";

/**
 * 情报页「关键联系人」拨号作战面板（重构）。
 * 销售一天用 N 次的操作面板：打电话前看谁（可拨优先排序）→ 拨打（tel:）→
 * 打后快标（CallResultTag 七态）→ 修正错误数据（hover 显现的编辑 / 删除）。
 * 设计规范：Linear 学派、扁平无渐变无阴影、lucide 图标、framed-sheet 密度。
 * SPEC-Z 教训：成块组件不放页面文件——从 enterprises/view 页抽出独立组件。
 */
import { useMemo, useState } from "react";
import { Input, Modal, Select, Tag, Toast } from "@douyinfe/semi-ui";
import { Edit2, Phone, Trash2 } from "lucide-react";
import CallResultTag from "./CallResultTag";
import { api } from "@/lib/api";

export interface ContactRow {
  id: number;
  name: string;
  role: string;
  title: string | null;
  email: string | null;
  decision_role: string | null;
  verify_state: string;
  phones?: string[];
  call_result?: string;
  called_at?: string | null;
  source: string;
  evidence_claim: string | null;
}

/** 角色中文标签（后端 _ROLES 受控枚举；历史中文角色值经 fallback 原样透出） */
const CONTACT_ROLE_CN: Record<string, string> = {
  legal_rep: "法人",
  buyer_contact: "采购联系人",
  project_contact: "项目联系人",
  map_phone: "地图公开电话",
  proxy: "代理机构",
};

/** 编辑 Modal 角色选项：与「记一条」表单同值域，补 map_phone 使采集回填的联系人可正确显示与修正 */
const ROLE_OPTIONS = [
  { value: "buyer_contact", label: "采购联系人" },
  { value: "legal_rep", label: "法人" },
  { value: "project_contact", label: "项目联系人" },
  { value: "proxy", label: "代理机构" },
  { value: "map_phone", label: "地图公开电话" },
];
const ROLE_KEYS = new Set(ROLE_OPTIONS.map((o) => o.value));

const truncate80 = (s: string) => (s.length > 80 ? `${s.slice(0, 80)}…` : s);

export default function ContactDialPanel({
  contacts,
  onRefresh,
  onMarkResult,
}: {
  contacts: ContactRow[];
  onRefresh: () => void;
  onMarkResult: (cid: number, result: string) => void;
}) {
  const [editing, setEditing] = useState<ContactRow | null>(null);
  const [editForm, setEditForm] = useState({ name: "", phone: "", title: "", role: "" });
  const [editBusy, setEditBusy] = useState(false);

  // 拨号排序（前端渲染前）：可拨 → 决策人 → 公告验证 → 同序 id 降序
  const sorted = useMemo(
    () =>
      [...contacts].sort((a, b) => {
        const aDial = (a.phones?.length ?? 0) > 0 ? 0 : 1;
        const bDial = (b.phones?.length ?? 0) > 0 ? 0 : 1;
        if (aDial !== bDial) return aDial - bDial;
        const aDm = a.decision_role === "decision_maker" ? 0 : 1;
        const bDm = b.decision_role === "decision_maker" ? 0 : 1;
        if (aDm !== bDm) return aDm - bDm;
        const aVer = a.verify_state === "verified" ? 0 : 1;
        const bVer = b.verify_state === "verified" ? 0 : 1;
        if (aVer !== bVer) return aVer - bVer;
        return b.id - a.id;
      }),
    [contacts],
  );
  const dialableCount = sorted.filter((c) => (c.phones?.length ?? 0) > 0).length;

  const openEdit = (c: ContactRow) => {
    setEditing(c);
    setEditForm({
      name: c.name,
      phone: (c.phones ?? []).join(", "),
      title: c.title ?? "",
      // 历史中文角色值不在受控枚举内——不预选，避免保存时静默改角色
      role: ROLE_KEYS.has(c.role) ? c.role : "",
    });
  };

  // PATCH /api/contacts/{cid}：后端仅更新提供的字段；role 未选时不下发
  const submitEdit = async () => {
    if (!editing || !editForm.name.trim()) return;
    setEditBusy(true);
    try {
      await api.patch<{ id: number; name: string; phones: string[] }>(
        `/api/contacts/${editing.id}`,
        {
          name: editForm.name.trim(),
          phone: editForm.phone.trim(),
          title: editForm.title.trim(),
          ...(editForm.role ? { role: editForm.role } : {}),
        },
      );
      Toast.success("联系人已更新");
      setEditing(null);
      onRefresh();
    } catch (e) {
      // 422（电话格式 / 同号重复等）后端 detail 为中文，直接透出
      Toast.error(e instanceof Error ? e.message : "更新失败");
    } finally {
      setEditBusy(false);
    }
  };

  const confirmDelete = (c: ContactRow) => {
    Modal.confirm({
      title: `删除联系人 ${c.name}？`,
      content: "号码将从可拨库移除，误删可通过采集回填恢复。",
      okText: "删除",
      okType: "danger",
      onOk: async () => {
        try {
          await api.delete(`/api/contacts/${c.id}`);
          Toast.success("已删除");
          onRefresh();
        } catch (e) {
          Toast.error(e instanceof Error ? e.message : "删除失败");
        }
      },
    });
  };

  if (contacts.length === 0) {
    return (
      <div className="py-8 text-center text-[13px] text-[var(--muted)]">
        暂无联系人 —— 每日 04:08 回填任务会从政采公告详情页自动补充法定公开联系方式
      </div>
    );
  }

  return (
    <div>
      {/* 作战面板头部：排序口径 + 可拨统计 */}
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[11.5px] text-[var(--muted)]">
        <span>排序：可拨优先 → 决策人优先 → 公告验证优先（悬停卡片可编辑 / 删除）</span>
        <span className="tabular-nums">
          共 {sorted.length} 人 · 可拨 {dialableCount} 人
        </span>
      </div>
      <div className="space-y-1.5">
        {sorted.map((c) => (
          <div
            key={c.id}
            className="group rounded-lg border border-[var(--border)] px-4 py-2.5 transition-colors hover:border-[var(--accent)]"
          >
            <div className="flex items-center gap-3">
              {/* 左：姓名 + 职务 + 角色中文 Tag + 决策链橙标 + 公告验证绿标 */}
              <div className="flex min-w-0 items-center gap-1.5">
                <span
                  className="max-w-[9rem] shrink-0 truncate text-[14px] font-medium text-[var(--ink)]"
                  title={c.name}
                >
                  {c.name}
                </span>
                {c.title && (
                  <span
                    className="hidden min-w-0 max-w-[9rem] truncate text-[12px] text-[var(--muted)] xl:inline"
                    title={c.title}
                  >
                    {c.title}
                  </span>
                )}
                <Tag color="cyan" type="light" size="small" className="!shrink-0">
                  {CONTACT_ROLE_CN[c.role] ?? c.role}
                </Tag>
                {c.decision_role === "decision_maker" && (
                  <Tag color="orange" type="light" size="small" className="!shrink-0">
                    决策链
                  </Tag>
                )}
                {c.verify_state === "verified" && (
                  <Tag color="green" type="light" size="small" className="!shrink-0">
                    公告验证
                  </Tag>
                )}
              </div>
              {/* 中：规范化可拨号码 chips（点击复制 + tel: 拨打）；无号码显示 — */}
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
                {(c.phones ?? []).length > 0 ? (
                  <>
                    {(c.phones ?? []).map((p) => (
                      <span key={p} className="inline-flex items-center gap-0.5">
                        <span
                          className="cursor-pointer rounded border border-[var(--border)] bg-[var(--raised)] px-2 py-1 font-mono text-[12.5px] tabular-nums text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white md:px-1.5 md:py-0.5"
                          title="点击复制号码"
                          onClick={() => {
                            void navigator.clipboard?.writeText(p);
                            Toast.success(`已复制 ${p}`);
                          }}
                        >
                          {p}
                        </span>
                        <a
                          href={`tel:${p}`}
                          title="点击拨打"
                          className="inline-flex h-7 w-7 items-center justify-center rounded text-[var(--muted)] transition-colors hover:bg-[var(--raised)] hover:text-[var(--accent)]"
                          onClick={(ev) => ev.stopPropagation()}
                        >
                          <Phone size={14} />
                        </a>
                      </span>
                    ))}
                    {c.email && (
                      <a
                        href={`mailto:${c.email}`}
                        title={c.email}
                        className="ml-1 hidden max-w-[11rem] truncate text-[12px] text-[var(--muted)] hover:text-[var(--accent)] lg:inline"
                      >
                        {c.email}
                      </a>
                    )}
                  </>
                ) : (
                  <span className="text-[13px] text-[var(--muted)]">—</span>
                )}
              </div>
              {/* 右：触达快标 + hover 显现的编辑 / 删除小按钮 */}
              <div className="flex shrink-0 items-center gap-1">
                <CallResultTag
                  result={(c.call_result as string) ?? "pending"}
                  onChange={(r) => onMarkResult(c.id, r)}
                />
                <span className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    title="编辑联系人"
                    aria-label={`编辑联系人 ${c.name}`}
                    onClick={() => openEdit(c)}
                    className="cursor-pointer rounded border-0 bg-transparent p-1 text-[var(--muted)] hover:bg-[var(--raised)] hover:text-[var(--accent)]"
                  >
                    <Edit2 size={13} />
                  </button>
                  <button
                    title="删除联系人"
                    aria-label={`删除联系人 ${c.name}`}
                    onClick={() => confirmDelete(c)}
                    className="cursor-pointer rounded border-0 bg-transparent p-1 text-[var(--muted)] hover:bg-[var(--raised)] hover:text-[var(--negative)]"
                  >
                    <Trash2 size={13} />
                  </button>
                </span>
              </div>
            </div>
            {/* 证据句保留，截断 80 字（悬停看全文） */}
            {c.evidence_claim && (
              <div
                className="mt-1 truncate border-l-2 border-[var(--accent)] pl-2 text-[11.5px] leading-4 text-[var(--muted)]"
                title={c.evidence_claim}
              >
                {truncate80(c.evidence_claim)}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 编辑联系人 Modal（PATCH /api/contacts/{cid}，仅更新提供字段） */}
      <Modal
        title={editing ? `编辑联系人 · ${editing.name}` : "编辑联系人"}
        visible={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => void submitEdit()}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ disabled: !editForm.name.trim() }}
        confirmLoading={editBusy}
        width={440}
        maskClosable={false}
      >
        <div className="space-y-3 py-1">
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">姓名 *</div>
            <Input
              size="small"
              value={editForm.name}
              placeholder="必填"
              showClear
              onChange={(v) => setEditForm((f) => ({ ...f, name: v }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">
              电话（可空，可粘贴多个号码自动抽取）
            </div>
            <Input
              size="small"
              value={editForm.phone}
              placeholder="手机号或座机，含分机"
              showClear
              onChange={(v) => setEditForm((f) => ({ ...f, phone: v }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">职务</div>
            <Input
              size="small"
              value={editForm.title}
              showClear
              onChange={(v) => setEditForm((f) => ({ ...f, title: v }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">角色</div>
            <Select
              size="small"
              style={{ width: "100%" }}
              placeholder="保持原有角色"
              value={editForm.role || undefined}
              optionList={ROLE_OPTIONS}
              onChange={(v) => setEditForm((f) => ({ ...f, role: String(v) }))}
            />
          </div>
        </div>
      </Modal>
    </div>
  );
}
