"use client";

/**
 * 无限滚动哨兵（移动 app 惯例）：进入视口（含 600px 预载缓冲）自动触发 onLoadMore，
 * 替代"加载更多"点按按钮。桌面同样生效（LinkedIn/企查查桌面亦为无限滚动）。
 */
import { useEffect, useRef } from "react";

export default function LoadMoreSensor({ onLoadMore }: { onLoadMore: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const cbRef = useRef(onLoadMore);
  cbRef.current = onLoadMore;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) cbRef.current();
      },
      { rootMargin: "600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return <div ref={ref} aria-hidden className="h-px" />;
}
