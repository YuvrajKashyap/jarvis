import { chromium } from "@playwright/test";

const pairingUrl = process.env.JARVIS_PAIR_URL;
if (!pairingUrl) {
  throw new Error("JARVIS_PAIR_URL is required");
}

const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const requests = [];
  const consoleErrors = [];
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith("/v1/")) requests.push(`${path}:${response.status()}`);
  });
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto(pairingUrl, { waitUntil: "networkidle", timeout: 30_000 });
  await page.waitForFunction(() => window.location.hash === "", undefined, { timeout: 15_000 });
  const liveStatus = page.getByRole("status");
  await liveStatus.waitFor({ timeout: 15_000 });
  const liveState = (await liveStatus.textContent())?.trim();
  if (!liveState) throw new Error("phone live state is empty");

  const expected = [
    "/v1/pairing/",
    "/v1/auth/challenges:201",
    "/v1/auth/sessions:201",
  ];
  for (const required of expected) {
    if (!requests.some((request) => request.includes(required))) {
      throw new Error(`phone flow did not complete ${required}`);
    }
  }
  if (consoleErrors.length > 0) {
    throw new Error(`phone console errors: ${consoleErrors.join(" | ")}`);
  }
  console.log(
    JSON.stringify({
      paired: true,
      authenticated: true,
      liveSocket: true,
      liveState,
      fragmentCleared: true,
      requestStatuses: requests,
    }),
  );
} finally {
  await browser.close();
}
