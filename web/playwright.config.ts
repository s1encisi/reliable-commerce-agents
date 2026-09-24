import { defineConfig } from "@playwright/test";

/**
 * e2e 套件不启动任何服务：它假定前端与编排器已经在运行。
 * 全部服务绑定固定端口（3000、5432、6379、8080-8085、8090、9001），
 * 因此同一时间只能跑一套，运行是严格串行的。
 */

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    // 之所以允许覆盖：3000 端口未必就是前端——开发机上别的服务可能已经占了它
    // （至少有一台机器上是 open-webui），这时 compose 里的前端启动后不会发布
    // 端口，于是每个 spec 都在登录步骤失败，而原因与应用本身毫无关系。
    // 用 E2E_BASE_URL 指向前端真正所在的位置即可。
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
