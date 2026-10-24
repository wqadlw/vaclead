import type { Metadata } from "next";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { antdTheme } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: "vaclead · 真空设备智能拓客",
  description: "真空设备行业智能拓客平台：招投标信号 × 企业画像 × 线索跟进",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdRegistry>
          <ConfigProvider theme={antdTheme} locale={zhCN}>
            {children}
          </ConfigProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
