"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Avatar, Dropdown } from "@douyinfe/semi-ui";
import { motion } from "framer-motion";
import {
  LayoutDashboard,
  LogOut,
  Radar,
  Search,
  Settings,
  UserRound,
  Zap,
} from "lucide-react";
import { getToken, logout } from "@/lib/api";
import { BRAND, PAGE_TITLES } from "@/lib/theme";

const NAV = [
  { href: "/", label: "工作台", icon: LayoutDashboard },
  { href: "/enterprises", label: "企业筛选", icon: Search },
  { href: "/leads", label: "线索看板", icon: Radar },
];

const ADMIN_NAV = [
  { href: "/tasks", label: "拓客任务", icon: Zap },
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
      <div className="flex min-h-screen items-center justify-center bg-[#f3f4f9]">
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
          className="h-9 w-9 rounded-full border-[3px] border-indigo-200 border-t-indigo-600"
        />
      </div>
    );
  }

  const navItems = role === "admin" ? [...NAV, ...ADMIN_NAV] : NAV;

  return (
    <div className="min-h-screen">
      {/* 侧边栏 */}
      <aside className="fixed inset-y-0 left-0 z-30 w-[212px] flex flex-col bg-[#0e1230]">
        <div className="flex items-center gap-2.5 px-5 h-[60px] border-b border-white/5">
          <div className="w-8 h-8 rounded-[10px] bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-lg shadow-indigo-950/60">
            <Radar className="w-[17px] h-[17px] text-white" strokeWidth={2.4} />
          </div>
          <div className="leading-tight">
            <div className="text-white font-bold tracking-wide text-[15px]">{BRAND.name}</div>
            <div className="text-[9px] text-indigo-300/50 tracking-[0.13em]">{BRAND.tagline}</div>
          </div>
        </div>

        <nav className="flex-1 px-3 pt-4 space-y-1 overflow-y-auto">
          {navItems.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <button
                key={href}
                data-nav={href}
                onClick={() => router.push(href)}
                className={`relative w-full flex items-center gap-2.5 rounded-[10px] px-3.5 py-[9px] text-[13.5px] cursor-pointer transition-colors ${
                  active ? "text-white font-medium" : "text-indigo-200/60 hover:text-indigo-100 hover:bg-white/5"
                }`}
              >
                {active && (
                  <motion.span
                    layoutId="nav-pill"
                    className="absolute inset-0 rounded-[10px] bg-gradient-to-r from-indigo-600/90 to-violet-600/80 shadow-lg shadow-indigo-950/50"
                    transition={{ type: "spring", stiffness: 380, damping: 32 }}
                  />
                )}
                <Icon className="relative w-[16px] h-[16px]" strokeWidth={active ? 2.3 : 1.9} />
                <span className="relative">{label}</span>
              </button>
            );
          })}
        </nav>

        <div className="px-5 pb-5 text-[10px] text-indigo-300/30">v0.2 · Semi Design</div>
      </aside>

      {/* 主区域 */}
      <div className="ml-[212px] min-h-screen flex flex-col">
        <header className="sticky top-0 z-20 flex items-center justify-between px-7 h-[60px] bg-white/80 backdrop-blur-md border-b border-slate-200/70">
          <div className="flex items-baseline gap-3">
            <span className="text-[16px] font-semibold text-slate-800">{PAGE_TITLES[pathname] ?? ""}</span>
            <span className="text-[12px] text-slate-400 hidden sm:inline">{BRAND.subtitle}</span>
          </div>
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
            <span className="flex items-center gap-2.5 cursor-pointer select-none rounded-full pl-1 pr-3 py-1 hover:bg-slate-100 transition-colors">
              <Avatar alt={user} size="extra-small" color="indigo">
                {role === "admin" ? <UserRound size={13} /> : user.slice(0, 1)}
              </Avatar>
              <span className="text-[13px] text-slate-700">{user}</span>
            </span>
          </Dropdown>
        </header>

        <main className="flex-1 p-6 max-w-[1440px] w-full mx-auto">{children}</main>
      </div>
    </div>
  );
}
