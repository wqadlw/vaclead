"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Form, Input, message } from "antd";
import { AimOutlined, SafetyOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { setAuth, api } from "@/lib/api";

const FEATURES = [
  { icon: <ThunderboltOutlined />, title: "招投标意图信号", desc: "设备词表实时匹配，谁在买设备先知道" },
  { icon: <AimOutlined />, title: "ICP 智能评分", desc: "半导体/光伏/锂电等六类下游客户自动分级" },
  { icon: <SafetyOutlined />, title: "撞单与公海机制", desc: "线索不撞单，30 天未跟进自动回收" },
];

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = await api.post<{ token: string; role: string; display_name: string }>(
        "/api/auth/login",
        values,
      );
      setAuth(res.token, res.role, res.display_name);
      router.push("/");
    } catch (e) {
      message.error(`登录失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex bg-white">
      {/* 左侧品牌区 */}
      <div className="hidden lg:flex w-[52%] flex-col justify-between p-12 relative overflow-hidden text-white bg-gradient-to-br from-[#1e1b4b] via-[#312e81] to-[#6d28d9]">
        <div
          className="absolute -top-32 -right-32 w-96 h-96 rounded-full opacity-20"
          style={{ background: "radial-gradient(circle,#a78bfa,transparent 70%)" }}
        />
        <div
          className="absolute bottom-0 left-0 w-[420px] h-[420px] rounded-full opacity-10 -translate-x-1/3 translate-y-1/3"
          style={{ background: "radial-gradient(circle,#818cf8,transparent 70%)" }}
        />

        <div className="relative flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-white/10 border border-white/20 flex items-center justify-center text-xl font-black tracking-tight backdrop-blur">
            V
          </div>
          <div>
            <div className="text-lg font-bold tracking-wide">vaclead</div>
            <div className="text-[11px] text-indigo-200/80 -mt-0.5">VACUUM SALES INTELLIGENCE</div>
          </div>
        </div>

        <div className="relative max-w-[460px]">
          <h1 className="text-[34px] leading-[1.3] font-bold">
            让真空设备销售
            <br />
            不再靠展会和名片
          </h1>
          <p className="mt-4 text-indigo-200/90 text-[15px] leading-7">
            招投标公告、环评公示、工商变更 ——
            采购意图藏在公开数据里。vaclead 帮你把它们变成一条条带着理由的客户线索。
          </p>
          <div className="mt-9 space-y-5">
            {FEATURES.map((f) => (
              <div key={f.title} className="flex items-start gap-3.5">
                <div className="w-9 h-9 rounded-lg bg-white/10 border border-white/15 flex items-center justify-center text-[15px] shrink-0 backdrop-blur">
                  {f.icon}
                </div>
                <div>
                  <div className="text-[14px] font-semibold">{f.title}</div>
                  <div className="text-[12.5px] text-indigo-200/75 mt-0.5">{f.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative text-[12px] text-indigo-200/60">
          © 2026 vaclead · 自建垂直拓客平台 · 数据来自公开公示渠道
        </div>
      </div>

      {/* 右侧登录区 */}
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-[368px]">
          <div className="lg:hidden flex items-center gap-2.5 mb-8">
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-600 to-violet-600 text-white flex items-center justify-center font-black">
              V
            </div>
            <span className="text-lg font-bold text-slate-800">vaclead</span>
          </div>

          <h2 className="text-[22px] font-bold text-slate-900">欢迎回来</h2>
          <p className="text-slate-400 text-[13px] mt-1.5 mb-8">登录后开始今天的拓客</p>

          <Form layout="vertical" onFinish={onFinish} initialValues={{ username: "admin" }} size="large" requiredMark={false}>
            <Form.Item name="username" label={<span className="text-slate-600">用户名</span>} rules={[{ required: true, message: "请输入用户名" }]}>
              <Input placeholder="admin / sales" autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label={<span className="text-slate-600">密码</span>} rules={[{ required: true, message: "请输入密码" }]}>
              <Input.Password placeholder="••••••••" autoComplete="current-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading} className="!h-11 !mt-2">
              登 录
            </Button>
          </Form>

          <div className="mt-8 rounded-xl bg-slate-50 border border-slate-100 px-4 py-3 text-[12.5px] text-slate-500 leading-6">
            <span className="font-medium text-slate-600">演示账号</span>
            <br />
            管理员 admin / admin123 · 销售 sales / sales123
          </div>
        </div>
      </div>
    </div>
  );
}
