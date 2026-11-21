import { defineConfig } from "@playwright/test";
import { loadEnv } from "vite";

const { WORKBENCH_TEST_ORIGIN: liveOrigin } = loadEnv("test", ".", "WORKBENCH_TEST_");

export default defineConfig({
  testDir: "./e2e",
  metadata: { liveWorkbench: Boolean(liveOrigin) },
  use: { baseURL: liveOrigin ?? "http://127.0.0.1:5174" },
  webServer: liveOrigin ? undefined : [
    {
      command: "uv run --locked uvicorn ai_platform.api.main:app --host 127.0.0.1 --port 8081",
      cwd: "../backend",
      url: "http://127.0.0.1:8081/api/health",
    },
    { command: "npm run dev", url: "http://127.0.0.1:5174" },
  ],
});
