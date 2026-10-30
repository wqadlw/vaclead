"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Avatar, Dropdown, Input, Modal } from "@douyinfe/semi-ui";
import {
  ClipboardList,
  FileText,
  Gauge,
  LogOut,
  Package,
  Radar,
  Rocket,
  Search,
  Settings,
  UserRound,
  Bell,
  Moon,
  Sun,
} from "lucide-react";
import KeyboardShortcuts from "@/components/KeyboardShortcuts";
import { api, getToken, logout } from "@/lib/api";
import { useRouter as useNextRouter } from "next/navigation";
import { BRAND, PAGE_TITLES } from "@/lib/theme";

const NAV = [
  { href: "/", label: "工作台", icon: Gauge },
  { href: "/enterprises", label: "企业筛选", icon: Search },
  { href: "/leads", label: "线索看板", icon: Radar },
  { href: "/quotes", label: "报价单", icon: FileText },
  { href: "/products", label: "产品目录", icon: Package },
];

const ADMIN_NAV = [
  { href: "/tasks", label: "拓客任务", icon: Rocket },
  { href: "/forms", label: "留资表单", icon: ClipboardList },
  { href: "/settings", label: "设置", icon: Settings },
];


function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<{ id: number; title: string; body: string; read: number }[]>([]);
  const [visible, setVisible] = useState(false);

  const load = useCallback(() => {
    api.get<{ unread_count: number; items: { id: number; title: string; body: string; read: number }[] }>(
      "/api/notifications?page_size=8",
    )
      .then((d) => {
        setUnread(d.unread_count);
        setItems(d.items);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000); // 每分钟轻量轮询
    return () => clearInterval(t);
  }, [load]);

  const markAll = async () => {
    await api.post("/api/notifications/read-all").catch(() => undefined);
    load();
  };

  const markOne = async (id: number) => {
    await api.post(`/api/notifications/${id}/read`).catch(() => undefined);
    load();
  };

  return (
    <Dropdown
      trigger="click"
      position="bottomRight"
      visible={visible}
      onVisibleChange={(v) => {
        setVisible(v);
        if (v) load();
      }}
      render={
        <Dropdown.Menu style={{ width: 340, maxHeight: 380, overflowY: "auto" }}>
          <Dropdown.Item disabled>
            <span className="flex items-center justify-between">
              <span className="text-[13px] font-medium">订阅动态</span>
              <button
                className="border-0 bg-transparent p-0 text-[12px] text-[var(--accent)] cursor-pointer"
                onClick={(e) => {
                  e.stopPropagation();
                  void markAll();
                }}
              >
                全部已读
              </button>
            </span>
          </Dropdown.Item>
          <Dropdown.Divider />
          {items.length === 0 && (
            <Dropdown.Item disabled>暂无动态 —— 在企业详情点「关注此企业」后，新中标/新公告会推到这里</Dropdown.Item>
          )}
          {items.map((n) => (
            <Dropdown.Item
              key={n.id}
              onClick={() => void markOne(n.id)}
              style={{ whiteSpace: "normal", padding: "8px 14px" }}
            >
              <div className={`text-[12.5px] leading-5 ${n.read ? "text-[var(--muted)]" : "font-medium text-[var(--ink)]"}`}>
                {n.title}
              </div>
              <div className="mt-0.5 line-clamp-2 text-[11.5px] leading-4 text-[var(--muted)]">{n.body}</div>
            </Dropdown.Item>
          ))}
        </Dropdown.Menu>
      }
    >
      <span className="relative flex cursor-pointer select-none items-center rounded-md p-2 hover:bg-[rgba(19,20,22,0.04)]">
        <Bell size={16} className="text-[var(--ink)]" />
        {unread > 0 && (
          <span className="absolute right-0.5 top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-[var(--negative)] px-0.5 text-[9px] leading-none text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </span>
    </Dropdown>
  );
}



/** 主题三态（吸收 twenty ColorScheme: Light|Dark|System + persistedColorScheme）*/
type ColorScheme = "Light" | "Dark" | "System";
const applyScheme = (scheme: ColorScheme) => {
  const isDark =
    scheme === "Dark" ||
    (scheme === "System" &&
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
  // Semi Design 暗色开关：变量作用域在 body（Semi 官方约定）
  document.body.setAttribute("theme-mode", isDark ? "dark" : "light");
  document.documentElement.setAttribute("theme-mode", isDark ? "dark" : "light");
};
function ThemeToggle() {
  const [scheme, setScheme] = useState<ColorScheme>(() => {
    if (typeof window === "undefined") return "Light";
    return (localStorage.getItem("vaclead_scheme") as ColorScheme) || "Light";
  });
  useEffect(() => {
    applyScheme(scheme);
    localStorage.setItem("vaclead_scheme", scheme);
    if (scheme === "System") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const onChange = () => applyScheme("System");
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    }
  }, [scheme]);
  const isDark =
    scheme === "Dark" ||
    (scheme === "System" &&
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  return (
    <button
      onClick={() => {
        const next: ColorScheme =
          scheme === "Light" ? "Dark" : scheme === "Dark" ? "System" : "Light";
        setScheme(next);
      }}
      title={`主题：${scheme}（点击切换 亮色→暗色→跟随系统）`}
      className="flex h-7 cursor-pointer items-center gap-1 rounded border border-[var(--border)] bg-white px-2 text-[11.5px] text-[var(--muted)] hover:border-[var(--accent)]"
    >
      {isDark ? <Moon size={13} /> : <Sun size={13} />}
      {scheme === "System" ? "跟随" : scheme === "Dark" ? "暗" : "亮"}
    </button>
  );
}

/** Ctrl+K 全局命令面板（亮点 C）：页面跳转 + 企业即时搜索 */
function CommandPalette() {
  const router = useNextRouter();
  const [visible, setVisible] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ id: number; name: string }[]>([]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setVisible(true);
      }
      if (e.key === "Escape") setVisible(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const kw = q.trim();
    if (!kw) {
      setHits([]);
      return;
    }
    const t = setTimeout(() => {
      api
        .get<{ items: { id: number; name: string }[] }>(
          `/api/enterprises?q=${encodeURIComponent(kw)}&page_size=6`,
        )
        .then((d) => setHits(d.items))
        .catch(() => undefined);
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const pages = [
    { label: "工作台", path: "/" },
    { label: "企业筛选", path: "/enterprises" },
    { label: "线索看板", path: "/leads" },
    { label: "拓客任务", path: "/tasks" },
    { label: "设置", path: "/settings" },
  ].filter((p) => !q || p.label.includes(q.trim()));

  const go = (path: string) => {
    setVisible(false);
    setQ("");
    router.push(path);
  };

  return (
    <Modal
      title="快速跳转 / 搜企业"
      visible={visible}
      onCancel={() => setVisible(false)}
      footer={null}
      closeOnEsc
      width={480}
      bodyStyle={{ padding: "12px 16px" }}
    >
      <Input
        autoFocus
        value={q}
        onChange={setQ}
        placeholder="输入页面名或企业关键词…"
        size="large"
      />
      <div className="mt-3 max-h-[320px] overflow-auto">
        {pages.length > 0 && (
          <div className="mb-2">
            <div className="px-1 pb-1 text-[11px] text-[var(--muted)]">页面</div>
            {pages.map((p) => (
              <button
                key={p.path}
                onClick={() => go(p.path)}
                className="block w-full cursor-pointer rounded px-2 py-1.5 text-left text-[13px] text-[var(--ink)] hover:bg-[rgba(27,108,128,0.06)]"
              >
                {p.label}
              </button>
            ))}
          </div>
        )}
        {hits.length > 0 && (
          <div>
            <div className="px-1 pb-1 text-[11px] text-[var(--muted)]">企业</div>
            {hits.map((h) => (
              <button
                key={h.id}
                onClick={() => go(`/enterprises?q=${encodeURIComponent(h.name)}`)}
                className="block w-full cursor-pointer truncate rounded px-2 py-1.5 text-left text-[13px] text-[var(--ink)] hover:bg-[rgba(27,108,128,0.06)]"
                title={h.name}
              >
                {h.name}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="mt-2 text-[11px] text-[var(--muted)]">Esc 关闭 · Enter 无默认行为，点选跳转</div>
    </Modal>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [role, setRole] = useState("sales");
  const [user, setUser] = useState("");

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    setRole(localStorage.getItem("vaclead_role") || "sales");
    setUser(localStorage.getItem("vaclead_user") || "");
    setReady(true);
  }, [router]);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--ground)]">
        <div className="text-[13px] text-[var(--muted)]">加载中…</div>
      </div>
    );
  }

  const navItems = role === "admin" ? [...NAV, ...ADMIN_NAV] : NAV;

  return (
    <div className="min-h-screen bg-[var(--ground)]">
      {/* 侧栏：浅色框架，与顶栏同底无界线（L 框架） */}
      <aside className="fixed inset-y-0 left-0 z-30 flex w-[228px] flex-col">
        <div className="flex items-center gap-2.5 px-5 pt-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[var(--accent)]">
            <Radar className="h-[16px] w-[16px] text-white" strokeWidth={2.2} />
          </div>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold text-[var(--ink)]">{BRAND.name}</div>
            <div className="text-[11px] text-[var(--muted)]">{BRAND.subtitle}</div>
          </div>
        </div>

        <nav className="mt-6 flex-1 space-y-0.5 px-3">
          {navItems.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <button
                key={href}
                data-nav={href}
                onClick={() => router.push(href)}
                className={`nav-item flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-3 py-[7px] text-left text-[13.5px] ${
                  active
                    ? "nav-pill font-medium"
                    : "text-[var(--muted)] hover:bg-[rgba(19,20,22,0.04)] hover:text-[var(--ink)]"
                }`}
              >
                <Icon className="h-4 w-4 shrink-0" strokeWidth={1.8} />
                {label}
              </button>
            );
          })}
        </nav>

        <div className="px-5 pb-4 text-[11px] text-[#9ca0a3]">
          v0.3 · {role === "admin" ? "管理员" : "销售"}登录
        </div>
      </aside>

      {/* 主列：顶栏 + 白纸 */}
      <div className="ml-[228px] flex min-h-screen flex-col">
        <header className="sticky top-0 z-20 flex h-12 items-center justify-between bg-[var(--ground)] px-6">
          <div className="flex items-center gap-3">
            <button
              onClick={() => {
                const ev = new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true });
                window.dispatchEvent(ev);
              }}
              className="flex h-7 cursor-pointer items-center gap-1.5 rounded border border-[var(--border)] bg-white px-2 text-[11.5px] text-[var(--muted)] hover:border-[var(--accent)]"
            >
              搜索 <kbd className="rounded bg-[var(--raised)] px-1 text-[10px]">Ctrl K</kbd>
            </button>
            <button
              onClick={() => {
                const ev = new KeyboardEvent("keydown", { key: "?", bubbles: true });
                window.dispatchEvent(ev);
              }}
              title="键盘快捷键（?）"
              className="flex h-7 w-7 cursor-pointer items-center justify-center rounded border border-[var(--border)] bg-white text-[11.5px] text-[var(--muted)] hover:border-[var(--accent)]"
            >
              ?
            </button>
            <span className="text-[13px] text-[var(--muted)]">默认工作区 / 全国</span>
          </div>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <NotificationBell />
            <Dropdown
            trigger="click"
            position="bottomRight"
            render={
              <Dropdown.Menu>
                <Dropdown.Item disabled>
                  {role === "admin" ? "管理员账号" : "销售账号"}
                </Dropdown.Item>
                <Dropdown.Divider />
                <Dropdown.Item icon={<LogOut size={14} />} onClick={logout}>
                  退出登录
                </Dropdown.Item>
              </Dropdown.Menu>
            }
          >
            <span className="flex cursor-pointer select-none items-center gap-2 rounded-md py-1 pl-1 pr-2.5 hover:bg-[rgba(19,20,22,0.04)]">
              <Avatar alt={user} size="extra-small" color="light-blue">
                {role === "admin" ? <UserRound size={12} /> : user.slice(0, 1)}
              </Avatar>
              <span className="text-[13px] text-[var(--ink)]">{user}</span>
            </span>
          </Dropdown>
          </div>
        </header>

        {/* 白纸：距右/下边缘 8px，1px 边，16px 圆角，无阴影 */}
        <KeyboardShortcuts />
        <CommandPalette />
        <main className="mb-2 flex-1">
          <div className="min-h-[calc(100vh-48px-8px)] rounded-2xl border border-[var(--border)] bg-[var(--surface)] px-6 py-5">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
