"use client";

/** Semi Design 客户端包装层：locale + 全量样式。
 *  Semi 主入口含 `export *`（tiptap 依赖），不能被服务端组件直接引入；
 *  其 dist/css 未列入 package exports，故样式文件拷贝至 styles/semi.min.css 引入。 */
import { LocaleProvider } from "@douyinfe/semi-ui";
import zh_CN from "@douyinfe/semi-ui/lib/es/locale/source/zh_CN";
import "../styles/semi.min.css";

export default function Providers({ children }: { children: React.ReactNode }) {
  return <LocaleProvider locale={zh_CN}>{children}</LocaleProvider>;
}
