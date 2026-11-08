"use client";

/**
 * 登录页（大厂级重排，SPEC-CN）：Linear 学派双栏——
 * 左：墨色品牌场（眉题/大标题/图标化卖点/信任条），右：主题自适应表单。
 * 扁平无渐变无阴影；品牌六角涡；三态主题登录页可切换。
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Form, Toast } from "@douyinfe/semi-ui";
import { Gauge, Megaphone, Moon, Repeat, Sun } from "lucide-react";
import BrandMark from "@/components/BrandMark";
import { setAuth, api } from "@/lib/api";
import { BRAND } from "@/lib/theme";

type ColorScheme = "Light" | "Dark" | "System";

function applyScheme(scheme: ColorScheme) {
  const isDark =
    scheme === "Dark" ||
    (scheme === "System" &&
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
  document.body.setAttribute("theme-mode", isDark ? "dark" : "light");
  document.documentElement.setAttribute("theme-mode", isDark ? "dark" : "light");
}

const FEATURES = [
  {
    icon: Megaphone,
    name: "招投标意图信号",
    desc: "四省备案+政采双主干实时采集，谁在买设备先知道",
  },
  {
    icon: Gauge,
    name: "可解释评分",
    desc: "ICP 四段画像+意图半衰，每条推荐都带人话理由",
  },
  {
    icon: Repeat,
    name: "线索闭环",
    desc: "通话驾驶舱、跟进时间线、撞单保护、30 天公海回收",
  },
];

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [scheme, setScheme] = useState<ColorScheme>("Light");

  useEffect(() => {
    const saved = (localStorage.getItem("vaclead_scheme") as ColorScheme) || "Light";
    setScheme(saved);
    applyScheme(saved);
  }, []);

  const toggleScheme = () => {
    const next: ColorScheme = scheme === "Dark" ? "Light" : "Dark";
    setScheme(next);
    localStorage.setItem("vaclead_scheme", next);
    applyScheme(next);
  };

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
    <div className="flex min-h-screen bg-[var(--ground)]">
      {/* 左：墨色品牌场（扁平色场+微点阵，无渐变） */}
      <div
        className="animate-fadein relative hidden w-[46%] flex-col justify-between overflow-hidden p-12 text-white lg:flex"
        style={{
          background: "#131416",
          backgroundImage:
            "radial-gradient(circle, rgba(247,248,248,0.045) 1px, transparent 1px)",
          backgroundSize: "22px 22px",
        }}
      >
        <div className="relative flex items-center gap-3">
          <BrandMark size={34} hexColor="#f7f8f8" vortexColor="#7b86e8" />
          <div>
            <div className="text-[16px] font-semibold tracking-[-0.018em]">{BRAND.name}</div>
            <div className="mt-0.5 text-[10px] uppercase tracking-[0.16em] text-white/40">
              {BRAND.tagline}
            </div>
          </div>
        </div>

        <div className="relative max-w-[460px]">
          <h1 className="text-[30px] font-semibold leading-[1.32] tracking-[-0.02em]">
            工业设备的采购意图
            <br />
            藏在公开数据里
          </h1>
          <p className="mt-4 text-[14px] leading-7 text-white/60">
            {BRAND.name} 把招投标公告、项目备案变成一条条带着理由、
            带着电话的销售线索。
          </p>
          <div className="mt-9">
            {FEATURES.map((f, i) => (
              <div
                key={f.name}
                className={`flex items-start gap-3.5 py-4 ${i > 0 ? "border-t border-white/10" : ""}`}
              >
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/15">
                  <f.icon size={13} className="text-[#7b86e8]" strokeWidth={2} />
                </span>
                <div>
                  <div className="text-[13.5px] font-medium text-white/90">{f.name}</div>
                  <div className="mt-0.5 text-[12.5px] leading-5 text-white/50">{f.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative">
          <div className="mb-3 flex items-center gap-2 text-[11px] tracking-wide text-white/35">
            <span>四省备案通道</span>
            <span className="text-white/20">·</span>
            <span>官方公示数据</span>
            <span className="text-white/20">·</span>
            <span>全程证据可溯源</span>
          </div>
          <div className="text-[11.5px] text-white/30">© 2026 {BRAND.name}</div>
        </div>
      </div>

      {/* 右：登录表单（主题自适应） */}
      <div className="relative flex flex-1 items-center justify-center px-6">
        <button
          onClick={toggleScheme}
          className="absolute right-5 top-5 flex h-8 w-8 cursor-pointer items-center justify-center rounded-md border border-[var(--border)] bg-[var(--surface)] text-[var(--muted)] transition-colors hover:text-[var(--ink)]"
          title={scheme === "Dark" ? "切亮色" : "切暗色"}
        >
          {scheme === "Dark" ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        {/* 移动端品牌行（左栏 lg 以下隐藏） */}
        <div className="absolute left-5 top-5 flex items-center gap-2 lg:hidden">
          <BrandMark size={26} className="text-[var(--ink)]" />
          <span className="text-[14px] font-semibold tracking-[-0.018em] text-[var(--ink)]">
            {BRAND.name}
          </span>
        </div>

        <div className="w-full max-w-[340px] animate-riseup pt-10 lg:pt-0">
          <div className="mb-1.5 text-[11px] uppercase tracking-[0.16em] text-[var(--muted)]">
            团队工作台
          </div>
          <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-[var(--ink)]">
            登录 {BRAND.name}
          </h2>
          <p className="mb-7 mt-1.5 text-[13px] text-[var(--muted)]">
            用团队账号进入，先看今日行动，再打今日推荐。
          </p>

          <Form onSubmit={handleSubmit} labelPosition="top">
            <Form.Input
              field="username"
              label="用户名"
              initValue="admin"
              autoFocus
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

          <div className="mt-7 rounded-lg border border-[var(--border)] bg-[var(--raised)] px-4 py-3 text-[12px] leading-6 text-[var(--muted)]">
            演示账号 —— 管理员 <span className="font-medium text-[var(--ink)]">admin / admin123</span>
            <br />
            销售 <span className="font-medium text-[var(--ink)]">sales / sales123</span>
            <span className="ml-1 text-[var(--muted)]">· 忘记密码联系管理员重置</span>
          </div>

          <div className="mt-6 text-center text-[11px] text-[var(--muted)] lg:hidden">
            数据来自公开公示渠道 · 全程证据可溯源
          </div>
        </div>
      </div>
    </div>
  );
}
