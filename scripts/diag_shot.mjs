// 诊断 v2：更宽容的选择器与等待
import { createRequire } from "node:module";

const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1520, height: 900 } });

await page.goto("http://127.0.0.1:8000/login", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1000);
await page.fill('input[type="password"]', "admin123");
const userInput = page.locator("input").first();
await userInput.fill("admin");
await page.locator("button").first().click();
await page.waitForTimeout(3500);
console.log("URL now:", page.url());
await page.screenshot({ path: "E:/AAAA/A-xiaosou/vaclead/data/diag_dashboard.png" });

const btnColor = await page.evaluate(() => {
  const b = document.querySelector(".semi-button-primary");
  return b ? getComputedStyle(b).backgroundColor : "no primary btn found";
});
console.log("Semi primary color:", btnColor);
await browser.close();
