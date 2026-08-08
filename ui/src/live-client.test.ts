import { describe, expect, it, vi } from "vitest";

import { LiveClient, type SocketLike } from "./live-client";

class FakeSocket implements SocketLike {
  readyState = 0;
  binaryType: BinaryType = "blob";
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string | ArrayBuffer>) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readonly sent: Array<string | ArrayBuffer> = [];

  send(value: string | ArrayBuffer) {
    this.sent.push(value);
  }

  close() {
    this.readyState = 3;
  }
}

describe("LiveClient", () => {
  it("authenticates without putting the token in the URL and sends typed actions", () => {
    const socket = new FakeSocket();
    const factory = vi.fn((_url: string, _protocols: string[]) => socket);
    const connections: string[] = [];
    const client = new LiveClient({
      url: "ws://127.0.0.1:7331/v1/live",
      authProtocol: "jarvis.desktop.secret-token",
      deviceId: "desktop",
      socketFactory: factory,
      onConnection: (state) => connections.push(state),
      onEvent: () => undefined,
    });

    client.start();
    socket.readyState = 1;
    socket.onopen?.(new Event("open"));
    client.activate("keyboard");
    client.submitText("What am I looking at?");
    client.decideApproval("019fd977-1d96-7892-950c-6afbb71f7cf0", "approve");

    expect(factory).toHaveBeenCalledWith("ws://127.0.0.1:7331/v1/live", [
      "jarvis.v1",
      "jarvis.desktop.secret-token",
    ]);
    expect(factory.mock.calls[0]?.[0]).not.toContain("secret-token");
    expect(connections).toEqual(["reconnecting", "connected"]);
    expect(
      socket.sent
        .filter((event): event is string => typeof event === "string")
        .map((event) => JSON.parse(event).type),
    ).toEqual(["activate", "submit_text", "approval_decision"]);
  });

  it("rejects invalid server events instead of applying untrusted payloads", () => {
    const socket = new FakeSocket();
    const events = vi.fn();
    const client = new LiveClient({
      url: "wss://jarvis.tailnet.ts.net/v1/live",
      authProtocol: "jarvis.session.session-token",
      deviceId: "phone",
      socketFactory: () => socket,
      onConnection: () => undefined,
      onEvent: events,
    });

    client.start();
    socket.onmessage?.(new MessageEvent("message", { data: '{"type":"forged"}' }));

    expect(events).not.toHaveBeenCalled();
  });

  it("routes bounded server PCM separately from JSON events", () => {
    const socket = new FakeSocket();
    const audio = vi.fn();
    const events = vi.fn();
    const client = new LiveClient({
      url: "wss://jarvis.tailnet.ts.net/v1/live",
      authProtocol: "jarvis.session.session-token",
      deviceId: "phone",
      socketFactory: () => socket,
      onConnection: () => undefined,
      onEvent: events,
      onAudio: audio,
    });

    client.start();
    const pcm = new Int16Array([1, -1]).buffer;
    socket.onmessage?.(new MessageEvent("message", { data: pcm }));

    expect(socket.binaryType).toBe("arraybuffer");
    expect(audio).toHaveBeenCalledWith(pcm);
    expect(events).not.toHaveBeenCalled();
  });
});
