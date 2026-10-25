"use client";

/**
 * 键盘快捷键体系（吸收 twenty keyboard-shortcut-menu + useGoToHotkeys 序列热键）。
 * - ? 弹出快捷键帮助菜单
 * - g 后接 1-5：两段式导航（工作台/企业/线索/任务/设置）
 * - / 聚焦命令面板（打开 Ctrl+K）
 * - t 切换主题
 */
import { useEffect, useState } from "react";
import { Modal } from "@douyinfe/semi-ui";

const GO_TARGETS: { key: string; label: string; path: string }[] = [
  { key: "1", label: "工作台", path: "/" },
  { key: "2", label: "企业筛选", path: "/enterprises" },
  { key: "3", label: "商机管线", path: "/leads" },
  { key: "4", label: "拓客任务", path: "/tasks" },
  { key: "5", label: "设置", path: "/settings" },
];

export const SHORTCUT_GROUPS: { group: string; items: { keys: string; label: string }[] }[] = [
  {
    group: "导航（先按 g 再按数字）",
    items: GO_TARGETS.map((t) => ({ keys: `g ${t.key}`, label: `前往${t.label}` })),
  },
  {
    group: "通用",
    items: [
      { keys: "?", label: "快捷键帮助" },
      { keys: "/", label: "搜索（命令面板）" },
      { keys: "t", label: "切换亮/暗主题" },
    ],
  },
];

export default function KeyboardShortcuts() {
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => {
    let pendingG = false;
    let gTimer: ReturnType<typeof setTimeout> | null = null;

    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "?" && !inField) {
        e.preventDefault();
        setHelpOpen((v) => !v);
        return;
      }
      if (inField) return;
      // g 序列：1s 内接数字导航（twenty useGlobalHotkeysSequence 模式）
      if (pendingG) {
        const hit = GO_TARGETS.find((t) => t.key === e.key);
        pendingG = false;
        if (gTimer) clearTimeout(gTimer);
        if (hit) {
          e.preventDefault();
          window.location.assign(`#${hit.path}`);
          window.location.hash = "";
          // Next.js App Router 客户端路由
          history.pushState(null, "", hit.path);
          window.dispatchEvent(new PopStateEvent("popstate"));
          return;
        }
      }
      if (e.key === "g") {
        pendingG = true;
        if (gTimer) clearTimeout(gTimer);
        gTimer = setTimeout(() => {
          pendingG = false;
        }, 1000);
        return;
      }
      if (e.key === "t") {
        e.preventDefault();
        const btns = Array.from(document.querySelectorAll("button") as unknown as HTMLButtonElement[]);
        const themeBtn = btns.find((b) => b.title?.includes("主题"));
        themeBtn?.click();
        return;
      }
      if (e.key === "/") {
        e.preventDefault();
        const btns = Array.from(document.querySelectorAll("button") as unknown as HTMLButtonElement[]);
        const search = btns.find((b) => b.textContent?.includes("搜索") && b.textContent?.includes("Ctrl"));
        search?.click();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <Modal
      title="键盘快捷键"
      visible={helpOpen}
      onCancel={() => setHelpOpen(false)}
      footer={null}
      width={440}
    >
      <div className="space-y-4 pt-1 pb-2">
        {SHORTCUT_GROUPS.map((g) => (
          <div key={g.group}>
            <div className="mb-1.5 text-[11.5px] text-[var(--muted)]">{g.group}</div>
            <div className="space-y-1">
              {g.items.map((it) => (
                <div key={it.keys} className="flex items-center justify-between px-1 py-0.5">
                  <span className="text-[13px] text-[var(--ink)]">{it.label}</span>
                  <kbd className="rounded bg-[var(--raised)] px-1.5 py-0.5 text-[11px] tabular-nums text-[var(--muted)]">
                    {it.keys}
                  </kbd>
                </div>
              ))}
            </div>
          </div>
        ))}
        <div className="text-[11px] text-[var(--muted)]">
          顺序快捷键参考 twenty GoToHotkeys：先按 g（1 秒内）再按目标数字。
        </div>
      </div>
    </Modal>
  );
}
