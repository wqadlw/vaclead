"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Form, Toast } from "@douyinfe/semi-ui";
import { Radar } from "lucide-react";
import { setAuth, api } from "@/lib/api";
import { BRAND } from "@/lib/theme";

const FEATURES = [
  { name: "招投标意图信号", desc: "设备词表实时匹配公开公告，谁在买设备先知道" },
  { name: "ICP 智能评分", desc: "六类下游客户画像自动打分分级，每条线索带理由" },
  { name: "线索闭环", desc: "跟进时间线、撞单保护、30 天公海回收" },
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
    <div className="flex min-h-screen bg-white">
      {/* 左：墨色整面（扁平色场，无渐变） */}
      <div className="animate-fadein relative hidden w-[46%] flex-col justify-between overflow-hidden p-12 text-white lg:flex" style={{ background: "#131416" }}>
        <div className="relative flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-[var(--accent)]">
            <Radar className="h-[17px] w-[17px]" strokeWidth={2.2} />
          </div>
          <div>
            <div className="text-[16px] font-semibold">{BRAND.name}</div>
            <div className="text-[11px] text-white/50">{BRAND.subtitle}</div>
          </div>
        </div>

        <div className="relative max-w-[440px]">
          <h1 className="text-[32px] font-semibold leading-[1.3]">
            工业设备的采购意图
            <br />
            藏在公开数据里
          </h1>
          <p className="mt-4 text-[14.5px] leading-7 text-white/60">
            {BRAND.name} 把招投标公告、企业档案变成一条条带着理由的销售线索。
          </p>
          <div className="mt-10 space-y-6">
            {FEATURES.map((f) => (
              <div key={f.name}>
                <div className="text-[14px] font-medium text-[var(--accent)] [color:#5fb3c6]">{f.name}</div>
                <div className="mt-1 text-[13px] leading-6 text-white/55">{f.desc}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative text-[11.5px] text-white/35">
          © 2026 {BRAND.name} · 数据来自公开公示渠道
        </div>
      </div>

      {/* 右：登录表单 */}
      <div className="flex flex-1 items-center justify-center px-6">
        <div className="w-full max-w-[340px] animate-riseup">
          <h2 className="text-[22px] font-semibold text-[var(--ink)]">登录 {BRAND.name}</h2>
          <p className="mb-8 mt-1.5 text-[13px] text-[var(--muted)]">销售工作台 · 请使用团队账号</p>

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
            <Button htmlType="submit" theme="solid" block loading={loading} className="!mt-2 !h-10">
              登 录
            </Button>
          </Form>

          <div className="mt-8 rounded-lg bg-[var(--raised)] px-4 py-3 text-[12.5px] leading-6 text-[var(--muted)]">
            演示账号 —— 管理员 admin / admin123 · 销售 sales / sales123
          </div>
        </div>
      </div>
    </div>
  );
}
