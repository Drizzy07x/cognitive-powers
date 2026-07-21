const { defineConfig } = require('@playwright/test');

const port = Number(process.env.COGNITIVE_WEB_BENCHMARK_PORT || 41791);

module.exports = defineConfig({
  testDir: './tests',
  timeout: 15_000,
  use: { baseURL: `http://127.0.0.1:${port}` },
  webServer: {
    command: 'node server.js',
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 15_000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
