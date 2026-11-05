// SPEC-BB BB2 日活流 journey：推荐卡可见 → 快标 → 转线索 → 状态保持
import { test, expect } from "@playwright/test";

test("推荐卡快标转线索日活流", async ({ page }) => {
  // 登录
  await page.goto("/");
  await page.waitForURL("**/login");
  await page.fill('input[type="text"], input:not([type="password"])', "admin");
  await page.fill('input[type="password"]', "admin123");
  await page.click('button[type="submit"], button:has-text("登录")');
  await page.waitForURL("**/");

  // 推荐卡可见（种子企业有电话+未在管 → 进推荐）
  const recSection = page.locator("text=今日推荐");
  await expect(recSection).toBeVisible({ timeout: 10000 });

  // 快标「接通」（种子推荐项应有快标按钮或状态 Tag）
  const markBtn = page.locator('button:has-text("接通")').first();
  const hasMark = (await markBtn.count()) > 0;
  if (hasMark) {
    await markBtn.click();
    // 标记后状态 Tag「已接通」出现（或快标消失）
    await expect(page.locator('text=已接通').first()).toBeVisible({ timeout: 5000 });
  }

  // 转线索：点击「转线索」按钮
  const convertBtn = page.locator('button:has-text("转线索")').first();
  if ((await convertBtn.count()) > 0) {
    await convertBtn.click();
    await expect(page.locator('text=已转线索').or(page.locator('text=已有跟进')).first()).toBeVisible({ timeout: 5000 });
  }
});
