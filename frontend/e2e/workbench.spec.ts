import { expect, test } from "@playwright/test";

test("member configures, reconnects, cancels and archives a conversation", async ({ page, context }) => {
  test.skip(!test.info().config.metadata.liveWorkbench, "Started by the isolated backend/browser fixture");
  test.setTimeout(90000);
  await page.goto("/agents");
  await page.getByLabel("登录名").fill("member");
  await page.getByLabel("密码", { exact: true }).fill("member-password-1");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("link", { name: "创建智能体", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("验收助手");
  await page.getByLabel("模型", { exact: true }).click();
  await page.getByText("文本模型", { exact: true }).click();
  await page.getByLabel("历史消息上限").fill("2");
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByRole("button", { name: "新建对话" })).toHaveCount(0);
  await page.getByRole("button", { name: "发布", exact: true }).click();
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(page.getByText("发布版本 v1", { exact: true })).toBeVisible();
  const snapshot = "发布版本 v1";
  const conversation = page.url();
  async function send(text: string) {
    await page.getByLabel("消息", { exact: true }).fill(text);
    await page.getByRole("button", { name: "发送", exact: true }).click();
  }
  await send("重连测试");
  await expect(page.getByText("首段", { exact: true })).toBeVisible();
  await context.setOffline(true);
  await context.setOffline(false);
  // Reconnect with a real cursor that predates the retained event window.
  await page.route(
    "**/api/v1/runs/*/events?*",
    async (route) => {
      const url = new URL(route.request().url());
      const run = url.pathname.split("/").at(-2);
      url.searchParams.set("after", `${run}:0`);
      await route.continue({ url: url.toString() });
    },
    { times: 1 },
  );
  const expired = page.waitForResponse((r) => r.url().includes("/events?") && r.status() === 410);
  await page.reload();
  await expired;
  await expect(page.getByText("首段与尾段", { exact: true })).toBeVisible();
  await expect(page.locator("[data-status=completed]")).toHaveCount(1);
  await send("取消测试");
  await expect(page.getByText("已保存部分", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "停止生成" }).click();
  await expect(page.locator("[data-status=cancelled]")).toBeVisible();
  await send("第二轮");
  await expect(page.locator("[data-status=completed]")).toHaveCount(2);
  await send("第三轮");
  await expect(page.getByText(/已使用摘要/)).toBeVisible();
  await page.getByRole("link", { name: "用量", exact: true }).click();
  await expect(page.getByRole("cell", { name: "context_summary", exact: true })).toHaveCount(1);
  await page.getByRole("link", { name: "对话", exact: true }).click();
  await page.getByRole("link", { name: "重连测试", exact: true }).click();
  await expect(page).toHaveURL(conversation);
  await expect(page.getByText(snapshot!, { exact: true })).toBeVisible();
  await expect(page.getByText("首段与尾段", { exact: true })).toHaveCount(1);
  await expect(page.getByText("已保存部分", { exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "删除会话", exact: true }).click();
  const first = page.waitForResponse((r) => r.request().method() === "DELETE");
  await page.getByRole("button", { name: "确认删除" }).click();
  expect((await first).status()).toBe(503);
  await expect(page.getByText("会话已隐藏，等待归档确认")).toBeVisible();
  await expect(page.getByRole("link", { name: "重连测试", exact: true })).toHaveCount(0);
  await expect(async () => {
    const ack = page.waitForResponse((r) => r.request().method() === "DELETE");
    await page.getByRole("button", { name: "重试删除" }).click();
    expect((await ack).status()).toBe(202);
  }).toPass({ timeout: 10000 });
  await expect(page.getByRole("button", { name: "重试删除" })).toHaveCount(0);
});
