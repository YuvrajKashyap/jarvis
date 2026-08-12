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
    await capture(browser, "proactivity", { width: 1280, height: 720 }, "proactive-assistance.png", true);
    await capture(browser, "memory", { width: 1280, height: 720 }, "memory-continuity.png");
    await capture(browser, "privacy", { width: 1280, height: 720 }, "private-mode.png");
    await capture(browser, "approval", { width: 1280, height: 720 }, "approval-gate.png");
    await capture(browser, "phone", { width: 430, height: 860 }, "phone-companion.png");
    await capture(browser, "orb", { width: 400, height: 400 }, "resting-orb.png");
  } finally {
    await browser.close();
  }
} finally {
  server.kill();
}

async function capture(browser, scenario, viewport, filename, expandReason = false) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  try {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(`${baseUrl}?scenario=${scenario}`, { waitUntil: "networkidle" });
    if (expandReason) await page.getByText("Why I mentioned it").click();
    await page.locator(".showcase__surface").screenshot({ path: resolve(output, filename) });
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
