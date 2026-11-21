import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8081", "/openapi/v1": "http://127.0.0.1:8081" },
  },
  test: { include: ["tests/**/*.test.ts"] },
});
