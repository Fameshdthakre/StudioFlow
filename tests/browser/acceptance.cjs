const { chromium } = require("@playwright/test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
(async () => {
  const browser = await chromium.launch({
    channel: process.env.STUDIOFLOW_BROWSER_CHANNEL || undefined,
    headless: true,
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (dialog) => dialog.accept());
  const root = path.resolve(__dirname, "../..");
  await page.goto(
    process.env.STUDIOFLOW_BROWSER_URL || "http://127.0.0.1:18001",
  );
  await page.fill("#login-user", "tester");
  await page.fill("#login-pass", "password");
  await page.click("#login-form button");
  await page.waitForSelector("#login-overlay", { state: "hidden" });
  await page.setInputFiles(
    "#photos",
    path.join(root, ".tmp/browser-fixtures/ui-reference.png"),
  );
  await page.waitForSelector(".plan-sku");
  await page.fill(".plan-sku", "UI-TEST-" + Date.now());
  await page.setInputFiles(
    ".plan-cutout",
    path.join(root, ".tmp/browser-fixtures/ui-cutout.png"),
  );
  await page.fill(".plan-pack", "3");
  await page.check(".plan-approved");
  await page.click("[data-kind=main]");
  await page.click("[data-kind=infographic]");
  await page.selectOption("#plan-card-template", "dimensions");
  await page.fill("#plan-card-headline", "60 CM PICKS");
  await page.fill("#plan-card-body", "Three verified picks included");
  await page.fill("#plan-card-measurement", "60");
  await page.check("#plan-card-confirmed");
  assert.equal(await page.inputValue("#quality"), "high");
  assert.equal(await page.locator("#type-tabs .selected").count(), 3);
  await page.click("[data-kind=infographic]");
  await page.click("[data-kind=infographic]");
  assert.equal(await page.inputValue("#plan-card-headline"), "60 CM PICKS");
  assert.equal(await page.textContent("#estimated-count"), "3");
  fs.mkdirSync(path.join(root, ".tmp/ui-checks"), { recursive: true });
  await page.screenshot({
    path: path.join(root, ".tmp/ui-checks/create-desktop.png"),
    fullPage: true,
  });
  const submitted = page.waitForResponse(
    (r) => r.url().endsWith("/api/generate") && r.request().method() === "POST",
  );
  await page.click("#generate-btn");
  const response = await submitted;
  assert.equal(response.status(), 200, await response.text());
  const job = await response.json();
  assert.equal(job.images_queued, 3);
  await page.click("#nav-gallery");
  for (let i = 0; i < 30; i++) {
    const rows = await page.evaluate(async () =>
      (await fetch("/api/library")).json(),
    );
    if (
      rows.items.filter(
        (r) => r.job_id === job.job_id && r.status === "completed",
      ).length === 3
    )
      break;
    await page.waitForTimeout(500);
  }
  await page.click("#gallery-refresh");
  await page.waitForSelector(".library-choice");
  const sku = await page.locator(".image-meta h3").first().textContent();
  await page.fill("#filter-sku", sku.split(" · ")[0]);
  await page.waitForTimeout(300);
  await page.click("#library-select-page");
  assert.match(await page.textContent("#library-selection"), /^3 selected/);
  await page.fill("#filter-sku", "missing-product");
  await page.waitForTimeout(300);
  assert.match(
    await page.textContent("#library-selection"),
    /3 selected.*3 outside/,
  );
  await page.fill("#filter-sku", sku.split(" · ")[0]);
  await page.waitForTimeout(300);
  await page.click("#gallery-refresh");
  await page.waitForTimeout(300);
  assert.equal(await page.locator(".library-choice input:checked").count(), 3);
  await page.click("#library-export");
  await page.getByText("Prepare ZIP", { exact: true }).waitFor();
  assert.match(await page.textContent("#export-preview"), /MAIN\.jpg/);
  assert.match(await page.textContent("#export-preview"), /PT02\.jpg/);
  const downloadEvent = page.waitForEvent("download", { timeout: 30000 });
  await page.getByText("Prepare ZIP", { exact: true }).click();
  const download = await downloadEvent;
  assert.equal(download.suggestedFilename(), "studioflow-images.zip");
  await download.saveAs(path.join(root, ".tmp/ui-checks/export.zip"));
  await page.screenshot({
    path: path.join(root, ".tmp/ui-checks/library-desktop.png"),
    fullPage: true,
  });
  await page.locator(".review-btn").first().click();
  await page.locator("#layout-editor summary").click();
  await page.fill("#edit-card-headline", "Revised verified copy");
  await page.check("#edit-card-confirmed");
  await page.check("#edit-cutout-confirmed");
  const composed = page.waitForResponse((r) => r.url().endsWith("/compose"));
  await page.click("#save-layout");
  const compositionResponse = await composed;
  assert.equal(
    compositionResponse.status(),
    200,
    await compositionResponse.text(),
  );
  assert.equal((await compositionResponse.json()).paid_requests, 0);
  await page.click("#nav-create");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(root, ".tmp/ui-checks/create-mobile.png"),
    fullPage: true,
  });
  const dimensions = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    width: innerWidth,
  }));
  assert.ok(
    dimensions.scroll <= dimensions.width + 1,
    JSON.stringify(dimensions),
  );
  assert.deepEqual(errors, []);
  console.log(
    "Browser acceptance passed: three image types, settings retained, stable ZIP names, selection across refresh/filters, local recomposition, mobile layout, no page errors.",
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
