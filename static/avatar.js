const VISEME_SHAPE = {
  0: { open: 1, wide: 1 },
  1: { open: 1.28, wide: 1.06 },
  2: { open: 1.52, wide: 1.04 },
  3: { open: 1.36, wide: 0.94 },
  4: { open: 1.18, wide: 1.16 },
  5: { open: 1.16, wide: 1.02 },
  6: { open: 1.12, wide: 1.2 },
  7: { open: 1.22, wide: 0.74 },
  8: { open: 1.34, wide: 0.82 },
  9: { open: 1.42, wide: 0.9 },
  10: { open: 1.32, wide: 0.86 },
  11: { open: 1.3, wide: 1.08 },
  12: { open: 1.08, wide: 1.02 },
  13: { open: 1.1, wide: 0.98 },
  14: { open: 1.12, wide: 1.08 },
  15: { open: 1.06, wide: 1.12 },
  16: { open: 1.14, wide: 0.88 },
  17: { open: 1.1, wide: 1.1 },
  18: { open: 1.04, wide: 1.14 },
  19: { open: 1.08, wide: 1.04 },
  20: { open: 1.08, wide: 1.02 },
  21: { open: 0.78, wide: 0.94 },
};

const FACES = {
  maya: {
    src: "/static/avatars/maya.png?v=9",
    mouth: { x: 50.4, y: 31.45, w: 10.6, h: 3.8 },
    eyes: { y: 22.4, l: 43.7, r: 57.2, w: 6.0, h: 2.8 },
  },
  noah: {
    src: "/static/avatars/noah.png?v=9",
    mouth: { x: 50.25, y: 32.95, w: 11.4, h: 3.6 },
    eyes: { y: 23.0, l: 43.4, r: 57.0, w: 5.8, h: 2.6 },
  },
  priya: {
    src: "/static/avatars/priya.png?v=9",
    mouth: { x: 52.05, y: 28.95, w: 10.8, h: 3.7 },
    eyes: { y: 21.6, l: 44.8, r: 59.0, w: 5.8, h: 2.6 },
  },
};

function markup(id) {
  const face = FACES[id] || FACES.maya;
  const { eyes: e } = face;
  return `
    <div class="avatar-puppet">
      <canvas class="avatar-art" aria-hidden="true"></canvas>
      <div class="face-rig">
        <span class="lid lid-l" style="left:${e.l}%;top:${e.y}%;width:${e.w}%;height:${e.h}%"></span>
        <span class="lid lid-r" style="left:${e.r}%;top:${e.y}%;width:${e.w}%;height:${e.h}%"></span>
      </div>
    </div>
  `;
}

const Avatar = {
  root: null,
  canvas: null,
  ctx: null,
  img: null,
  face: FACES.maya,
  shape: VISEME_SHAPE[0],
  skin: "#e2b496",
  init(node, avatarId) {
    this.root = node;
    this.render(avatarId || "maya");
    setInterval(() => this.blink(), 3600 + Math.random() * 1400);
  },
  render(avatarId) {
    this.root.dataset.avatar = avatarId;
    this.face = FACES[avatarId] || FACES.maya;
    this.root.innerHTML = markup(avatarId);
    this.canvas = this.root.querySelector("canvas");
    this.ctx = this.canvas.getContext("2d", { alpha: true });
    this.shape = VISEME_SHAPE[0];
    const img = new Image();
    this.img = img;
    img.onload = () => {
      if (this.img !== img) return;
      this.canvas.width = img.naturalWidth;
      this.canvas.height = img.naturalHeight;
      this.sampleSkin();
      this.draw();
    };
    img.src = this.face.src;
  },
  sampleSkin() {
    const { mouth: m } = this.face;
    const x = Math.max(0, Math.min(this.img.naturalWidth - 1, Math.round((m.x / 100) * this.img.naturalWidth)));
    const y = Math.max(0, Math.min(this.img.naturalHeight - 1, Math.round(((m.y - m.h * 0.72) / 100) * this.img.naturalHeight)));
    this.ctx.drawImage(this.img, 0, 0);
    const pixel = this.ctx.getImageData(x, y, 1, 1).data;
    this.skin = `rgb(${pixel[0]}, ${pixel[1]}, ${pixel[2]})`;
  },
  setState(next) {
    this.root.dataset.state = next;
    if (next === "listening") this.setExpression("listening");
    if (next === "thinking") this.setExpression("thinking");
    if (next === "idle") this.setExpression("happy");
  },
  setExpression(name) {
    this.root.dataset.expression = name;
  },
  setViseme(id) {
    this.shape = VISEME_SHAPE[id] ?? VISEME_SHAPE[0];
    this.draw();
  },
  draw() {
    if (!this.ctx || !this.img || !this.img.complete || !this.img.naturalWidth) return;
    const ctx = this.ctx;
    const img = this.img;
    const cw = this.canvas.width;
    const ch = this.canvas.height;
    ctx.clearRect(0, 0, cw, ch);
    ctx.drawImage(img, 0, 0, cw, ch);

    const { open, wide } = this.shape;
    if (this.root.dataset.state !== "speaking") return;
    if (Math.abs(open - 1) < 0.03 && Math.abs(wide - 1) < 0.03) return;

    const m = this.face.mouth;
    const cx = (m.x / 100) * cw;
    const cy = (m.y / 100) * ch;
    const mw = (m.w / 100) * cw;
    const mh = (m.h / 100) * ch;
    const originY = cy - mh * 0.3;

    ctx.save();
    ctx.beginPath();
    ctx.ellipse(cx, cy, mw * 0.52, mh * 0.58, 0, 0, Math.PI * 2);
    ctx.fillStyle = this.skin;
    ctx.fill();
    ctx.restore();

    ctx.save();
    ctx.beginPath();
    ctx.ellipse(cx, originY + (cy - originY) * open, (mw / 2) * wide, (mh / 2) * Math.max(open, 0.7), 0, 0, Math.PI * 2);
    ctx.clip();
    ctx.translate(cx, originY);
    ctx.scale(wide, open);
    ctx.translate(-cx, -originY);
    ctx.drawImage(img, 0, 0, cw, ch);
    ctx.restore();
  },
  blink() {
    if (!this.root || this.root.dataset.state === "speaking") return;
    this.root.classList.add("blink");
    setTimeout(() => this.root.classList.remove("blink"), 160);
  },
};

window.Avatar = Avatar;
