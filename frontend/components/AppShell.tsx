"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Avatar, Dropdown } from "@douyinfe/semi-ui";
import {
  Gauge,
  LogOut,
  Radar,
  Rocket,
  Search,
  Settings,
  UserRound,
} from "lucide-react";
import { getToken, logout } from "@/lib/api";
import { BRAND, PAGE_TITLES } from "@/lib/theme";

const NAV = [
  { href: "/", label: "工作台", icon: Gauge },
  { href: "/enterprises", label: "企业筛选", icon: Search },
  { href: "/leads", label: "线索看板", icon: Radar },
];

const ADMIN_NAV = [
  { href: "/tasks", label: "拓客任务", icon: Rocket },
  { href: "/settings", label: "设置", icon: Settings },
];

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
          <div className="text-[13px] text-[var(--muted)]">默认工作区 / 全国</div>
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
        </header>

        {/* 白纸：距右/下边缘 8px，1px 边，16px 圆角，无阴影 */}
        <main className="mb-2 flex-1">
          <div className="min-h-[calc(100vh-48px-8px)] rounded-2xl border border-[var(--border)] bg-[var(--surface)] px-6 py-5">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
