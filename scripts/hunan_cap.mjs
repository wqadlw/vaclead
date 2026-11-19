// 湖南政采公告列表页 XHR 捕获（用后即删）——luban 组件四省同款
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const xhrs = [];
page.on("request", (req) => {
  if (req.resourceType() === "xhr" || req.resourceType() === "fetch") {
    xhrs.push({ m: req.method(), u: req.url().slice(0, 260), post: (req.postData() || "").slice(0, 400) });
  }
});
page.on("response", async (res) => {
  const u = res.url();
  if (res.request().resourceType() === "xhr" && /(notice|Notice|list|List|query|search)/i.test(u)) {
    let body = "";
    try { body = (await res.text()).slice(0, 500); } catch { body = "<x>"; }
    xhrs.push({ resp: u.slice(0, 260), body });
  }
});

try {
  await page.goto("http://www.ccgp-hunan.gov.cn/", { waitUntil: "commit", timeout: 40000 });
  await page.waitForTimeout(6000);
  xhrs.length = 0;
  // 点"采购公告"类导航
  const link = page.locator("a").filter({ hasText: /采购公告|公告/ }).first();
  const href = await link.getAttribute("href", { timeout: 8000 }).catch(() => null);
  console.log("公告链接:", String(href).slice(0, 120));
  if (href && href !== "javascript:void(0)" && href !== "#") {
    await page.goto(new URL(href, "http://www.ccgp-hunan.gov.cn/").href, { waitUntil: "commit", timeout: 40000 });
    await page.waitForTimeout(6000);
  } else {
    await link.click({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(6000);
  }
  console.log("落地 URL:", page.url().slice(0, 140));
} catch (e) {
  console.log("ERR:", String(e).slice(0, 140));
}
console.log("==== XHR ====");
for (const x of xhrs.slice(0, 10)) {
  console.log(x.m || "RESP", x.u || x.resp);
  if (x.post) console.log("  POST:", x.post);
  if (x.body) console.log("  BODY:", x.body.replace(/\s+/g, " ").slice(0, 400));
}
await browser.close();
console.log("DONE");
