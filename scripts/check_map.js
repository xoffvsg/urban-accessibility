// Headless check of docs/ served at http://localhost:$PORT (default 8000; make serve, in another terminal).
// Run: NODE_PATH=$CONDA_PREFIX/lib/node_modules node scripts/check_map.js <out_dir>
// Prints rendered feature counts, popup text and console errors; writes 4 screenshots to <out_dir>.
const { chromium } = require("playwright");
const out = process.argv[2] || ".";

(async () => {
  const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(`http://localhost:${process.env.PORT || 8000}/`, { waitUntil: "load" });
  await page.waitForFunction(() => typeof map !== "undefined" && map && map.loaded() && map.getLayer("bg-fill"), null, { timeout: 60000 });
  await page.waitForTimeout(4000);  // tiles + basemap

  const n = await page.evaluate(() => map.queryRenderedFeatures({ layers: ["bg-fill"] }).length);
  const nsvc = await page.evaluate(() => map.queryRenderedFeatures({ layers: ["services"] }).length);
  // split dots where services share a spot; they collapse to full dots when one layer is off
  const icons = () => page.evaluate(() => map.querySourceFeatures("svc").map((f) => f.properties.icon)
    .filter((i) => i.includes("+")).sort());
  const splitIcons = await icons();
  await page.uncheck('#layers [data-svc="healthcare"]');
  await page.waitForTimeout(300);
  const splitNoHealth = await icons();
  await page.check('#layers [data-svc="healthcare"]');
  const bars = await page.$$eval("#chart .bar", (b) => b.length);
  await page.screenshot({ path: `${out}/map_default.png` });

  // colour of one block group's fill before/after setting all weight on grocery
  const probe = async () => page.evaluate(() => {
    const f = map.queryRenderedFeatures({ layers: ["bg-fill"] }).find((x) => x.properties.geoid === "325100010012");
    return f ? JSON.stringify(map.getPaintProperty("bg-fill", "fill-color")).length + ":" +
      document.querySelector('#chart .bar text').textContent : null;
  });
  const before = await probe();
  for (const s of ["healthcare", "schools", "transit", "parks"]) await page.uncheck(`#on-${s}`);
  await page.waitForTimeout(500);
  const firstBar = await page.$eval("#chart .bar", (g) => g.dataset.geoid + " " + g.querySelectorAll("text")[1].textContent);
  const pctGrocery = await page.$eval("#p-grocery", (e) => e.textContent);
  await page.screenshot({ path: `${out}/map_grocery_only.png` });
  await page.click("#reset");

  // sliders: moving one changes only those below it; the total stays 100
  const setSlider = (id, v) => page.$eval(`#w-${id}`, (el, v) => {
    el.value = v; el.dispatchEvent(new Event("input", { bubbles: true }));
  }, v);
  const readW = () => page.$$eval(".weight input[type=range]", (els) => els.map((e) => +e.value));
  const w0 = await readW();
  await setSlider("grocery", 40);
  const w1 = await readW();
  await setSlider("healthcare", 30);
  const w2 = await readW();
  const sliders = { default: w0, grocery_40: w1, then_healthcare_30: w2,
    sums: [w0, w1, w2].map((w) => w.reduce((a, x) => a + x, 0)),
    grocery_kept: w2[0] === w1[0], last_disabled: await page.$eval(".weight:last-child input[type=range]", (e) => e.disabled) };
  await page.click("#reset");

  // popup on a block group
  const pt = await page.evaluate(() => {
    const f = map.queryRenderedFeatures({ layers: ["bg-fill"] }).find((x) => x.properties.has_residents);
    const c = f.geometry.type === "Polygon" ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
    const [x, y] = c.reduce((a, p) => [a[0] + p[0] / c.length, a[1] + p[1] / c.length], [0, 0]);
    return map.project([x, y]);
  });
  await page.mouse.click(pt.x + 380, pt.y);  // map is right of the 380 px panel
  await page.waitForTimeout(800);
  const popup = await page.$eval(".maplibregl-popup-content", (e) => e.innerText).catch(() => null);
  await page.screenshot({ path: `${out}/map_popup.png` });

  // narrow screen
  await page.setViewportSize({ width: 420, height: 900 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/map_mobile.png`, fullPage: false });

  console.log(JSON.stringify({ block_groups_rendered: n, services_rendered: nsvc, split_icons: splitIcons, split_without_healthcare: splitNoHealth,
    chart_bars: bars,
    before, first_bar_grocery_only: firstBar, grocery_weight: pctGrocery, sliders,
    popup: popup && popup.slice(0, 400), errors }, null, 1));
  await browser.close();
})();
