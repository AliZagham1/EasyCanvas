import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  timeout: 30000,
  fullyParallel: true,
  use: { baseURL: process.env.EASYCANVAS_UI_BASE_URL || "http://localhost:3004", headless: true },
  webServer: {
    command: "npm run start -- --port 3004",
    url: "http://localhost:3004/study-assistant",
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
});
