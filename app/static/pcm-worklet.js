// Downsamples microphone audio to 16 kHz mono and posts 16-bit PCM chunks
// (about 100 ms each) plus an RMS level for the input meter.
const TARGET_RATE = 16000;
const CHUNK_SAMPLES = 1600;

class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.pos = 0; // fractional read position into the incoming stream
    this.out = new Int16Array(CHUNK_SAMPLES);
    this.outLen = 0;
    this.sumSq = 0;
    this.prev = 0; // last sample of the previous block, for interpolation
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channels = input.length;
    const n = input[0].length;

    // Mix down to mono.
    const mono = new Float32Array(n);
    for (let c = 0; c < channels; c++) {
      const data = input[c];
      for (let i = 0; i < n; i++) mono[i] += data[i] / channels;
    }

    // Linear interpolation resampler.
    while (this.pos < n) {
      const i = Math.floor(this.pos);
      const frac = this.pos - i;
      const a = i === 0 ? this.prev : mono[i - 1];
      const b = mono[i];
      const s = Math.max(-1, Math.min(1, a + (b - a) * frac));
      this.out[this.outLen++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      this.sumSq += s * s;
      if (this.outLen === CHUNK_SAMPLES) {
        const level = Math.sqrt(this.sumSq / CHUNK_SAMPLES);
        this.port.postMessage({ pcm: this.out.buffer, level }, [this.out.buffer]);
        this.out = new Int16Array(CHUNK_SAMPLES);
        this.outLen = 0;
        this.sumSq = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= n;
    this.prev = mono[n - 1];
    return true;
  }
}

registerProcessor("pcm-processor", PcmProcessor);
