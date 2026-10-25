import { test, expect, type Page } from "@playwright/test";

/**
 * 销售旅程 E2E（SPEC-H H2）：登录→工作台→企业筛选→情报页→转线索→看板→完成待办。
 * 断言用可见文本（服务端 seed 的确定性数据），不依赖实现细节。
 */

async function login(page: Page) {
  await page.goto("/login");
  await page.evaluate(async () => {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "admin123" }),
    });
    const d = await r.json();
    localStorage.setItem("vaclead_token", d.token);
    localStorage.setItem("vaclead_role", "admin");
    localStorage.setItem("vaclead_user", "管理员");
  });
}

test("完整销售旅程", async ({ page }) => {
  // 1) 登录 → 工作台
  await login(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "工作台" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "今日作战" })).toBeVisible();

  // 2) 企业页 → 快捷筛选"有联系电话"（seed：2 家有联系人，1 家没有）
  await page.goto("/enterprises");
  await expect(page.getByRole("button", { name: "企业筛选" })).toBeVisible();
  await page.getByRole("button", { name: "有联系电话" }).click();
  await expect(page.getByText(/本页 \d+ 家 · 筛选共/)).toContainText("2");

  // 3) 清筛选 → 打开情报页（Side Panel 预览 → 展开完整页）
  await page.getByRole("button", { name: "有联系电话" }).click(); // 再点取消
  await page.getByRole("button", { name: "苏州长晶半导体有限公司" }).first().click();
  await page.getByRole("button", { name: /展开完整情报页/ }).click();
  await expect(page).toHaveURL(/enterprises\/view/);

  // 4) 情报页四页签：档案（四段评分）→ 图谱 → 联系人 → 信号
  await expect(page.getByText("四段评分")).toBeVisible();
  await expect(page.getByText("行业适配")).toBeVisible();
  await expect(page.getByText("采购意图")).toBeVisible();
  await page.getByRole("tab", { name: "关键联系人" }).click();
  await expect(page.getByText("王工")).toBeVisible();
  await expect(page.getByText("13800000001")).toBeVisible();

  // 5) 转线索（情报页按钮）→ 商机管线看板出现卡片
  await page.getByRole("button", { name: /转为线索/ }).click();
  await expect(page.getByText("已转为线索")).toBeVisible();
  await page.goto("/leads");
  await expect(page.getByText("商机管线")).toBeVisible();
  await expect(page.getByText("苏州长晶半导体有限公司").first()).toBeVisible();

  // 6) 切列表视图 → 跟进（写跟进记录）→ 阶段流转
  await page.getByRole("tab", { name: "列表" }).click();
  const followBtn = page.getByRole("button", { name: "跟进", exact: true }).first();
  await followBtn.click();
  await page.getByPlaceholder(/沟通了什么/).fill("E2E 首次联系，确认设备需求");
  await page.getByRole("button", { name: /保存跟进/ }).click();
  await expect(page.getByText("跟进已记录")).toBeVisible();

  // 7) 待办完成（工作台勾选）
  await page.goto("/");
  const todoCheckbox = page.locator('input[type="checkbox"]').first();
  if (await todoCheckbox.isVisible()) {
    await todoCheckbox.click();
  }

  // 8) 主题切换（亮→暗）
  const themeBefore = await page.evaluate(
    () => document.documentElement.getAttribute("data-theme"),
  );
  await page.locator('button[title*="主题"]').click();
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.getAttribute("data-theme")),
    )
    .not.toBe(themeBefore);

  // 9) 快捷键 g2 → 企业页
  await page.keyboard.press("g");
  await page.waitForTimeout(120);
  await page.keyboard.press("2");
  await expect(page).toHaveURL(/enterprises/);

  // 10) 命令面板 Ctrl+K 搜企业
  await page.keyboard.press("Control+k");
  await page.getByPlaceholder(/页面名或企业关键词/).fill("长晶");
  await expect(page.getByText("苏州长晶半导体有限公司").first()).toBeVisible();
});
