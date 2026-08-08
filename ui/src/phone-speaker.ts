const SERVER_SAMPLE_RATE = 24_000;

type AudioContextFactory = () => AudioContext;

export class PhoneSpeaker {
  readonly #contextFactory: AudioContextFactory;
  #context: AudioContext | null = null;
  #cursor = 0;
  #sources = new Set<AudioBufferSourceNode>();

  constructor(
    contextFactory: AudioContextFactory = () => new AudioContext({ latencyHint: "interactive" }),
  ) {
    this.#contextFactory = contextFactory;
  }

  async start(): Promise<void> {
    const context = this.#context ?? this.#contextFactory();
    this.#context = context;
    this.#cursor = Math.max(this.#cursor, context.currentTime);
    if (context.state === "suspended") await context.resume();
  }

  async play(pcm: ArrayBuffer): Promise<void> {
    if (pcm.byteLength === 0 || pcm.byteLength % 2 !== 0) return;
    await this.start();
    const context = this.#context;
    if (!context) return;
    const input = new Int16Array(pcm);
    const samples = new Float32Array(input.length);
    for (let index = 0; index < input.length; index += 1) {
      samples[index] = (input[index] ?? 0) / 32_768;
    }
    const buffer = context.createBuffer(1, samples.length, SERVER_SAMPLE_RATE);
    buffer.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    this.#sources.add(source);
    source.onended = () => this.#sources.delete(source);
    const startAt = Math.max(context.currentTime, this.#cursor);
    source.start(startAt);
    this.#cursor = startAt + buffer.duration;
  }

  async cancel(): Promise<void> {
    for (const source of this.#sources) {
      try {
        source.stop();
      } catch {
        // A source that ended between iteration and stop is already cancelled.
      }
    }
    this.#sources.clear();
    this.#cursor = this.#context?.currentTime ?? 0;
  }

  async close(): Promise<void> {
    await this.cancel();
    if (this.#context) await this.#context.close();
    this.#context = null;
  }
}
