import { expect, test } from "@playwright/test";

test("component drafts, bindings and API examples stay in the preview", async ({ page }) => {
  const unexpected: string[] = [];
  const agent = { id: "demo", name: "测试助手", can_edit: true, status: "active" };
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/me") return route.fulfill({ json: {
      workspace_id: "fixture", user: { display_name: "测试成员", role: "member" },
    } });
    if (path === "/api/v1/agents/demo" && route.request().method() === "GET") return route.fulfill({ json: agent });
    unexpected.push(path);
    return route.abort();
  });
  await page.route("**/openapi/**", (route) => { unexpected.push(route.request().url()); return route.abort(); });
  // Enter from an existing workspace route so this also runs in the backend's isolated SPA fixture.
  await page.goto("/settings/security");
  await page.getByRole("link", { name: "能力组件", exact:true }).click();
  await page.getByRole("link", { name:"数据源管理" }).click();
  await page.getByRole("button", { name:"创建数据源" }).click();
  const modal = page.getByRole("dialog");
  await modal.getByLabel("名称", { exact:true }).fill("质检库");
  await modal.getByRole("button", { name:"下一步" }).click();
  await modal.getByLabel("连接地址").fill("postgresql://demo.example.invalid/qa");
  await modal.getByRole("button", { name:"保存预览" }).click();
  await page.getByRole("textbox", { name:"搜索组件" }).fill("质检库");
  await expect(page.getByRole("row").filter({ hasText:"质检库" })).toHaveCount(1);
  await page.getByRole("link", {name:"工作流设计"}).click();
  await page.getByRole("button", {name:/用户输入/}).click();
  await page.getByLabel("输入说明").fill("填写质检记录");

  await page.getByRole("link", { name:"应用接入", exact:true }).click();
  await page.getByRole("button", {name:"生成示例 Key"}).click();
  await expect(page.getByLabel("示例 Key")).toHaveValue("DEMO-NOT-A-KEY-1");
  await page.getByRole("radiogroup", {name:"调用步骤"}).getByText("发起运行").click();
  await page.getByRole("radiogroup", {name:"示例语言"}).getByText("Python").click();
  await expect(page.locator(".example-box pre")).toContainText("requests.post");
  await expect(page.locator(".example-box pre")).toContainText("Idempotency-Key");
  await page.getByRole("tab", {name:"会话记录"}).click();
  await page.getByRole("button", {name:"查询工单进度"}).click();
  await expect(page.getByRole("dialog")).toContainText("visitor_037");
  expect(unexpected).toEqual([]);
});
