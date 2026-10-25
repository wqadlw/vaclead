/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "export",
  images: { unoptimized: true },
  transpilePackages: ["@douyinfe/semi-ui", "@douyinfe/semi-icons", "@douyinfe/semi-foundation"],
};

export default nextConfig;
