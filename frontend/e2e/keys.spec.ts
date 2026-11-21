import { expect, test } from "@playwright/test";

const agents = {
  items: [
    { id: "11111111-1111-1111-1111-111111111111", name: "官网客服", description: "", status: "active",
      visibility: "private", published_version: 2, published_revision: 3, revision: 3, model_definition_id: "chat", is_creator: true,
      creator_name: "测试成员", updated_at: "2026-11-21T02:00:00Z" },
    { id: "22222222-2222-2222-2222-222222222222", name: "草稿助手", description: "", status: "active",
      visibility: "private", published_version: null, published_revision: null, is_creator: true,
      creator_name: "测试成员", updated_at: "2026-11-21T02:00:00Z" },
  ],
  next_cursor: null,
};

const key = {
  id: "33333333-3333-3333-3333-333333333333", name: "官网后台", key_prefix: "aip_8Fq2xK",
  status: "active", agent_ids: ["11111111-1111-1111-1111-111111111111"],
  expires_at: null, last_used_at: "2026-11-21T03:30:00Z", revision: 1,
  created_at: "2026-11-20T02:00:00Z", updated_at: "2026-11-20T02:00:00Z",
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({
    json: { user: { role: "member", display_name: "测试成员" }, workspace_id: "fixture" },
  }));
  await page.route("**/api/v1/auth/csrf", (route) => route.fulfill({ json: { csrf_token: "fixture" } }));
  await page.route("**/api/v1/agents?*", (route) => route.fulfill({ json: agents }));
  await page.route("**/api/v1/models?*", (route) => route.fulfill({
    json: { items: [{ id: "chat", display_name: "客服模型" }], next_cursor: null },
  }));
});

test("key listing shows the prefix and bound agents but never a secret", async ({ page }) => {
  await page.route("**/api/v1/access-keys?*", (route) => route.fulfill({
    json: { items: [key], next_cursor: null },
  }));
  await page.goto("/access-keys");
  await expect(page.getByRole("cell", { name: /aip_8Fq2xK/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: "官网客服" })).toBeVisible();
  await expect(page.getByText("生效中")).toBeVisible();
  // The table must offer rotation and revocation, and must not render a full token anywhere.
  await expect(page.getByRole("button", { name: "轮换" })).toBeVisible();
  await expect(page.getByRole("button", { name: "撤销" })).toBeVisible();
  await expect(page.getByText("仅显示一次")).toHaveCount(0);
});

test("creating a key reveals the token once and only lists published agents", async ({ page }) => {
  let created = false;
  await page.route("**/api/v1/access-keys?*", (route) => route.fulfill({
    json: { items: created ? [key] : [], next_cursor: null },
  }));
  await page.route("**/api/v1/access-keys", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    created = true;
    const body = route.request().postDataJSON();
    expect(body.agent_ids).toEqual(["11111111-1111-1111-1111-111111111111"]);
    await route.fulfill({ status: 201, json: { key, token: "aip_8Fq2xK-secret-value-shown-once" } });
  });

  await page.goto("/access-keys");
  await page.getByRole("button", { name: "创建密钥" }).click();
  await page.getByLabel("名称").fill("官网后台");

  // The unpublished agent must not be offered: external callers only run released versions.
  await page.getByLabel("可调用的智能体").click();
  await expect(page.getByTitle("官网客服（我创建的）")).toBeVisible();
  await expect(page.getByText("草稿助手")).toHaveCount(0);
  await page.getByTitle("官网客服（我创建的）").click();
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByText("接入密钥（仅显示一次，请立即复制保存）")).toBeVisible();
  await expect(page.getByText("aip_8Fq2xK-secret-value-shown-once")).toBeVisible();
  await page.getByLabel("访问凭证").click();
  await page.getByTitle("官网后台（aip_8Fq2xK…）").click();
  await page.getByLabel("模型", { exact: true }).click();
  await page.getByTitle("客服模型 · 官网客服").click();
  await expect(page.locator(".example-box pre")).toContainText(`/agents/${agents.items[0].id}/conversations`);
  await expect(page.locator(".example-box pre")).toContainText('"agent_revision": 3');
});

test("revoked and expired keys are distinguishable and offer no actions", async ({ page }) => {
  await page.route("**/api/v1/access-keys?*", (route) => route.fulfill({
    json: {
      items: [
        { ...key, id: "a", name: "退役密钥", status: "revoked" },
        { ...key, id: "b", name: "过期密钥", expires_at: "2020-01-01T00:00:00Z" },
      ],
      next_cursor: null,
    },
  }));
  await page.goto("/access-keys");
  await expect(page.getByText("已撤销", { exact: true })).toBeVisible();
  await expect(page.getByText("已过期", { exact: true })).toBeVisible();
  // A revoked key exposes no rotate/revoke buttons; the expired one still does.
  await expect(page.getByRole("button", { name: "轮换" })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "撤销" })).toHaveCount(1);
});
