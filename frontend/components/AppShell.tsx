"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Avatar, Dropdown, Layout, Menu, Spin } from "antd";
import {
  DashboardOutlined,
  LogoutOutlined,
  SearchOutlined,
  SolutionOutlined,
  RocketOutlined,
  SettingOutlined,
  UserOutlined,
  CrownFilled,
} from "@ant-design/icons";
import { getToken, logout } from "@/lib/api";
import { PAGE_TITLES } from "@/lib/theme";

const { Sider, Header, Content } = Layout;

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
      <div className="flex min-h-screen items-center justify-center bg-[#f4f5fa]">
        <Spin size="large" />
      </div>
    );
  }

  const items = [
    { key: "/", icon: <DashboardOutlined />, label: "工作台" },
    { key: "/enterprises", icon: <SearchOutlined />, label: "企业筛选" },
    { key: "/leads", icon: <SolutionOutlined />, label: "线索看板" },
    ...(role === "admin"
      ? [
          { type: "group" as const, label: "数据运营", children: [
            { key: "/tasks", icon: <RocketOutlined />, label: "拓客任务" },
            { key: "/settings", icon: <SettingOutlined />, label: "设置" },
          ] },
        ]
      : []),
  ];

  return (
    <Layout className="min-h-screen">
      <Sider width={208} className="!fixed !h-full z-10" style={{ background: "#111633" }}>
        <div className="flex items-center gap-2.5 px-5 h-16 border-b border-white/5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white font-black text-[15px] shadow-lg shadow-indigo-900/50">
            V
          </div>
          <div className="leading-tight">
            <div className="text-white font-bold tracking-wide text-[15px]">vaclead</div>
            <div className="text-[9.5px] text-indigo-300/60 tracking-[0.14em]">VACUUM INTELLIGENCE</div>
          </div>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          className="!border-r-0 !bg-transparent !pt-3 [&_.ant-menu-item]:!mx-3 [&_.ant-menu-item]:!rounded-lg [&_.ant-menu-item-group-title]:!px-5 !text-[13.5px]"
          style={{ background: "transparent" }}
          selectedKeys={[pathname]}
          items={items}
          onClick={({ key }) => router.push(key)}
        />
        <div className="absolute bottom-5 left-0 right-0 px-5 text-[10.5px] text-indigo-300/40">
          v0.1 · 内部版
        </div>
      </Sider>

      <Layout style={{ marginLeft: 208, background: "#f4f5fa" }}>
        <Header
          className="sticky top-0 z-20 flex items-center justify-between px-6"
          style={{ background: "rgba(255,255,255,.85)", backdropFilter: "blur(8px)", borderBottom: "1px solid #eceef3", height: 60 }}
        >
          <div className="flex items-baseline gap-3">
            <span className="text-[16px] font-semibold text-slate-800">
              {PAGE_TITLES[pathname] ?? ""}
            </span>
            <span className="text-[12px] text-slate-400 hidden sm:inline">真空设备智能拓客平台</span>
          </div>
          <Dropdown
            menu={{
              items: [
                { key: "role", label: `${role === "admin" ? "管理员" : "销售"}账号`, disabled: true },
                { type: "divider" },
                { key: "logout", icon: <LogoutOutlined />, label: "退出登录", onClick: logout },
              ],
            }}
          >
            <span className="cursor-pointer flex items-center gap-2.5 pl-1 rounded-full hover:bg-slate-50 pr-3 py-1 transition-colors">
              <Avatar
                size={30}
                className="!bg-gradient-to-br !from-indigo-500 !to-violet-600"
                icon={role === "admin" ? <CrownFilled className="text-[13px]" /> : <UserOutlined />}
              />
              <span className="text-[13.5px] text-slate-700">{user}</span>
            </span>
          </Dropdown>
        </Header>
        <Content className="p-6 max-w-[1440px]">{children}</Content>
      </Layout>
    </Layout>
  );
}
