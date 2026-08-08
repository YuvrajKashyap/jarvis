const TARGET_SAMPLE_RATE = 16_000;

export class PhoneMicrophone {
  #stream: MediaStream | null = null;
  #context: AudioContext | null = null;
  #node: AudioWorkletNode | null = null;
  #mutedOutput: GainNode | null = null;

  async start(onPcm: (pcm: ArrayBuffer) => void): Promise<void> {
    if (this.#stream) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    try {
      const context = new AudioContext({
        sampleRate: TARGET_SAMPLE_RATE,
        latencyHint: "interactive",
      });
      await context.audioWorklet.addModule("/pcm-capture.js");
      const source = context.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(context, "jarvis-pcm-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        channelCount: 1,
      });
      const mutedOutput = context.createGain();
      mutedOutput.gain.value = 0;
      node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => onPcm(event.data);
      source.connect(node);
      node.connect(mutedOutput);
      mutedOutput.connect(context.destination);
      if (context.state === "suspended") await context.resume();
      this.#stream = stream;
      this.#context = context;
      this.#node = node;
      this.#mutedOutput = mutedOutput;
    } catch (error) {
      for (const track of stream.getTracks()) track.stop();
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.#node?.disconnect();
    this.#node = null;
    this.#mutedOutput?.disconnect();
    this.#mutedOutput = null;
    for (const track of this.#stream?.getTracks() ?? []) track.stop();
    this.#stream = null;
    if (this.#context) await this.#context.close();
    this.#context = null;
  }
}
