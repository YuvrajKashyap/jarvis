class JarvisPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.output = [];
    this.sourcePosition = 0;
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    const ratio = sampleRate / 16000;
    while (this.sourcePosition < input.length) {
      this.output.push(input[Math.floor(this.sourcePosition)] ?? 0);
      this.sourcePosition += ratio;
    }
    this.sourcePosition -= input.length;

    while (this.output.length >= 512) {
      const pcm = new Int16Array(512);
      for (let index = 0; index < pcm.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, this.output[index] ?? 0));
        pcm[index] = sample < 0 ? sample * 32768 : sample * 32767;
      }
      this.output.splice(0, pcm.length);
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("jarvis-pcm-capture", JarvisPcmCapture);
