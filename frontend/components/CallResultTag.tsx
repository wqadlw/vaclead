"use client";

/**
 * 触达结果快标（SPEC-AN AN1 七态受控枚举；SPEC-BQ 从 view 页抽共享）。
 * view 页与联系人中心共用——SPEC-Z 教训：共享组件不放页面文件。
 */
import { Dropdown, Tag } from "@douyinfe/semi-ui";
import { Phone } from "lucide-react";

export const CALL_RESULTS: {
  key: string;
  label: string;
  color: "grey" | "orange" | "red" | "green";
}[] = [
  { key: "pending", label: "未拨", color: "grey" },
  { key: "no_answer", label: "无人接", color: "orange" },
  { key: "voicemail", label: "语音信箱", color: "orange" },
  { key: "invalid", label: "空号", color: "red" },
  { key: "connected", label: "已接通", color: "green" },
  { key: "interested", label: "有意向", color: "green" },
  { key: "not_interested", label: "无需求", color: "grey" },
];

export default function CallResultTag({
  result,
  onChange,
}: {
  result: string;
  onChange: (r: string) => void;
}) {
  const cur = CALL_RESULTS.find((x) => x.key === result) ?? CALL_RESULTS[0];
  return (
    <Dropdown
      trigger="click"
      render={
        <div className="flex flex-col py-1">
          {CALL_RESULTS.map((r) => (
            <button
              key={r.key}
              className={`border-0 bg-transparent px-3 py-1.5 text-left text-[12.5px] hover:bg-[var(--raised)] ${r.key === result ? "font-medium text-[var(--accent)]" : "text-[var(--ink)]"}`}
              onClick={() => onChange(r.key)}
            >
              {r.label}
            </button>
          ))}
        </div>
      }
    >
      <Tag color={cur.color} type="light" size="small" className="!cursor-pointer">
        <Phone size={11} className="mr-1 inline" />
        {cur.label}
      </Tag>
    </Dropdown>
  );
}
