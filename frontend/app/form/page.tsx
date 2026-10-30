"use client";

/**
 * 公开留资落地页（SPEC-J J5）：无需登录，?t=<token>。
 * honeypot 字段对人类不可见；提交走 /api/public/lead-capture。
 * 独立页（不套 AppShell）——静态导出必须查询参数 + Suspense。
 */
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Button, Input, Toast } from "@douyinfe/semi-ui";
import { api } from "@/lib/api";

const inputCls =
  "w-full rounded-md border border-[var(--border,#e2e8f0)] bg-white px-3 py-2 text-[14px] outline-none focus:border-[#1b6c80]";

function CaptureForm() {
  const sp = useSearchParams();
  const token = sp.get("t") ?? "";
  const [company, setCompany] = useState("");
  const [contactName, setContactName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [need, setNeed] = useState("");
  const [website, setWebsite] = useState(""); // honeypot：人类不可见不可填
  const [done, setDone] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!company.trim() || (!phone.trim() && !email.trim())) {
      Toast.warning("请填写公司名，并留下电话或邮箱");
      return;
    }
    setSubmitting(true);
    try {
      await api.post(`/api/public/lead-capture/${token}`, {
        company: company.trim(), contact_name: contactName.trim() || null,
        phone: phone.trim() || null, email: email.trim() || null,
        need: need.trim() || null, website,
      });
      setDone(true);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "提交失败，请稍后再试");
    } finally {
      setSubmitting(false);
    }
  };

  if (!token) {
    return (
      <div className="p-10 text-center text-[14px] text-slate-500">链接无效：缺少表单标识。</div>
    );
  }
  if (done) {
    return (
      <div className="p-12 text-center">
        <div className="text-[40px]">✓</div>
        <h2 className="mt-2 text-[18px] font-medium text-[#1b2a33]">提交成功</h2>
        <p className="mt-2 text-[14px] text-slate-500">我们已收到您的需求，会尽快与您联系。</p>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <Input placeholder="公司名称 *" value={company} onChange={setCompany} />
      <Input placeholder="联系人" value={contactName} onChange={setContactName} />
      <Input placeholder="电话 *" value={phone} onChange={setPhone} />
      <Input placeholder="邮箱" value={email} onChange={setEmail} />
      <Input placeholder="想了解的设备或需求（选填）" value={need} onChange={setNeed} />
      {/* honeypot：人类不可见（移出屏幕），机器人会填 */}
      <div aria-hidden className="absolute -left-[9999px] top-auto h-px w-px overflow-hidden">
        <input tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
      </div>
      <Button
        theme="solid"
        block
        loading={submitting}
        onClick={submit}
        className="!bg-[#1b2a33]"
      >
        提交需求
      </Button>
      <p className="text-center text-[12px] text-slate-400">
        提交即表示同意我们通过所留方式与您联系；信息仅用于采购咨询。
      </p>
    </div>
  );
}

export default function PublicFormPage() {
  return (
    <main className="min-h-screen bg-[#f6f8f9] py-14">
      <Suspense fallback={null}>
        <div className="mx-auto max-w-[440px] px-6">
          <div className="rounded-xl border border-[#e2e8f0] bg-white p-8">
            <div className="mb-6 text-center">
              <div className="text-[13px] font-semibold tracking-[0.2em] text-[#1b6c80]">VACLEAD</div>
              <h1 className="mt-1 text-[20px] font-medium text-[#1b2a33]">真空设备采购咨询</h1>
              <p className="mt-1 text-[13px] text-slate-500">留下需求，我们为您匹配方案与报价</p>
            </div>
            <CaptureForm />
          </div>
        </div>
      </Suspense>
    </main>
  );
}
