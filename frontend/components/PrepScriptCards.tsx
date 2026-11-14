"use client";

/**
 * 通话驾驶舱「话术」区（SPEC-SALE S1）：prep 聚合端点新增的 script_cards
 * 直接对准通话中场景——卡片折叠列表 + 一键复制，零跳转。
 * 容错契约：后端并行上线中，prep 响应无 script_cards 键 / 空数组 /
 * 元素缺字段时一律静默不渲染或降级显示，绝不阻塞驾驶舱主体。
 */
import { useState } from "react";
import { ChevronDown, Copy } from "lucide-react";
import { Tag, Toast } from "@douyinfe/semi-ui";

export interface PrepScriptCard {
  title: string;
  scene: string;
  role_hint: string | null;
  body_md: string;
}

/** scene 枚举中文（后端 script_card.scene 受控五值；未知值原样透出） */
export const SCRIPT_SCENE_CN: Record<string, string> = {
  first_contact: "首联",
  competitor_replace: "竞对替换",
  repeat_purchase: "老户复购",
  proposal_window: "拟建窗口",
  generic: "通用",
};

/** role_hint 对齐 Enterprise.customer_role（oem/end_user/reseller/integrator，空=通用） */
export const SCRIPT_ROLE_CN: Record<string, string> = {
  oem: "应用设备制造商",
  end_user: "终端用户",
  reseller: "分销商",
  integrator: "系统工程商",
};

const SCENE_TAG_COLOR: Record<string, "blue" | "red" | "green" | "cyan" | "grey"> = {
  competitor_replace: "red",
  proposal_window: "cyan",
  repeat_purchase: "green",
  first_contact: "blue",
  generic: "grey",
};

/** 容错取卡：非数组 / 元素缺 title+body 一律丢弃（后端半程上线也不炸 UI） */
function normalize(cards: unknown): PrepScriptCard[] {
  if (!Array.isArray(cards)) return [];
  return cards
    .filter((c): c is Record<string, unknown> => !!c && typeof c === "object")
    .map((c) => ({
      title: typeof c.title === "string" ? c.title : "",
      scene: typeof c.scene === "string" ? c.scene : "generic",
      role_hint: typeof c.role_hint === "string" && c.role_hint ? c.role_hint : null,
      body_md: typeof c.body_md === "string" ? c.body_md : "",
    }))
    .filter((c) => c.title || c.body_md);
}

export default function PrepScriptCards({ cards }: { cards?: unknown }) {
  const list = normalize(cards);
  // 折叠列表：默认收起全部，点标题展开/再点收起（通话中一眼扫标题，需要时展开）
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  if (list.length === 0) return null;

  const copyCard = (c: PrepScriptCard) => {
    const text = c.body_md || c.title;
    void navigator.clipboard
      ?.writeText(text)
      .then(() => Toast.success("已复制"))
      .catch(() => {
        // 非安全上下文（http 非 localhost）clipboard 不可用：降级 execCommand
        try {
          const ta = document.createElement("textarea");
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
          Toast.success("已复制");
        } catch {
          Toast.error("复制失败，请手动选择文本");
        }
      });
  };

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[13.5px] font-medium text-[var(--ink)]">
          话术（{list.length}）
        </div>
        <div className="text-[11.5px] text-[var(--muted)]">点开照念 · 一键复制</div>
      </div>
      <div className="space-y-1.5">
        {list.map((c, i) => {
          const open = openIdx === i;
          const scene = SCRIPT_SCENE_CN[c.scene] ?? c.scene;
          const role = c.role_hint ? (SCRIPT_ROLE_CN[c.role_hint] ?? c.role_hint) : null;
          return (
            <div
              key={`${c.title}-${i}`}
              className={`rounded-lg border transition-colors ${
                open
                  ? "border-[var(--accent)] bg-white"
                  : "border-[var(--border)] bg-white hover:border-[var(--accent)]"
              }`}
            >
              <div className="flex items-center gap-2 px-3 py-2">
                <button
                  className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 border-0 bg-transparent p-0 text-left"
                  onClick={() => setOpenIdx(open ? null : i)}
                  aria-expanded={open}
                >
                  <ChevronDown
                    size={14}
                    className={`shrink-0 text-[var(--muted)] transition-transform ${open ? "rotate-180" : ""}`}
                  />
                  <span className="truncate text-[13px] font-medium text-[var(--ink)]" title={c.title}>
                    {c.title || "（未命名话术）"}
                  </span>
                </button>
                <span className="flex shrink-0 items-center gap-1">
                  <Tag color={SCENE_TAG_COLOR[c.scene] ?? "grey"} type="light" size="small">
                    {scene}
                  </Tag>
                  {role && (
                    <Tag color="orange" type="light" size="small">
                      {role}
                    </Tag>
                  )}
                  <button
                    title="复制话术正文"
                    aria-label={`复制话术：${c.title}`}
                    className="shrink-0 cursor-pointer rounded border-0 bg-transparent p-1 text-[var(--muted)] hover:bg-[var(--raised)] hover:text-[var(--accent)]"
                    onClick={() => copyCard(c)}
                  >
                    <Copy size={13} />
                  </button>
                </span>
              </div>
              {open && (
                <div className="border-t border-[var(--rule)] px-3 py-2.5">
                  <pre className="whitespace-pre-wrap break-words border-0 bg-transparent p-0 font-[inherit] text-[12.5px] leading-5 text-[var(--ink)]">
                    {c.body_md || "（正文为空）"}
                  </pre>
                  <div className="mt-1.5 flex justify-end">
                    <button
                      className="cursor-pointer rounded border border-[var(--border)] bg-white px-2.5 py-1 text-[11.5px] text-[var(--accent)] hover:border-[var(--accent)]"
                      onClick={() => copyCard(c)}
                    >
                      复制全文
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
