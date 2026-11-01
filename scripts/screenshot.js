// README screenshot: images/headline.png from the live site ($URL) or a local server
// (http://localhost:$PORT, default 8000). Run: NODE_PATH=<env>/lib/node_modules node scripts/screenshot.js
const { chromium } = require("playwright");

(async () => {
  const url = process.env.URL || `http://localhost:${process.env.PORT || 8000}/`;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: "load" });
  await page.waitForFunction(() => typeof map !== "undefined" && map && map.loaded() && document.querySelector("#chart svg"),
    null, { timeout: 60000 });
  await page.waitForTimeout(3000);  // basemap tiles
  await page.screenshot({ path: "images/headline.png" });
  console.log(`images/headline.png from ${url}`);
  await browser.close();
})();
