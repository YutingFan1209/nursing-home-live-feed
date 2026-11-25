// Front-end tests: the real app served by Vite, with deals.json replaced by
// tests/fixtures/deals.json (tests/helpers.js), so results don't move with the
// live data. Test-only: nothing here builds or deploys the site.
import { defineConfig, devices } from "@playwright/test";

const PORT = 5179;

export default defineConfig({
  testDir: "tests",
  fullyParallel: true,
  reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  use: {
    baseURL: `http://localhost:${PORT}/nursing-home-live-feed/`,
    trace: "retain-on-failure",
    // Same text pixels every run: sub-pixel glyph positioning, hinting and
    // LCD smoothing varied between runs and failed the pixel test
    // (tests/visual.spec.js, 2026-11-25)
    launchOptions: { args: ["--disable-font-subpixel-positioning", "--font-render-hinting=none",
      "--disable-lcd-text", "--force-color-profile=srgb"] },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } } },
  ],
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}/nursing-home-live-feed/`,
    reuseExistingServer: false,
  },
});
