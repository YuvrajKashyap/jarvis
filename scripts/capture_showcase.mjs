import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = resolve(root, "docs", "assets");
const baseUrl = "http://127.0.0.1:1422/showcase.html";
const vite = resolve(root, "node_modules", "vite", "bin", "vite.js");
const server = spawn(
  process.execPath,
  [vite, "--config", "ui/vite.config.ts", "--host", "127.0.0.1", "--port", "1422"],
  {
    cwd: root,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  },
);
let serverOutput = "";
server.stdout.on("data", (chunk) => (serverOutput += chunk.toString()));
server.stderr.on("data", (chunk) => (serverOutput += chunk.toString()));

try {
  await waitForServer(baseUrl);
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    await capture(browser, "conversation", { width: 1280, height: 720 }, "desktop-conversation.png");
    await capture(browser, "approval", { width: 1280, height: 720 }, "approval-gate.png");
    await capture(browser, "readiness", { width: 1280, height: 780 }, "readiness-diagnostics.png", true);
    await capture(browser, "phone", { width: 430, height: 860 }, "phone-companion.png");
  } finally {
    await browser.close();
  }
} finally {
  server.kill();
}

async function capture(browser, scenario, viewport, filename, expandDetails = false) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  try {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(`${baseUrl}?scenario=${scenario}`, { waitUntil: "networkidle" });
    if (expandDetails) await page.getByText("3 checks need attention").click();
    await page.screenshot({ path: resolve(output, filename), fullPage: true });
  } finally {
    await page.close();
  }
}

async function waitForServer(url) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // The local dev server has not bound its socket yet.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 150));
  }
  throw new Error(`JARVIS showcase server did not start.\n${serverOutput}`);
}
