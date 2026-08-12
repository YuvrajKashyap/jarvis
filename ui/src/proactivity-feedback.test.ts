import { describe, expect, it, vi } from "vitest";

import { sendProactivityFeedback } from "./proactivity-feedback";

describe("proactivity feedback", () => {
  it("sends an authenticated bounded preference action", async () => {
    const fetcher = vi.fn(async () => new Response("{}", { status: 200 }));

    await sendProactivityFeedback(
      "https://jarvis.test",
      "private-token",
      "019fd977-1d96-7892-950c-6afbb71f7cfd",
      "snooze",
      fetcher,
    );

    expect(fetcher).toHaveBeenCalledWith(
      "https://jarvis.test/v1/proactivity/019fd977-1d96-7892-950c-6afbb71f7cfd/feedback",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer private-token" }),
        body: '{"feedback":"snooze"}',
      }),
    );
  });
});
