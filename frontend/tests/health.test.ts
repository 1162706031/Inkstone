import { expect, it, vi } from "vitest";
import { getHealth } from "../src/api/health";

it("reads the health endpoint", async () => {
  const request = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ status: "ok" }));
  await expect(getHealth(request)).resolves.toEqual({ status: "ok" });
  expect(request).toHaveBeenCalledWith("/api/health");
});

it("rejects unsuccessful responses", async () => {
  const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 503 }));
  await expect(getHealth(request)).rejects.toThrow("503");
});
