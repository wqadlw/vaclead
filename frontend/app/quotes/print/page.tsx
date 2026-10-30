"use client";

/**
 * 报价单打印视图（SPEC-J J3）：纯 HTML 免 PDF 依赖。
 * 独立页（不套 AppShell），window.print() 出报价单。
 */
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Skeleton } from "@douyinfe/semi-ui";
import { api } from "@/lib/api";

interface QuoteItem { id: number; name: string; spec?: string | null; qty: number; price: number; price_unit: string; subtotal: number }
interface QuoteDetail {
  id: number;
  status: string;
  valid_until: string | null;
  total_wan: number;
  remark: string | null;
  enterprise_name: string | null;
  items: QuoteItem[];
}
const STATUS_CN: Record<string, string> = { draft: "草稿", sent: "已发送", won: "已成交", lost: "未成交" };

function PrintBody() {
  const sp = useSearchParams();
  const qid = sp.get("id");
  const [data, setData] = useState<QuoteDetail | null>(null);

  useEffect(() => {
    if (qid) api.get<QuoteDetail>(`/api/quotes/${qid}`).then(setData).catch(() => undefined);
  }, [qid]);

  if (!data) {
    return <Skeleton placeholder={<Skeleton.Paragraph rows={6} />} loading active />;
  }
  return (
    <div className="mx-auto max-w-[760px] p-10 print:p-6">
      <div className="mb-1 flex items-baseline justify-between border-b-2 border-[#1b6c80] pb-3">
        <h1 className="text-[22px] font-semibold text-[#1b6c80]">报价单</h1>
        <span className="text-[13px] text-slate-500">编号 QT-{String(data.id).padStart(5, "0")}</span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 text-[14px]">
        <div><span className="text-slate-500">客户：</span><span className="font-medium">{data.enterprise_name ?? "—"}</span></div>
        <div className="text-right"><span className="text-slate-500">状态：</span>{STATUS_CN[data.status] ?? data.status}</div>
        <div><span className="text-slate-500">有效期至：</span>{data.valid_until ?? "—"}</div>
        <div className="text-right text-slate-400">由 vaclead 生成</div>
      </div>
      <table className="mt-6 w-full border-collapse text-[13.5px]">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">项目</th>
            <th className="py-2">参数</th>
            <th className="py-2 text-right">数量</th>
            <th className="py-2 text-right">单价(万)</th>
            <th className="py-2 text-right">小计(万)</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((i) => (
            <tr key={i.id} className="border-b border-slate-100">
              <td className="py-2 font-medium">{i.name}</td>
              <td className="py-2 text-slate-500">{i.spec ?? "—"}</td>
              <td className="py-2 text-right tabular-nums">{i.qty} {i.price_unit}</td>
              <td className="py-2 text-right tabular-nums">{i.price.toLocaleString()}</td>
              <td className="py-2 text-right tabular-nums">{i.subtotal.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-4 text-right text-[18px] font-semibold tabular-nums text-[#1b6c80]">
        合计：{data.total_wan.toLocaleString()} 万元
      </div>
      {data.remark && <div className="mt-4 text-[13px] text-slate-500">备注：{data.remark}</div>}
      <div className="mt-8 hidden print:block">
        <div className="flex justify-between text-[13px] text-slate-500">
          <span>供方签字：＿＿＿＿＿＿＿</span>
          <span>需方签字：＿＿＿＿＿＿＿</span>
        </div>
      </div>
      <div className="mt-8 print:hidden">
        <button
          onClick={() => window.print()}
          className="rounded-md bg-[#1b2a33] px-4 py-2 text-[13px] text-white hover:opacity-90"
        >
          打印 / 另存为 PDF
        </button>
      </div>
    </div>
  );
}

export default function QuotePrintPage() {
  return (
    <Suspense fallback={<Skeleton placeholder={<Skeleton.Paragraph rows={6} />} loading active />}>
      <PrintBody />
    </Suspense>
  );
}
