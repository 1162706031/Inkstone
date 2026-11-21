import { expect, test } from "@playwright/test";

test("member finds shared and editable agents and opens the P1 configuration", async ({ page }) => {
  const own = { id: "own-agent", name: "写作助手", description: "校对文本", can_edit: true,
    revision: 2, published_revision: null, published_version: null, creator_name: "测试成员",
    is_creator: true, can_manage: true, visibility: "private", status: "active",
    updated_at: "2026-06-15T08:00:00Z", model_definition_id: "model" };
  let shared = { ...own, id: "shared-agent", name: "会议助手", description: "整理会议记录",
    is_creator: false, can_manage: false, visibility: "public", published_revision: 1, published_version: 1,
    creator_name: "其他成员", updated_at: "2026-06-16T08:00:00Z" };
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({
    json: { user: { role: "member", display_name: "测试成员" }, workspace_id: "workspace" },
  }));
  await page.route("**/api/v1/agents?*", (route) => route.fulfill({
    json: { items: [own, shared], next_cursor: null },
  }));
  await page.route("**/api/v1/auth/csrf", (route) => route.fulfill({ json: { csrf_token: "test" } }));
  await page.route("**/api/v1/agents/shared-agent", (route) => {
    if (route.request().method() === "PATCH") {
      const draft = route.request().postDataJSON();
      expect(draft.revision).toBe(2);
      shared = { ...shared, name: draft.name, revision: 3 };
    }
    return route.fulfill({ json: shared });
  });
  await page.route("**/api/v1/agents/shared-agent/publish", (route) => {
    expect(route.request().postDataJSON()).toEqual({ revision: 3 });
    shared = { ...shared, published_version: 2, published_revision: 3 };
    return route.fulfill({ json: shared });
  });
  await page.route("**/api/v1/models?*", (route) => route.fulfill({
    json: { items: [{ id: "model", display_name: "文本模型" }], next_cursor: null },
  }));

  await page.goto("/agents");
  const cards = page.locator(".agent-card");
  await expect(cards.first()).toContainText("会议助手");
  await page.getByRole("radiogroup").getByText("我创建的").click();
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("写作助手");
  await page.getByLabel("搜索智能体").fill("shared-agent");
  await expect(page.getByText("没有符合条件的智能体")).toBeVisible();
  await page.getByRole("radiogroup").getByText("全部智能体").click();
  await cards.first().click();
  await expect(page.getByRole("button", { name: "新建对话" })).toBeVisible();
  await expect(page.getByRole("link", { name: "访问接口" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发布", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "归档" })).toHaveCount(0);
  await page.getByLabel("名称", { exact: true }).fill("会议记录助手");
  const publish = page.getByRole("button", { name: "发布", exact: true });
  await expect(publish).toBeDisabled();
  await page.getByRole("button", { name: "保存草稿" }).click();
  await publish.click();
  await expect(page.getByText(/已发布 v2 · 创建人 其他成员/)).toBeVisible();
  await expect(publish).toBeDisabled();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("dialog").getByRole("link", { name: "智能体列表" }).click();
  await page.getByRole("link", { name: "创建智能体" }).click();
  await expect(page.getByLabel("名称", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "保存草稿" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
