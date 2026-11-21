// 皖桂吉三省 luban searchHome 参数捕获（用后即删）
// 组件同款 → /portal/searchHome 模式通用，各省 code/subCodes 不同——抓 POST body
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const provinces = [
  ["安徽", "http://www.ccgp-anhui.gov.cn/"],
  ["广西", "https://zfcg.gxzf.gov.cn/"],
  ["吉林", "http://www.ccgp-jilin.gov.cn/"],
];

const browser = await chromium.launch({ headless: true });
for (const [name, url] of provinces) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const posts = [];
  page.on("request", (req) => {
    if (/searchHome|search/i.test(req.url()) && req.method() === "POST") {
      posts.push({ u: req.url().slice(0, 200), post: (req.postData() || "").slice(0, 400) });
    }
  });
  page.on("response", async (res) => {
    if (/searchHome/i.test(res.url()) && res.request().method() === "POST") {
      let body = "";
      try { body = (await res.text()).slice(0, 300); } catch { body = "<x>"; }
      posts.push({ resp: res.url().slice(0, 200), body });
    }
  });
  try {
    await page.goto(url, { waitUntil: "commit", timeout: 40000 });
    await page.waitForTimeout(8000);
    console.log(`== ${name} ==`);
    for (const p of posts.slice(0, 6)) {
      console.log(" POST", p.u);
      if (p.post) console.log("   body:", p.post);
      if (p.body) console.log("   resp:", p.body.replace(/\s+/g, " ").slice(0, 220));
    }
    if (posts.length === 0) console.log("  无 searchHome 调用——路径不同");
  } catch (e) {
    console.log(`== ${name} ERR:`, String(e).slice(0, 100));
  }
  await page.close();
}
await browser.close();
console.log("DONE");
