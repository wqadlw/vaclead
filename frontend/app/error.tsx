"use client";

/**
 * 路由级错误边界（Next app router 约定）：渲染异常不再白屏——
 * 显示可行动的错误页（重试/回工作台），错误详情留控制台。
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 p-6">
      <div className="text-[16px] font-medium text-[var(--ink)]">页面出了点问题</div>
      <div className="max-w-md text-center text-[12.5px] leading-5 text-[var(--muted)]">
        {error.message || "渲染时发生异常"}——你的数据不会丢，点下面重试或先回工作台。
      </div>
      <div className="mt-1 flex gap-2">
        <button
          onClick={reset}
          className="h-9 cursor-pointer rounded-lg border-0 bg-[#131416] px-5 text-[13.5px] font-medium text-white transition-opacity active:opacity-70"
        >
          重试
        </button>
        <a
          href="/"
          className="flex h-9 items-center rounded-lg border border-[var(--border)] bg-white px-5 text-[13.5px] text-[var(--ink)] hover:border-[var(--accent)]"
        >
          回工作台
        </a>
      </div>
    </div>
  );
}
