import { beforeEach, describe, expect, it, vi } from "vitest";

import { PhoneMicrophone } from "./phone-audio";

const stopTrack = vi.fn();
const resume = vi.fn(async () => undefined);
const close = vi.fn(async () => undefined);
const addModule = vi.fn(async () => undefined);
const sourceConnect = vi.fn();
const nodeConnect = vi.fn();
const nodeDisconnect = vi.fn();
const gainConnect = vi.fn();
const gainDisconnect = vi.fn();
const gain = { gain: { value: 1 }, connect: gainConnect, disconnect: gainDisconnect };

class FakeAudioContext {
  state = "suspended";
  destination = {};
  audioWorklet = { addModule };
  createMediaStreamSource = vi.fn(() => ({ connect: sourceConnect }));
  createGain = vi.fn(() => gain);
  resume = resume;
  close = close;
}

class FakeAudioWorkletNode {
  port = { onmessage: null };
  connect = nodeConnect;
  disconnect = nodeDisconnect;
}

describe("PhoneMicrophone", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: stopTrack }] })),
      },
    });
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
  });

  it("resumes iOS audio and keeps the capture worklet alive through a muted output", async () => {
    const microphone = new PhoneMicrophone();

    await microphone.start(vi.fn());

    expect(resume).toHaveBeenCalledOnce();
    expect(gain.gain.value).toBe(0);
    expect(nodeConnect).toHaveBeenCalledWith(gain);
    await microphone.stop();
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(gainDisconnect).toHaveBeenCalledOnce();
  });
});
