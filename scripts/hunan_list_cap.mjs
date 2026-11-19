// 湖南列表接口捕获 v2：首页完整加载不清零（列表组件会调列表 API）+ 关键词搜索
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const xhrs = [];
page.on("request", (req) => {
  if (req.resourceType() === "xhr" || req.resourceType() === "fetch") {
    xhrs.push({ m: req.method(), u: req.url().slice(0, 280), post: (req.postData() || "").slice(0, 500) });
  }
});
page.on("response", async (res) => {
  const u = res.url();
  if (res.request().resourceType() === "xhr" && /(notice|Notice|article|Article|list|List|query|search)/i.test(u)) {
    let body = "";
    try { body = (await res.text()).slice(0, 600); } catch { body = "<x>"; }
    xhrs.push({ resp: u.slice(0, 280), body });
  }
});

try {
  await page.goto("http://www.ccgp-hunan.gov.cn/", { waitUntil: "commit", timeout: 40000 });
  await page.waitForTimeout(8000);
  console.log("== 首页全部 XHR（找列表接口）==");
  for (const x of xhrs.slice(0, 14)) {
    console.log(x.m || "RESP", x.u || x.resp);
    if (x.post) console.log("  POST:", x.post);
    if (x.body) console.log("  BODY:", x.body.replace(/\s+/g, " ").slice(0, 350));
  }
} catch (e) {
  console.log("ERR:", String(e).slice(0, 140));
}
await browser.close();
console.log("DONE");
