import { expect, test } from "@playwright/test";

test("component previews and the integrated access examples", async ({ page }) => {
  const unexpected: string[] = [];
  const agent = { id: "demo", name: "测试助手", can_edit: true, status: "active",
    published_name: "发布助手", published_version: 2, published_revision: 3, revision: 4,
    model_definition_id: "draft", published_model_definition_id: "live" };
  const key = { id: "key-a", name: "门户", key_prefix: "aip_fixture", status: "active", expires_at: null, agent_ids: ["demo"] };
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/me") return route.fulfill({ json: {
      workspace_id: "fixture", user: { display_name: "测试成员", role: "member" },
    } });
    if (path === "/api/v1/agents/demo" && route.request().method() === "GET") return route.fulfill({ json: agent });
    if (path === "/api/v1/agents") return route.fulfill({ json: { items: [agent,
      { ...agent, id: "other", published_name: "未授权助手", published_model_definition_id: undefined }], next_cursor: null } });
    if (path === "/api/v1/agents/other/versions") return route.fulfill({ json: { items: [
      { revision: 4, published_version: null, config: { model_definition_id: "draft" } },
      { revision: 3, published_version: 2, config: { model_definition_id: "live" } }], next_cursor: null } });
    if (path === "/api/v1/access-keys") return route.fulfill({ json: {
      items: [key, { ...key, id: "key-b", name: "服务台", agent_ids: ["other"] },
        { ...key, id: "old", name: "旧凭证", status: "revoked" },
        { ...key, id: "expired", name: "过期凭证", expires_at: "2020-01-01T00:00:00Z" }], next_cursor: null,
    } });
    if (path === "/api/v1/models") return route.fulfill({ json: { items: [
      { id: "live", display_name: "发布模型" }, { id: "draft", display_name: "草稿模型" }], next_cursor: null } });
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

  await page.getByRole("link", { name:"接入密钥", exact:true }).click();
  await expect(page.getByRole("link", { name:"应用接入" })).toHaveCount(0);
  const sample = page.locator(".example-box pre");
  await expect(sample).toHaveCount(0);
  await page.getByLabel("访问凭证").click();
  await expect(page.getByTitle(/旧凭证|过期凭证/)).toHaveCount(0);
  await page.getByTitle("门户（aip_fixture…）").click();
  await page.getByLabel("模型", { exact: true }).click();
  await expect(page.getByTitle(/草稿模型|未授权助手/)).toHaveCount(0);
  await page.getByTitle("发布模型 · 发布助手").click();
  await expect(sample).toContainText("/openapi/v1/agents/demo/conversations");
  await expect(sample).toContainText('"agent_revision": 3');
  await expect(sample).toContainText("Bearer $INKSTONE_KEY_KEY_A");
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: /复制/ }).click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied.replaceAll("\r\n", "\n")).toBe(await sample.textContent());
  await page.getByRole("radiogroup", {name:"调用步骤"}).getByText("发起运行").click();
  await page.getByRole("radiogroup", {name:"示例语言"}).getByText("Python").click();
  await expect(page.locator(".example-box pre")).toContainText("requests.post");
  await expect(page.locator(".example-box pre")).toContainText("Idempotency-Key");
  await page.getByRole("radiogroup", {name:"调用步骤"}).getByText("订阅事件").click();
  await page.getByRole("radiogroup", {name:"示例语言"}).getByText("JavaScript").click();
  await expect(sample).toContainText("text/event-stream");
  await page.getByLabel("访问凭证").click();
  await page.getByTitle("服务台（aip_fixture…）").click();
  await expect(sample).toHaveCount(0);
  await page.getByLabel("模型", { exact: true }).click();
  await page.getByTitle("发布模型 · 未授权助手").click();
  await expect(sample).toContainText("/openapi/v1/agents/other/conversations");
  await expect(sample).toContainText("process.env.INKSTONE_KEY_KEY_B");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.getByRole("tab", {name:/会话记录/}).click();
  await page.getByRole("button", {name:"查询工单进度"}).click();
  await expect(page.getByRole("dialog")).toContainText("visitor_037");
  for (const path of ["/integrations", "/agents/demo/preview/access"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/access-keys$/);
  }
  expect(unexpected).toEqual([]);
});
