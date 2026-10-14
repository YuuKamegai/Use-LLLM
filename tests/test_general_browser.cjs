// Optional integration smoke: PLAYWRIGHT_MODULE=/path/to/playwright node this-file
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { mkdtemp, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const net = require("node:net");
  const listener = net.createServer();
  await new Promise(resolve => listener.listen(0, "127.0.0.1", resolve));
  const port = listener.address().port; await new Promise(resolve => listener.close(resolve));
  const python = process.env.PYTHON || path.resolve(".venv/Scripts/python.exe");
  const server = spawn(python, ["-m", "tests.general_ui_fixture", String(port)], { stdio: ["ignore", "pipe", "pipe"] });
  let logs = ""; server.stderr.on("data", chunk => logs += chunk);
  const directory = await mkdtemp(path.join(tmpdir(), "session-export-"));
  let browser;
  try {
    const url = `http://127.0.0.1:${port}`;
    for (let n = 0; n < 100; n++) {
      try { if ((await fetch(url)).ok) break; } catch {}
      if (server.exitCode !== null) throw new Error(logs);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    browser = await chromium.launch({ channel: "msedge", headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    await page.goto(url);
    await page.locator("#session-name").waitFor({ state: "visible" });
    await page.locator("#session-name").fill("自由な名前 <safe>");
    const saved = page.waitForResponse(response => response.request().method() === "PATCH");
    await page.locator("#session-name").press("Enter"); assert.equal((await saved).status(), 200);
    await page.reload(); await page.locator("#session-name").waitFor({ state: "visible" });
    assert.equal(await page.locator("#session-name").inputValue(), "自由な名前 <safe>");
    await page.locator("#toggle-settings").click();
    await page.locator('[data-language="en"]').click();
    await page.waitForFunction(() => document.documentElement.lang === "en");
    assert.equal(await page.locator("#delete-session").innerText(), "Delete history");
    assert.equal(await page.locator("#export-session").innerText(), "Export HTML");
    assert.equal(await page.locator(".message.user .message-content").innerText(), "接続と設定");
    await page.locator("#add-server").click();
    assert.match(await page.locator("#server-form").innerText(), /Arguments \(one per line\)/);
    await page.reload(); await page.locator("#session-name").waitFor({ state: "visible" });
    assert.equal(await page.locator("html").getAttribute("lang"), "en");
    await page.locator("#session-name").fill("escaped edit");
    await page.locator("#session-name").press("Escape");
    assert.equal(await page.locator("#session-name").inputValue(), "自由な名前 <safe>");
    const actions = await page.locator("#export-session, #delete-session").evaluateAll(nodes => nodes.map(node => ({ id: node.id, x: node.getBoundingClientRect().x })));
    assert.ok(actions[0].x < actions[1].x);
    const downloaded = page.waitForEvent("download"); await page.locator("#export-session").click();
    const file = path.join(directory, "session.html"); await (await downloaded).saveAs(file);
    const offline = await browser.newContext({ offline: true });
    const exported = await offline.newPage(); exported.on("pageerror", error => errors.push(error.message));
    const network = []; exported.on("request", request => { if (/^https?:/.test(request.url())) network.push(request.url()); });
    await exported.goto(pathToFileURL(file).href);
    await exported.waitForFunction(() => document.querySelectorAll(".js-plotly-plot").length === 1);
    assert.equal(await exported.locator(".message").count(), 3);
    assert.ok((await exported.locator(".chat-pca-plot").boundingBox()).width > 600);
    await exported.locator(".tool-image").scrollIntoViewIfNeeded();
    await exported.locator(".tool-image").evaluate(image => image.decode());
    assert.ok(await exported.locator(".tool-image").evaluate(image => image.complete && image.naturalWidth > 0));
    assert.equal(await exported.locator(".katex").count(), 1);
    assert.equal(await exported.evaluate(() => window.injected), undefined);
    assert.match(await exported.locator("#raw-session").textContent(), /test-history/);
    assert.deepEqual(network, []); assert.deepEqual(errors, []);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.locator("#export-session").isVisible());
    await page.locator("#toggle-settings").click(); await page.locator('[data-language="ja"]').click();
    assert.equal(await page.locator("#delete-session").innerText(), "履歴を削除");
    console.log("general browser smoke passed: rename, persistence, JA/EN, mobile, offline HTML/images/math/plot/history");
  } finally {
    if (browser) await browser.close();
    server.kill(); await rm(directory, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
