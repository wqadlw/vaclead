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
} from "@ant-design/icons";
import { getToken, logout } from "@/lib/api";

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
      <div className="flex min-h-screen items-center justify-center">
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
          { key: "/tasks", icon: <RocketOutlined />, label: "拓客任务" },
          { key: "/settings", icon: <SettingOutlined />, label: "设置" },
        ]
      : []),
  ];

  return (
    <Layout className="min-h-screen">
      <Sider theme="dark" width={180}>
        <div className="px-4 py-4 text-white font-bold text-lg tracking-wide">vaclead</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[pathname]}
          items={items}
          onClick={({ key }) => router.push(key)}
        />
      </Sider>
      <Layout>
        <Header className="bg-white px-6 flex items-center justify-between"
          style={{ borderBottom: "1px solid #e8e8e8", height: 56, lineHeight: "56px" }}>
          <div className="text-gray-500 text-sm">真空设备智能拓客平台</div>
          <Dropdown
            menu={{
              items: [
                {
                  key: "logout",
                  icon: <LogoutOutlined />,
                  label: "退出登录",
                  onClick: logout,
                },
              ],
            }}
          >
            <span className="cursor-pointer flex items-center gap-2">
              <Avatar size="small" icon={<UserOutlined />} />
              {user}（{role === "admin" ? "管理员" : "销售"}）
            </span>
          </Dropdown>
        </Header>
        <Content className="p-6">{children}</Content>
      </Layout>
    </Layout>
  );
}
