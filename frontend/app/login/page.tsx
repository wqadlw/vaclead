"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Form, Input, Toast } from "@douyinfe/semi-ui";
import { Radar, Target, ShieldCheck, Zap } from "lucide-react";
import { setAuth, api } from "@/lib/api";
import { BRAND } from "@/lib/theme";

const FEATURES = [
  { icon: Zap, title: "招投标意图信号", desc: "设备词表实时匹配，谁在买设备先知道" },
  { icon: Target, title: "ICP 智能评分", desc: "半导体/光伏/锂电等六类下游客户自动分级" },
  { icon: ShieldCheck, title: "撞单与公海机制", desc: "线索不撞单，30 天未跟进自动回收" },
];

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = await api.post<{ token: string; role: string; display_name: string }>(
        "/api/auth/login",
        values,
      );
      setAuth(res.token, res.role, res.display_name);
      router.push("/");
    } catch (e) {
      Toast.error(`登录失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex bg-white">
      {/* 左侧品牌区（纯 CSS 动画，无水合依赖） */}
      <div
        className="hidden lg:flex w-[52%] flex-col justify-between p-12 relative overflow-hidden text-white animate-fadein"
        style={{ background: "linear-gradient(135deg,#1e1b4b 0%,#312e81 45%,#6d28d9 100%)" }}
      >
        <div
          className="absolute -top-32 -right-32 w-96 h-96 rounded-full opacity-25 pointer-events-none"
          style={{ background: "radial-gradient(circle,#a78bfa,transparent 70%)" }}
        />
        <div
          className="absolute bottom-10 left-0 w-[420px] h-[420px] rounded-full opacity-10 pointer-events-none"
          style={{ background: "radial-gradient(circle,#818cf8,transparent 70%)" }}
        />

        <div className="relative flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-white/10 border border-white/20 flex items-center justify-center backdrop-blur">
            <Radar className="w-5 h-5" strokeWidth={2.2} />
          </div>
          <div>
            <div className="text-lg font-bold tracking-wide">{BRAND.name}</div>
            <div className="text-[10px] text-indigo-200/70 tracking-[0.15em]">{BRAND.tagline}</div>
          </div>
        </div>

        <div className="relative max-w-[470px]">
          <h1 className="text-[34px] leading-[1.32] font-bold animate-riseup">
            让真空设备销售
            <br />
            不再靠展会和名片
          </h1>
          <p className="mt-4 text-indigo-200/85 text-[14.5px] leading-7 animate-riseup" style={{ animationDelay: "0.15s" }}>
            招投标公告、环评公示、工商变更 —— 采购意图藏在公开数据里。
            {BRAND.name} 帮你把它们变成一条条带着理由的客户线索。
          </p>
          <div className="mt-9 space-y-5">
            {FEATURES.map((f, i) => (
              <div
                key={f.title}
                className="flex items-start gap-3.5 animate-riseup"
                style={{ animationDelay: `${0.3 + i * 0.13}s` }}
              >
                <div className="w-9 h-9 rounded-[10px] bg-white/10 border border-white/15 flex items-center justify-center shrink-0 backdrop-blur">
                  <f.icon className="w-4 h-4" strokeWidth={2} />
                </div>
                <div>
                  <div className="text-[14px] font-semibold">{f.title}</div>
                  <div className="text-[12.5px] text-indigo-200/70 mt-0.5">{f.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative text-[11.5px] text-indigo-200/50">
          © 2026 {BRAND.name} · 自建垂直拓客平台 · 数据来自公开公示渠道
        </div>
      </div>

      {/* 右侧登录区 */}
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-[368px] animate-riseup">
          <div className="lg:hidden flex items-center gap-2.5 mb-8">
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-600 to-violet-600 text-white flex items-center justify-center">
              <Radar className="text-white" size={17} />
            </div>
            <span className="text-lg font-bold text-slate-800">{BRAND.name}</span>
          </div>

          <h2 className="text-[22px] font-bold text-slate-900">欢迎回来</h2>
          <p className="text-slate-400 text-[13px] mt-1.5 mb-8">登录后开始今天的拓客</p>

          <Form onSubmit={handleSubmit} labelPosition="top">
            <Form.Input
              field="username"
              label="用户名"
              initValue="admin"
              rules={[{ required: true, message: "请输入用户名" }]}
              placeholder="admin / sales"
            />
            <Form.Input
              field="password"
              mode="password"
              label="密码"
              rules={[{ required: true, message: "请输入密码" }]}
              placeholder="请输入密码"
            />
            <Button
              htmlType="submit"
              theme="solid"
              block
              loading={loading}
              className="!mt-2 !h-11 !bg-gradient-to-r !from-indigo-600 !to-violet-600 hover:!from-indigo-500 hover:!to-violet-500 !rounded-[10px] !font-medium"
            >
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
