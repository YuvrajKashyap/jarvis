import { describe, expect, it, vi } from "vitest";

import { PhoneSpeaker } from "./phone-speaker";

class FakeBuffer {
  readonly duration: number;
  readonly samples: Float32Array;

  constructor(length: number, sampleRate: number) {
    this.duration = length / sampleRate;
    this.samples = new Float32Array(length);
  }

  copyToChannel(source: Float32Array) {
    this.samples.set(source);
  }
}

class FakeSource {
  buffer: FakeBuffer | null = null;
  onended: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn();
  connect = vi.fn();
}

class FakeContext {
  currentTime = 1;
  state: AudioContextState = "suspended";
  destination = {};
  sources: FakeSource[] = [];
  resume = vi.fn(async () => {
    this.state = "running";
  });
  close = vi.fn(async () => undefined);

  createBuffer(_channels: number, length: number, sampleRate: number) {
    return new FakeBuffer(length, sampleRate);
  }

  createBufferSource() {
    const source = new FakeSource();
    this.sources.push(source);
    return source;
  }
}

describe("PhoneSpeaker", () => {
  it("unlocks on the user gesture and schedules fixed-rate PCM without gaps", async () => {
    const context = new FakeContext();
    const speaker = new PhoneSpeaker(() => context as unknown as AudioContext);
    await speaker.start();
    await speaker.play(new Int16Array([32767, -32768]).buffer);
    await speaker.play(new Int16Array([0, 16384]).buffer);

    expect(context.resume).toHaveBeenCalledOnce();
    expect(context.sources[0]?.start).toHaveBeenCalledWith(1);
    expect(context.sources[1]?.start).toHaveBeenCalledWith(1 + 2 / 24_000);
    expect(context.sources[0]?.buffer?.samples[0]).toBeCloseTo(32767 / 32768);
    expect(context.sources[0]?.buffer?.samples[1]).toBe(-1);

    await speaker.cancel();
    expect(context.sources.every((source) => source.stop.mock.calls.length === 1)).toBe(true);
  });
});
