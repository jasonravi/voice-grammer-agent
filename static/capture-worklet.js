class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(4096);
    this._offset = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    const room = this._buf.length - this._offset;
    if (channel.length >= room) {
      this._buf.set(channel.subarray(0, room), this._offset);
      let sum = 0;
      for (let i = 0; i < this._buf.length; i += 1) sum += this._buf[i] * this._buf[i];
      this.port.postMessage({
        pcm: this._buf.slice(),
        rms: Math.sqrt(sum / this._buf.length),
      });
      this._offset = 0;
      const rest = channel.subarray(room);
      if (rest.length) {
        this._buf.set(rest, 0);
        this._offset = rest.length;
      }
    } else {
      this._buf.set(channel, this._offset);
      this._offset += channel.length;
    }
    return true;
  }
}

registerProcessor("pcm-capture", CaptureProcessor);
