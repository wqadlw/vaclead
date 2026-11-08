"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Avatar, Dropdown, Input, Modal, Toast } from "@douyinfe/semi-ui";
import {
  BarChart3,
  ClipboardList,
  Crosshair,
  Database,
  FileText,
  Gauge,
  Inbox,
  LogOut,
  Map,
  Megaphone,
  Menu,
  Package,
  PhoneCall,
  Radar,
  Rocket,
  Search,
  Send,
  Settings,
  Sprout,
  UserRound,
  X,
  Bell,
  Moon,
  Sun,
} from "lucide-react";
import BrandMark from "@/components/BrandMark";
import KeyboardShortcuts from "@/components/KeyboardShortcuts";
import { api, getToken, logout } from "@/lib/api";
import { useRouter as useNextRouter } from "next/navigation";
import { BRAND, PAGE_TITLES } from "@/lib/theme";

// SPEC-CG 导航分组：按"销售一天的使用顺序"组织——任务在先，情报在后。
// NAV_GROUPS 是唯一导航源：侧栏分组 / 底部导航 5 tab / 顶部组内导航条共用一份。
// 用户定稿（2026-11-09）：情报雷达 ∪ 触达 合并为一组（雷达发现信号 → 触达承接，同一作战链路）。
const NAV = [{ href: "/", label: "工作台", icon: Gauge }];
const DAILY_NAV = [
  { href: "/contacts", label: "联系人中心", icon: PhoneCall },
  { href: "/leads", label: "线索看板", icon: Radar },
  { href: "/quotes", label: "报价单", icon: FileText },
];
const FIND_NAV = [
  { href: "/signals", label: "标讯中心", icon: Megaphone },
  { href: "/enterprises", label: "企业列表", icon: Search },
  { href: "/map", label: "客户地图", icon: Map },
];
const RADAR_NAV = [
  { href: "/competitors", label: "竞对雷达", icon: Crosshair },
  { href: "/proposals", label: "拟建雷达", icon: Sprout },
  { href: "/outreach", label: "序列触达", icon: Send },
  { href: "/outreach/inbox", label: "收件箱", icon: Inbox },
];
const TOOL_NAV = [{ href: "/products", label: "产品目录", icon: Package }];

const NAV_GROUPS: {
  key: string;
  label: string;
  icon: typeof Gauge;
  defaultPath: string;
  items: typeof NAV;
}[] = [
  { key: "home", label: "工作台", icon: Gauge, defaultPath: "/", items: NAV },
  { key: "daily", label: "每日行动", icon: ClipboardList, defaultPath: "/contacts", items: DAILY_NAV },
  { key: "find", label: "找客户", icon: Search, defaultPath: "/signals", items: FIND_NAV },
  { key: "radar", label: "情报雷达", icon: Radar, defaultPath: "/competitors", items: RADAR_NAV },
  { key: "tool", label: "工具", icon: Package, defaultPath: "/products", items: TOOL_NAV },
];

/** 当前路由归属组判定（详情页 /enterprises/view 归属其前缀组） */
const matchNavItem = (href: string, path: string) =>
  href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);

const ADMIN_NAV = [
  { href: "/quality", label: "数据质量", icon: Database },
  { href: "/tasks", label: "拓客任务", icon: Rocket },
  { href: "/forms", label: "留资表单", icon: ClipboardList },
  { href: "/reports", label: "报表中心", icon: BarChart3 },
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
          <Dropdown.Item disabled>
            <span className="flex items-center justify-between">
              <span className="text-[11.5px] text-[var(--muted)]">未读 {unread}</span>
              <button
                className="border-0 bg-transparent p-0 text-[12px] text-[var(--accent)] cursor-pointer"
                onClick={(e) => {
                  e.stopPropagation();
                  window.location.href = "/notifications";
                }}
              >
                查看全部 →
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
      <span className="relative flex h-10 w-10 cursor-pointer select-none items-center justify-center rounded-md hover:bg-[rgba(19,20,22,0.04)] md:h-8 md:w-8">
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
      className="flex h-9 cursor-pointer items-center gap-1 rounded border border-[var(--border)] bg-white px-2 text-[11.5px] text-[var(--muted)] hover:border-[var(--accent)] md:h-7"
    >
      {isDark ? <Moon size={13} /> : <Sun size={13} />}
      {scheme === "System" ? "跟随" : scheme === "Dark" ? "暗" : "亮"}
    </button>
  );
}

