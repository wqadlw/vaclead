// 山东详情路由参数名试探（用后即删）：/detail 路由 + 多参数形态，抓 getNoticeBody XHR
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const NOTICE_ID = "BDfixdXXhxDnZTa-c0Ba4otISV6POyPnOMwEHfDj";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const found = [];
page.on("request", (req) => {
  if (/getNoticeBody|getDetail/i.test(req.url())) {
    found.push({
      m: req.method(),
      u: req.url().slice(0, 300),
      post: (req.postData() || "").slice(0, 200),
    });
  }
});
page.on("response", async (res) => {
  if (/getNoticeBody/i.test(res.url())) {
    let body = "";
    try { body = (await res.text()).slice(0, 500); } catch { body = "<x>"; }
    found.push({ resp: res.url().slice(0, 300), body });
  }
});

for (const [name, url] of [
  ["id", `https://www.ccgp-shandong.gov.cn/detail?id=${NOTICE_ID}&colCode=0301`],
  ["noticeId", `https://www.ccgp-shandong.gov.cn/detail?noticeId=${NOTICE_ID}&colCode=0301`],
  ["ggid", `https://www.ccgp-shandong.gov.cn/detail?ggid=${NOTICE_ID}`],
]) {
  try {
    await page.goto(url, { waitUntil: "commit", timeout: 30000 });
    await page.waitForTimeout(5000);
    console.log(`[${name}] ${page.url().slice(0, 100)}`);
  } catch (e) {
    console.log(`[${name}] ERR`, String(e).slice(0, 80));
  }
}
await browser.close();
console.log("==== getNoticeBody 捕获 ====");
for (const x of found.slice(0, 10)) {
  console.log(x.m || "RESP", x.u || x.resp);
  if (x.post) console.log("  POST:", x.post);
  if (x.body) console.log("  BODY:", x.body.replace(/\s+/g, " ").slice(0, 400));
}
if (found.length === 0) console.log("无捕获——详情路由参数名均未命中");
console.log("DONE");
