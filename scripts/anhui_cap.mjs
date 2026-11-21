// 安徽 announcementList 真实参数捕获（用后即删）
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const hits = [];
page.on("request", (req) => {
  if (/announcementList/i.test(req.url())) {
    hits.push({
      u: req.url().slice(0, 200),
      post: (req.postData() || "").slice(0, 600),
      h: JSON.stringify(Object.fromEntries(Object.entries(req.headers()).filter(([k]) => /token|auth|sign|cookie|content/i.test(k)))).slice(0, 300),
    });
  }
});
page.on("response", async (res) => {
  if (/announcementList/i.test(res.url())) {
    let body = "";
    try { body = (await res.text()).slice(0, 500); } catch { body = "<x>"; }
    hits.push({ resp: res.url().slice(0, 200), body });
  }
});

try {
  await page.goto("https://www.ccgp-anhui.gov.cn/", { waitUntil: "commit", timeout: 40000 });
  await page.waitForTimeout(10000);
  console.log("首页捕获:", hits.length, "条");
  for (const x of hits.slice(0, 4)) {
    console.log(x.u);
    if (x.post) console.log("  POST:", x.post);
    if (x.h && x.h !== "{}") console.log("  HEADERS:", x.h);
    if (x.body) console.log("  RESP:", x.body.replace(/\s+/g, " ").slice(0, 450));
  }
} catch (e) {
  console.log("ERR:", String(e).slice(0, 140));
}
await browser.close();
console.log("DONE");