/** Ctrl+K 全局命令面板（亮点 C）：页面跳转 + 企业即时搜索 */
interface PaletteEnt { id: number; name: string; industry_name: string | null; province: string | null; city: string | null; is_competitor: number }
interface PaletteContact { id: number; name: string; title: string | null; enterprise_id: number; enterprise_name: string }
interface PaletteSignal { id: number; title: string; type: string; ent_name: string }
interface PaletteResults { enterprises: PaletteEnt[]; contacts: PaletteContact[]; signals: PaletteSignal[] }

function CommandPalette() {
  const router = useNextRouter();
  const [visible, setVisible] = useState(false);
  const [q, setQ] = useState("");
  const [res, setRes] = useState<PaletteResults | null>(null);

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
      setRes(null);
      return;
    }
    const t = setTimeout(() => {
      api
        .get<PaletteResults>(`/api/search?q=${encodeURIComponent(kw)}`)
        .then(setRes)
        .catch(() => undefined);
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const pages = [
    { label: "工作台", path: "/" },
    { label: "企业列表", path: "/enterprises" },
    { label: "线索看板", path: "/leads" },
    { label: "序列触达", path: "/outreach" },
    { label: "收件箱", path: "/outreach/inbox" },
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
      title="全局搜索"
      visible={visible}
      onCancel={() => setVisible(false)}
      footer={null}
      closeOnEsc
      width={480}
      style={{ maxWidth: "calc(100vw - 24px)" }}
      bodyStyle={{ padding: "12px 16px" }}
    >
      <Input
        autoFocus
        value={q}
        onChange={setQ}
        placeholder="搜企业 / 联系人 / 标讯，或输入页面名…"
        size="large"
      />
      <div className="mt-3 max-h-[40vh] overflow-auto md:max-h-[320px]">
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
        {res && res.enterprises.length > 0 && (
          <div className="mb-2">
            <div className="px-1 pb-1 text-[11px] text-[var(--muted)]">企业</div>
            {res.enterprises.map((h) => (
              <button
                key={h.id}
                onClick={() => go(`/enterprises/view?id=${h.id}`)}
                className="block w-full cursor-pointer truncate rounded px-2 py-1.5 text-left text-[13px] text-[var(--ink)] hover:bg-[rgba(27,108,128,0.06)]"
                title={h.name}
              >
                {h.name}
                <span className="ml-1.5 text-[11px] text-[var(--muted)]">
                  {[h.industry_name, h.province && h.city ? `${h.province}${h.city}` : h.city].filter(Boolean).join(" · ")}
                </span>
                {h.is_competitor === 1 && <span className="ml-1 text-[11px] text-[var(--negative)]">同行</span>}
              </button>
            ))}
          </div>
        )}
        {res && res.contacts.length > 0 && (
          <div className="mb-2">
            <div className="px-1 pb-1 text-[11px] text-[var(--muted)]">联系人</div>
            {res.contacts.map((c) => (
              <button
                key={c.id}
                onClick={() => go(`/enterprises/view?id=${c.enterprise_id}`)}
                className="block w-full cursor-pointer truncate rounded px-2 py-1.5 text-left text-[13px] text-[var(--ink)] hover:bg-[rgba(27,108,128,0.06)]"
                title={c.name}
              >
                {c.name}
                <span className="ml-1.5 text-[11px] text-[var(--muted)]">{c.enterprise_name}</span>
              </button>
            ))}
          </div>
        )}
        {res && res.signals.length > 0 && (
          <div>
            <div className="px-1 pb-1 text-[11px] text-[var(--muted)]">标讯</div>
            {res.signals.map((s) => (
              <button
                key={s.id}
                onClick={() => go(`/signals?q=${encodeURIComponent(s.title.slice(0, 18))}`)}
                className="block w-full cursor-pointer truncate rounded px-2 py-1.5 text-left text-[13px] text-[var(--ink)] hover:bg-[rgba(27,108,128,0.06)]"
                title={s.title}
              >
                {s.title}
                <span className="ml-1.5 text-[11px] text-[var(--muted)]">{s.ent_name}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="mt-2 hidden text-[11px] text-[var(--muted)] md:block">Esc 关闭 · Enter 无默认行为，点选跳转</div>
    </Modal>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [role, setRole] = useState("sales");
  const [user, setUser] = useState("");
  // SPEC-CB 移动端抽屉
  const [mobileNav, setMobileNav] = useState(false);

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

  // SPEC-AI AI3：新信号轮询感知（5s 游标增量）——前台 Toast（节流 10s），
  // 页面隐藏时走 Web Notification（需一次授权，拒绝则仅 Toast）
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    let lastId = 0;
    let baseline = true;
    let lastToastAt = 0;
    const tick = async () => {
      try {
        const r = await fetch(`/api/signals?since_id=${lastId}&page_size=5`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) return;
        const d = await r.json();
        const items = d.items ?? [];
        if (baseline) {
          lastId = items[0]?.id ?? 0;
          baseline = false;
          return;
        }
        if (items.length === 0) return;
        lastId = Math.max(lastId, ...items.map((x: { id: number }) => x.id));
        const first = items[0];
        const now = Date.now();
        if (document.hidden) {
          if ("Notification" in window && Notification.permission === "granted") {
            new Notification("vaclead 新信号", {
              body: `${first.title ?? ""}`.slice(0, 90),
              tag: "vaclead-signal",
            });
          }
          return;
        }
        if (now - lastToastAt < 10000) return;
        lastToastAt = now;
        Toast.success({
          content: `新信号：${String(first.title ?? "").slice(0, 40)}`,
          duration: 4,
        });
      } catch {
        /* 网络抖动静默 */
      }
    };
    void tick();
    const timer = setInterval(tick, 5000);
    return () => clearInterval(timer);
  }, []);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--ground)]">
        <div className="text-[13px] text-[var(--muted)]">加载中…</div>
      </div>
    );
  }

  const navSections: { label: string | null; items: typeof NAV }[] = NAV_GROUPS.map(
    (g, gi) => ({ label: g.key === "home" ? null : g.label, items: g.items }),
  );
  if (role === "admin") navSections.push({ label: null, items: ADMIN_NAV });

  return (
    <div className="min-h-screen bg-[var(--ground)]">
      {/* 侧栏：浅色框架，与顶栏同底无界线（L 框架） */}
      {/* SPEC-CB：移动抽屉（<md 滑入），桌面常驻不变 */}
      {mobileNav && (
        <div
          className="fixed inset-0 z-30 bg-black/30 md:hidden"
          onClick={() => setMobileNav(false)}
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[228px] flex-col bg-[var(--ground)] transition-transform duration-200 ${
          mobileNav ? "translate-x-0" : "-translate-x-full"
        } md:translate-x-0`}
      >
        <div className="flex items-center gap-2.5 px-5 pt-5">
          <BrandMark size={30} className="shrink-0 text-[var(--ink)]" />
          <div className="leading-tight">
            <div className="text-[15px] font-semibold text-[var(--ink)]">{BRAND.name}</div>
            <div className="text-[11px] text-[var(--muted)]">{BRAND.subtitle}</div>
          </div>
        </div>

        <nav className="mt-6 flex-1 space-y-0.5 overflow-y-auto px-3 pb-2">
          {navSections.map((sec, si) => (
            <div key={sec.label ?? `sec-${si}`}>
              {sec.label && (
                <div className="px-3 pb-1 pt-4 text-[11px] font-medium tracking-[0.08em] text-[#9ca0a3]">
                  {sec.label}
                </div>
              )}
              {sec.items.map(({ href, label, icon: Icon }) => {
                const active = pathname === href;
                return (
                  <button
                    key={href}
                    data-nav={href}
                    onClick={() => { router.push(href); setMobileNav(false); }}
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
            </div>
          ))}
        </nav>

        {/* 主题切换移动端入口：顶栏不放（高德/企查查惯例），收进抽屉 */}
        <div className="flex items-center justify-between px-5 pb-3 md:hidden">
          <span className="text-[12.5px] text-[var(--muted)]">外观主题</span>
          <ThemeToggle />
        </div>
        <div className="px-5 pb-4 text-[11px] text-[#9ca0a3]">
          v0.3 · {role === "admin" ? "管理员" : "销售"}登录
        </div>
      </aside>

      {/* 主列：顶栏 + 白纸 */}
      <div className="flex min-h-screen flex-col md:ml-[228px]">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-1.5 border-b border-[var(--border)] bg-[var(--ground)] px-3 md:h-12 md:gap-3 md:border-b-0 md:px-6">
          {/* 移动端（<md）：☰ + 搜索条 + 右组——高德/企查查惯例，搜索条代替图标钮与页面标题 */}
          <button
            className="flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded border border-[var(--border)] bg-white text-[var(--muted)] md:hidden"
            title="菜单"
            aria-label="菜单"
            onClick={() => setMobileNav(true)}
          >
            <Menu size={17} />
          </button>
          <button
            onClick={() => {
              const ev = new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true });
              window.dispatchEvent(ev);
            }}
            title="全局搜索"
            aria-label="全局搜索"
            className="flex h-9 min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-full border border-[var(--border)] bg-white px-3.5 text-left text-[13px] text-[var(--muted)] md:hidden"
          >
            <Search size={15} className="shrink-0" />
            <span className="truncate">搜企业 / 联系人 / 标讯</span>
          </button>
          {/* 桌面（md+）：SPEC-CT CT2 文字搜索钮 + 快捷键 + 工作区 */}
          <div className="hidden items-center gap-3 md:flex">
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
          <div className="ml-auto flex shrink-0 items-center gap-1 md:gap-2">
            <div className="hidden md:block"><ThemeToggle /></div>
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
              <span className="flex cursor-pointer select-none items-center gap-2 rounded-md py-1 pl-1 pr-1 hover:bg-[rgba(19,20,22,0.04)] md:pr-2.5">
                <Avatar alt={user} size="extra-small" color="light-blue">
                  {role === "admin" ? <UserRound size={12} /> : user.slice(0, 1)}
                </Avatar>
                <span className="hidden text-[13px] text-[var(--ink)] md:inline">{user}</span>
              </span>
            </Dropdown>
          </div>
        </header>

        {/* 顶部组内导航条（<md）：当前底栏大区的二级页面切换（横滑 chips，md+ 由侧栏承担）
            单页组（工作台/工具）不渲染；管理组路由不归属五区、无条 */}
        {(() => {
          const group = NAV_GROUPS.find((g) =>
            g.items.some((it) => matchNavItem(it.href, pathname)),
          );
          if (!group || group.items.length <= 1) return null;
          return (
            <nav
              data-group-nav
              className="sticky top-14 z-10 flex flex-nowrap items-center gap-1.5 overflow-x-auto border-b border-[var(--border)] bg-[var(--ground)] px-3 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden md:hidden"
            >
              {group.items.map((it) => {
                const Icon = it.icon;
                const active = matchNavItem(it.href, pathname);
                return (
                  <button
                    key={it.href}
                    data-gnav={it.href}
                    onClick={() => router.push(it.href)}
                    className={`flex shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] ${
                      active
                        ? "border-[var(--accent)] bg-[var(--accent)] font-medium text-white"
                        : "border-[var(--border)] bg-white text-[var(--muted)]"
                    }`}
                  >
                    <Icon size={13} strokeWidth={active ? 2.2 : 1.8} />
                    {it.label}
                  </button>
                );
              })}
            </nav>
          );
        })()}

        {/* 白纸：距右/下边缘 8px，1px 边，16px 圆角，无阴影 */}
        <KeyboardShortcuts />
        <CommandPalette />
        <main className="mb-2 flex-1 pb-16 md:pb-0">
          <div className="min-h-[calc(100vh-56px-8px)] rounded-2xl border border-[var(--border)] bg-[var(--surface)] px-4 py-4 sm:px-6 sm:py-5 md:min-h-[calc(100vh-48px-8px)]">
            {children}
          </div>
        </main>
      </div>

      {/* 移动端底部导航（<md）——五大区一级 tab（用户定稿 2026-11-09）：
          工作台/每日行动/找客户/情报雷达/工具；点按进组默认页，组内页面由顶部导航条切换。
          管理组页面走顶栏 ☰ 抽屉。点按目标 ≥44px；z-20 低于抽屉背板 z-30 / 抽屉 z-40 */}
      <nav
        data-mobile-tabbar
        className="fixed inset-x-0 bottom-0 z-20 flex border-t border-[var(--border)] bg-[var(--surface)] md:hidden"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        {NAV_GROUPS.map((g) => {
          const Icon = g.icon;
          const active = g.items.some((it) => matchNavItem(it.href, pathname));
          return (
            <button
              key={g.key}
              data-btab={g.key}
              onClick={() => router.push(g.defaultPath)}
              className={`flex flex-1 cursor-pointer flex-col items-center justify-center gap-[3px] py-2 ${
                active ? "font-semibold text-[var(--accent)]" : "text-[var(--muted)]"
              }`}
            >
              <Icon size={18} strokeWidth={active ? 2.2 : 1.8} />
              <span className="text-[10.5px] leading-none">{g.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
