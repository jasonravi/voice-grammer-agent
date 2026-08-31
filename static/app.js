const PROFILE_KEY = "lumen.profile";
const LEVELS = {
  beginner: { label: "Beginner", path: "A1 → A2" },
  intermediate: { label: "Intermediate", path: "B1 → B2" },
  advanced: { label: "Advanced", path: "B2 → C1" },
  proficient: { label: "Proficient", path: "C1 → C2" },
};
const GOALS = {
  speak: { label: "Speak Confidently", mode: "free" },
  interview: { label: "Interview Preparation", mode: "interview" },
  career: { label: "Career Growth", mode: "workplace" },
  travel: { label: "Travel", mode: "travel" },
  study: { label: "Study Abroad", mode: "daily" },
  other: { label: "Other", mode: "free" },
};

const els = {
  appShell: document.getElementById("appShell"),
  status: document.getElementById("status"),
  caption: document.getElementById("caption"),
  hindi: document.getElementById("hindiLine"),
  liveUser: document.getElementById("liveUser"),
  talk: document.getElementById("talk"),
  listenHint: document.getElementById("listenHint"),
  player: document.getElementById("player"),
  stage: document.getElementById("stage"),
  wave: document.getElementById("wave"),
  chatLog: document.getElementById("chatLog"),
  tutorName: document.getElementById("tutorName"),
  dailyRange: document.getElementById("dailyRange"),
  dailyValue: document.getElementById("dailyValue"),
  dialArc: document.getElementById("dialArc"),
  homeHello: document.getElementById("homeHello"),
  homeProgress: document.getElementById("homeProgress"),
  homeGoal: document.getElementById("homeGoal"),
  nameInput: document.getElementById("nameInput"),
};

const state = {
  avatars: [],
  modes: [],
  avatar: "maya",
  mode: "free",
  sessionId: "",
  listening: false,
  busy: false,
  ignoringSelf: false,
  recognition: null,
  stream: null,
  audioCtx: null,
  processor: null,
  buffers: [],
  speaking: false,
  silent: 0,
  silentMs: 0,
  speechMs: 0,
  sampleRate: 48000,
  finalText: "",
  lastText: "",
  micUnlockBound: false,
  ws: null,
  wsWait: null,
  lastTurn: null,
  profile: loadProfile(),
};

function loadProfile() {
  try {
    return {
      name: "Ravi",
      level: "intermediate",
      goal: "speak",
      dailyMin: 15,
      onboarded: false,
      ...JSON.parse(localStorage.getItem(PROFILE_KEY) || "{}"),
    };
  } catch (err) {
    return { name: "Ravi", level: "intermediate", goal: "speak", dailyMin: 15, onboarded: false };
  }
}

function saveProfile() {
  localStorage.setItem(PROFILE_KEY, JSON.stringify(state.profile));
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function greeting() {
  const hour = new Date().getHours();
  const when = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  return `${when}, ${state.profile.name || "Ravi"} 👋`;
}

function showScreen(name) {
  document.querySelectorAll(".screen").forEach((screen) => {
    screen.classList.toggle("on", screen.dataset.screen === name);
  });
  const onboard = ["welcome", "level", "goal", "daily"].includes(name);
  const focus = ["tutor", "correction", "result"].includes(name);
  els.appShell.dataset.flow = onboard ? "onboard" : focus ? "tutor" : "main";
  document.querySelectorAll(".tabbar .tab").forEach((tab) => {
    tab.classList.toggle("on", tab.dataset.tab === name);
  });
}

function armMicUnlock() {
  if (state.micUnlockBound) return;
  state.micUnlockBound = true;
  const unlock = () => {
    document.removeEventListener("pointerdown", unlock);
    state.micUnlockBound = false;
    if (!state.busy && state.sessionId) startListening();
  };
  document.addEventListener("pointerdown", unlock);
}

function form(data) {
  const body = new FormData();
  Object.entries(data).forEach(([key, value]) => {
    if (value !== undefined && value !== null) body.append(key, value);
  });
  return body;
}

async function api(url, body, timeoutMs = 25000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { method: "POST", body, signal: controller.signal });
    const data = await res.json().catch(() => ({}));
    const detail = data.detail;
    const message = Array.isArray(detail) ? detail.map((item) => item.msg || item).join(" ") : (detail || "Request failed");
    if (!res.ok) throw new Error(message);
    return data;
  } catch (err) {
    if (err.name === "AbortError") throw new Error("That took too long. Just say it again.");
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",")[1] || "");
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function connectTutorSocket() {
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/tutor`);
  state.ws = ws;
  ws.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch (err) { return; }
    if (data.type === "status") {
      if (data.status === "transcribing") setStatus("Hearing you…");
      if (data.status === "thinking") setStatus("Thinking...");
      return;
    }
    if (!state.wsWait) return;
    const waiters = state.wsWait;
    state.wsWait = null;
    clearTimeout(waiters.timer);
    if (data.ok === false || data.type === "error") waiters.reject(new Error(data.detail || "Request failed"));
    else waiters.resolve(data);
  };
  ws.onclose = () => { if (state.ws === ws) state.ws = null; };
}

function tutorRequest(payload, timeoutMs = 25000) {
  return new Promise((resolve, reject) => {
    const send = () => {
      if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
        reject(new Error("socket closed"));
        return;
      }
      state.wsWait = {
        resolve,
        reject,
        timer: setTimeout(() => {
          state.wsWait = null;
          reject(new Error("That took too long. Just say it again."));
        }, timeoutMs),
      };
      state.ws.send(JSON.stringify(payload));
    };
    connectTutorSocket();
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      send();
      return;
    }
    const started = Date.now();
    const tick = () => {
      if (state.ws && state.ws.readyState === WebSocket.OPEN) send();
      else if (Date.now() - started > 4000) reject(new Error("socket timeout"));
      else setTimeout(tick, 60);
    };
    tick();
  });
}

async function tutorCall(kind, fields) {
  try {
    const payload = { type: kind, ...fields };
    delete payload.audioBlob;
    return await tutorRequest(payload);
  } catch (err) {
    if (kind === "start") return api("/api/session/start", form({ mode: fields.mode, avatar: fields.avatar }));
    const body = form({
      text: fields.text || "",
      session_id: fields.session_id,
      mode: fields.mode,
      avatar: fields.avatar,
      spoken: fields.spoken === false ? "false" : "true",
    });
    if (fields.audioBlob) body.append("audio", fields.audioBlob, "speech.wav");
    return api("/api/tutor/turn", body);
  }
}

function currentAvatar() {
  return state.avatars.find((item) => item.id === state.avatar) || { name: "Maya", title: "" };
}

function setStatus(text) {
  if (els.status) els.status.textContent = text;
}

function addLog(role, text) {
  if (!els.chatLog || !text) return;
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role === "bot" ? "bot" : "user"}`;
  bubble.textContent = text;
  els.chatLog.appendChild(bubble);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
  while (els.chatLog.children.length > 6) els.chatLog.firstChild.remove();
}

function setListeningUi(on) {
  if (els.stage) els.stage.classList.toggle("listening", on);
  if (els.talk) els.talk.classList.toggle("hot", on);
  if (els.listenHint) els.listenHint.textContent = on ? "Listening... pause when you finish" : "Tap to speak";
}

function paintWave(rms) {
  if (!els.wave) return;
  els.wave.querySelectorAll("span").forEach((bar, index) => {
    const lift = rms > 0.01 ? 10 + (index % 5) * 3 : 2;
    bar.style.height = `${Math.max(4, Math.min(28, rms * 380 + lift))}px`;
  });
}

function visemeAt(visemes, timeMs) {
  let current = 0;
  for (const item of visemes || []) {
    if (item.offset_ms <= timeMs) current = item.id;
    else break;
  }
  return current;
}

function encodeWav(buffers, sampleRate) {
  const length = buffers.reduce((sum, buf) => sum + buf.length, 0);
  const pcm = new Int16Array(length);
  let offset = 0;
  buffers.forEach((buf) => {
    for (let i = 0; i < buf.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, buf[i]));
      pcm[offset] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      offset += 1;
    }
  });
  const bytes = pcm.byteLength;
  const view = new DataView(new ArrayBuffer(44 + bytes));
  const write = (pos, text) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(pos + i, text.charCodeAt(i));
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + bytes, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, bytes, true);
  new Uint8Array(view.buffer).set(new Uint8Array(pcm.buffer), 44);
  return new Blob([view.buffer], { type: "audio/wav" });
}

function estimateSpeechMs(text, rate = 1) {
  const words = (text.trim().match(/\S+/g) || []).length;
  return Math.max(800, (words * 360 + 220) / rate);
}

function visemesFromText(text, durationMs) {
  const map = {
    a: 2, e: 4, i: 6, o: 8, u: 7, y: 6,
    m: 21, b: 21, p: 21, f: 18, v: 18, w: 7, q: 7,
    s: 15, z: 15, l: 14, r: 13, t: 19, d: 19, n: 19,
  };
  const units = [];
  for (const ch of text.toLowerCase()) {
    if (map[ch] !== undefined) units.push(map[ch]);
    else if (" .,!?".includes(ch)) units.push(0);
    else if (/[a-z]/.test(ch)) units.push(12);
  }
  if (!units.length) units.push(2);
  const total = Math.max(durationMs || units.length * 80, units.length * 50);
  const step = total / units.length;
  return units.map((id, index) => ({ id, offset_ms: index * step }));
}

function pickVoice(avatarId) {
  const voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
  const prefer = avatarId === "priya"
    ? ["neerja", "veena", "indian"]
    : avatarId === "noah"
      ? ["daniel", "alex", "google uk english male", "david"]
      : ["samantha", "karen", "moira", "google us english", "samantha"];
  const lower = (name) => (name || "").toLowerCase();
  return (
    voices.find((voice) => prefer.some((item) => lower(voice.name).includes(item)))
    || voices.find((voice) => /female|samantha|google us english/i.test(voice.name))
    || voices[0]
    || null
  );
}

function playReply(payload) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(watchdog);
      Avatar.setViseme(0);
      Avatar.setState("idle");
      state.ignoringSelf = false;
      resolve();
    };
    const watchdog = setTimeout(finish, 12000);
    const text = payload.reply || "";
    els.caption.textContent = text;
    Avatar.setExpression(payload.expression || "happy");
    Avatar.setState("speaking");
    state.ignoringSelf = true;
    setStatus("Speaking...");

    if (payload.audio_url) {
      els.player.src = payload.audio_url;
      let raf;
      const tick = () => {
        Avatar.setViseme(visemeAt(payload.visemes || [], els.player.currentTime * 1000));
        raf = requestAnimationFrame(tick);
      };
      const done = () => { cancelAnimationFrame(raf); finish(); };
      els.player.onended = done;
      els.player.onerror = done;
      els.player.play().then(() => { raf = requestAnimationFrame(tick); }).catch(done);
      return;
    }
    if (!window.speechSynthesis || !text) { finish(); return; }
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    const voice = pickVoice(state.avatar);
    if (voice) utterance.voice = voice;
    utterance.rate = 1.06;
    utterance.pitch = state.avatar === "noah" ? 0.9 : 1.04;
    const visemes = visemesFromText(text, estimateSpeechMs(text, utterance.rate));
    let lip;
    let started = 0;
    const tickLips = () => {
      if (started) Avatar.setViseme(visemeAt(visemes, performance.now() - started));
      lip = requestAnimationFrame(tickLips);
    };
    utterance.onstart = () => { started = performance.now(); };
    utterance.onend = utterance.onerror = () => { cancelAnimationFrame(lip); finish(); };
    lip = requestAnimationFrame(tickLips);
    speechSynthesis.speak(utterance);
  });
}

function stopCaptureGraph() {
  if (state.processor) {
    try { state.processor.port.onmessage = null; } catch (err) { /* ignore */ }
    try { state.processor.disconnect(); } catch (err) { /* already down */ }
    state.processor = null;
  }
  if (state.audioCtx) {
    try { state.audioCtx.close(); } catch (err) { /* already closed */ }
    state.audioCtx = null;
  }
  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
  }
  paintWave(0);
}

const SPEECH_START_RMS = 0.003;
const SPEECH_HOLD_RMS = 0.001;
const MIN_SPEECH_MS = 600;
const MAX_UTTERANCE_SEC = 16;

function endSilenceFor(speechMs) {
  return speechMs >= 1600 ? 1400 : 2000;
}

function shouldSendUtterance(speechMs, silentMs) {
  return speechMs >= MIN_SPEECH_MS && silentMs >= endSilenceFor(speechMs);
}

function dropQuietTail(buffers) {
  while (buffers.length > 1) {
    const last = buffers[buffers.length - 1];
    let peak = 0;
    for (let i = 0; i < last.length; i += 1) peak = Math.max(peak, Math.abs(last[i]));
    if (peak > SPEECH_HOLD_RMS) break;
    buffers.pop();
  }
  return buffers;
}

function onPcmFrame(payload) {
  if (!state.listening || state.busy || state.ignoringSelf) return;
  const pcm = payload.pcm;
  const rms = payload.rms;
  paintWave(rms);
  const frameMs = (pcm.length / Math.max(state.sampleRate, 1)) * 1000;
  const maxSamples = state.sampleRate * MAX_UTTERANCE_SEC;
  let total = state.buffers.reduce((sum, buf) => sum + buf.length, 0);
  while (total > maxSamples && state.buffers.length) total -= state.buffers.shift().length;
  state.buffers.push(new Float32Array(pcm));
  total += pcm.length;
  const loud = rms > (state.speaking ? SPEECH_HOLD_RMS : SPEECH_START_RMS);
  if (loud) {
    state.speaking = true;
    state.speechMs += frameMs;
    state.silentMs = 0;
    Avatar.setState("listening");
    setStatus("Listening...");
    if (els.liveUser && !els.liveUser.textContent) els.liveUser.textContent = "Hearing you…";
  } else if (state.speaking) {
    state.silentMs += frameMs;
    if (shouldSendUtterance(state.speechMs, state.silentMs)) flushUtterance();
  }
  if (state.speaking && state.speechMs >= MAX_UTTERANCE_SEC * 1000 && state.silentMs > 400) flushUtterance();
}

function flushUtterance() {
  const text = (state.lastText || state.finalText || "").trim();
  const blob = state.buffers.length ? encodeWav(dropQuietTail(state.buffers), state.sampleRate) : null;
  const heard = state.speaking;
  state.buffers = [];
  state.speaking = false;
  state.silentMs = 0;
  state.speechMs = 0;
  state.finalText = "";
  state.lastText = "";
  if (text) { sendTurn(text, true, null); return; }
  if (blob && blob.size > 8000 && heard) { sendTurn("", true, blob); return; }
  state.listening = true;
  setListeningUi(true);
  els.caption.textContent = "I missed that. Say it again.";
  setStatus("Listening...");
}

async function attachCapture(ctx, source) {
  try {
    await ctx.audioWorklet.addModule("/static/capture-worklet.js?v=14");
    const node = new AudioWorkletNode(ctx, "pcm-capture");
    const mute = ctx.createGain();
    mute.gain.value = 0;
    source.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
    node.port.onmessage = (event) => onPcmFrame(event.data);
    state.processor = node;
    return;
  } catch (err) {
    console.warn("AudioWorklet unavailable", err);
  }
  const node = ctx.createScriptProcessor(4096, 1, 1);
  node.onaudioprocess = (event) => {
    const pcm = event.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < pcm.length; i += 1) sum += pcm[i] * pcm[i];
    onPcmFrame({ pcm: new Float32Array(pcm), rms: Math.sqrt(sum / pcm.length) });
  };
  const mute = ctx.createGain();
  mute.gain.value = 0;
  source.connect(node);
  node.connect(mute);
  mute.connect(ctx.destination);
  state.processor = node;
}

async function ensureCapture() {
  if (state.processor && state.audioCtx && state.stream && state.stream.active) {
    try { await state.audioCtx.resume(); } catch (err) { /* ignore */ }
    return true;
  }
  stopCaptureGraph();
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    armMicUnlock();
    setStatus("Allow mic");
    els.caption.textContent = "Tap anywhere once to allow the microphone, then speak.";
    return false;
  }
  try {
    state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    await state.audioCtx.resume();
    state.sampleRate = state.audioCtx.sampleRate;
    await attachCapture(state.audioCtx, state.audioCtx.createMediaStreamSource(state.stream));
    return true;
  } catch (err) {
    stopCaptureGraph();
    armMicUnlock();
    els.caption.textContent = "Could not start the microphone.";
    return false;
  }
}

async function startListening() {
  if (state.listening || state.busy) return;
  if (state.ignoringSelf) {
    try { speechSynthesis.cancel(); } catch (err) { /* ignore */ }
    state.ignoringSelf = false;
  }
  if (!(await ensureCapture())) return;
  state.buffers = [];
  state.speaking = false;
  state.silentMs = 0;
  state.speechMs = 0;
  state.listening = true;
  setListeningUi(true);
  Avatar.setState("listening");
  setStatus("Listening...");
}

function stopListening() {
  state.listening = false;
  stopCaptureGraph();
  setListeningUi(false);
  Avatar.setState("idle");
}

function showCorrection(data) {
  const grammar = data.grammar || {};
  document.getElementById("fixOriginal").textContent = data.user_text || "—";
  document.getElementById("fixCorrected").textContent = grammar.corrected || data.user_text || "—";
  const why = (grammar.errors && grammar.errors[0] && grammar.errors[0].why) || data.hindi || "A clearer sentence helps listeners understand you.";
  document.getElementById("fixWhy").textContent = why;
  const hi = document.getElementById("fixHindi");
  hi.textContent = data.hindi || "";
  hi.hidden = !data.hindi;
  document.getElementById("fixTip").textContent = data.exercise || data.pronunciation_tip || "";
  showScreen("correction");
}

async function sendTurn(text, spoken = true, audioBlob = null) {
  const spokenText = (text || "").trim();
  if ((!spokenText && !audioBlob) || state.busy) return;
  state.busy = true;
  state.listening = false;
  setListeningUi(false);
  Avatar.setState("thinking");
  setStatus("Thinking...");
  if (spokenText) {
    els.liveUser.textContent = spokenText;
    addLog("user", spokenText);
  } else {
    els.liveUser.textContent = "Hearing you…";
  }
  try {
    const fields = {
      session_id: state.sessionId,
      mode: state.mode,
      avatar: state.avatar,
      spoken,
      text: spokenText,
      audioBlob,
    };
    if (!spokenText && audioBlob) fields.audio_b64 = await blobToBase64(audioBlob);
    const data = await tutorCall("turn", fields);
    state.lastTurn = data;
    const heard = data.user_text || spokenText;
    if (heard && !spokenText) addLog("user", heard);
    if (heard) els.liveUser.textContent = heard;
    if (data.hindi) {
      els.hindi.textContent = data.hindi;
      els.hindi.hidden = false;
    }
    addLog("bot", data.reply);
    await playReply(data);
    await wait(120);
    if (data.grammar && data.grammar.has_errors && data.grammar.corrected) {
      state.busy = false;
      showCorrection(data);
      return;
    }
  } catch (err) {
    els.caption.textContent = err.message;
    Avatar.setState("idle");
  } finally {
    state.busy = false;
    state.buffers = [];
  }
  if (state.sessionId && els.appShell.dataset.flow === "tutor") startListening();
}

async function startSession() {
  stopListening();
  setStatus("Starting…");
  Avatar.render(state.avatar);
  els.tutorName.textContent = currentAvatar().name;
  if (els.chatLog) els.chatLog.innerHTML = "";
  await ensureCapture();
  const data = await tutorCall("start", { mode: state.mode, avatar: state.avatar });
  state.sessionId = data.session_id;
  addLog("bot", data.reply);
  await playReply(data);
  await wait(120);
  els.caption.textContent = data.reply;
  await startListening();
}

async function launchTutor(mode) {
  state.mode = mode || GOALS[state.profile.goal]?.mode || "free";
  showScreen("tutor");
  await startSession();
}

function skillRows(scores) {
  return ["speaking", "grammar", "vocabulary", "pronunciation"].map((key) => {
    const value = scores[key] || scores.fluency || 0;
    const label = key[0].toUpperCase() + key.slice(1);
    const n = key === "speaking" ? (scores.fluency || value) : (scores[key] || 0);
    return `<div class="skill"><span>${label} ${n}%</span><div class="bar"><span style="width:${n}%"></span></div></div>`;
  }).join("");
}

function renderHome(progress) {
  els.homeHello.textContent = greeting();
  const weekly = progress?.windows?.weekly || {};
  const overall = Math.round(
    ((weekly.fluency || 0) + (weekly.grammar || 0) + (weekly.vocabulary || 0) + (weekly.pronunciation || 0)) / 4
  );
  els.homeProgress.textContent = `${overall || 0}%`;
  els.homeGoal.textContent = `${state.profile.dailyMin} min speaking practice`;
}

async function renderProgress() {
  const data = await fetch("/api/progress").then((res) => res.json());
  const weekly = data.windows?.weekly || {};
  const scores = {
    fluency: weekly.fluency || 0,
    grammar: weekly.grammar || 0,
    vocabulary: weekly.vocabulary || 0,
    pronunciation: weekly.pronunciation || 0,
  };
  document.getElementById("progressSkills").innerHTML = skillRows(scores);
  document.getElementById("levelPath").textContent = LEVELS[state.profile.level]?.path || "B1 → B2";
  document.getElementById("streakDays").textContent = Math.max(1, data.windows?.daily?.sessions || 0);
  document.getElementById("chatCount").textContent = data.windows?.monthly?.sessions || 0;
  const words = (data.mistakes || []).map((item) => item.pattern);
  document.getElementById("wordList").innerHTML = (data.trend || []).slice(-6).map((row) => `<li>${row.mode}</li>`).join("") || "<li>Complete a lesson to collect words.</li>";
  const wotd = ["Articulate", "Deadline", "Collaborate", "Prioritize"][(new Date().getDate()) % 4];
  document.getElementById("wordOfDay").textContent = wotd;
  renderHome(data);
  return data;
}

function renderResult(report) {
  const scores = report.scores || {};
  const overall = Math.round(
    ((scores.fluency || 0) + (scores.grammar || 0) + (scores.vocabulary || 0) + (scores.pronunciation || 0)) / 4
  );
  document.getElementById("resultTitle").textContent = `Great job, ${state.profile.name}!`;
  document.getElementById("resultOverall").textContent = `${overall}%`;
  document.getElementById("resultOverall").parentElement.style.setProperty("--p", overall);
  document.getElementById("resultSkills").innerHTML = skillRows(scores);
  const words = report.vocabulary || [];
  document.getElementById("resultWords").innerHTML = words.slice(0, 6).map((word) => `<li>${word}</li>`).join("") || "<li>None yet</li>";
  const mistakes = report.common_mistakes || [];
  document.getElementById("resultMistakes").innerHTML = mistakes.slice(0, 6).map((item) => `<li>${item.pattern}</li>`).join("") || "<li>None this session</li>";
  showScreen("result");
}

function bindChoices() {
  document.querySelectorAll("[data-level]").forEach((button) => {
    button.addEventListener("click", () => {
      state.profile.level = button.dataset.level;
      document.querySelectorAll("[data-level]").forEach((item) => item.classList.toggle("on", item === button));
    });
  });
  document.querySelectorAll("[data-goal]").forEach((button) => {
    button.addEventListener("click", () => {
      state.profile.goal = button.dataset.goal;
      state.mode = button.dataset.mode;
      document.querySelectorAll("[data-goal]").forEach((item) => item.classList.toggle("on", item === button));
    });
  });
  document.querySelectorAll("[data-next]").forEach((button) => {
    button.addEventListener("click", () => showScreen(button.dataset.next));
  });
  document.querySelectorAll("[data-go]").forEach((button) => {
    button.addEventListener("click", () => {
      const name = button.dataset.go;
      showScreen(name);
      if (name === "progress" || name === "home") renderProgress();
    });
  });
  document.querySelectorAll("[data-launch]").forEach((button) => {
    button.addEventListener("click", () => launchTutor(button.dataset.launch));
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const name = button.dataset.tab;
      if (name === "tutor") {
        launchTutor(GOALS[state.profile.goal]?.mode || "free");
        return;
      }
      showScreen(name);
      if (name === "progress" || name === "vocab" || name === "home") renderProgress();
    });
  });
}

function updateDial(minutes) {
  els.dailyValue.textContent = minutes;
  const offset = 327 * (1 - minutes / 60);
  els.dialArc.style.strokeDashoffset = String(offset);
}

bindChoices();
document.getElementById("getStarted").addEventListener("click", () => showScreen("level"));
document.getElementById("haveAccount").addEventListener("click", () => {
  state.profile.onboarded = true;
  saveProfile();
  showScreen("home");
  renderProgress();
});
document.getElementById("letsBegin").addEventListener("click", () => {
  state.profile.dailyMin = Number(els.dailyRange.value);
  state.profile.onboarded = true;
  state.mode = GOALS[state.profile.goal]?.mode || "free";
  saveProfile();
  showScreen("home");
  renderProgress();
});
els.dailyRange.addEventListener("input", () => {
  state.profile.dailyMin = Number(els.dailyRange.value);
  updateDial(state.profile.dailyMin);
});
document.getElementById("tutorBack").addEventListener("click", () => {
  stopListening();
  showScreen("home");
  renderProgress();
});
document.getElementById("gotIt").addEventListener("click", () => {
  showScreen("tutor");
  startListening();
});
document.getElementById("viewDetails").addEventListener("click", () => {
  showScreen("progress");
  renderProgress();
});
document.getElementById("end").addEventListener("click", async () => {
  if (!state.sessionId) {
    showScreen("home");
    return;
  }
  stopListening();
  const report = await api("/api/session/end", form({ session_id: state.sessionId }));
  state.sessionId = "";
  renderResult(report);
});
document.getElementById("resetOnboard").addEventListener("click", () => {
  state.profile.onboarded = false;
  saveProfile();
  showScreen("welcome");
});
els.nameInput.addEventListener("input", () => {
  state.profile.name = els.nameInput.value.trim() || "Ravi";
  saveProfile();
  document.getElementById("profileName").textContent = state.profile.name;
  els.homeHello.textContent = greeting();
});
els.talk.addEventListener("click", () => {
  if (state.busy) return;
  if (state.listening) {
    flushUtterance();
    return;
  }
  startListening();
});

async function boot() {
  if (window.speechSynthesis) {
    speechSynthesis.getVoices();
    speechSynthesis.onvoiceschanged = () => speechSynthesis.getVoices();
  }
  Avatar.init(document.getElementById("avatarRoot"), "maya");
  if (els.wave) els.wave.innerHTML = Array.from({ length: 18 }, () => "<span></span>").join("");
  const catalog = await fetch("/api/catalog").then((res) => res.json()).catch(() => ({ avatars: [], modes: [] }));
  state.avatars = catalog.avatars || [];
  state.modes = catalog.modes || [];
  state.mode = GOALS[state.profile.goal]?.mode || "free";
  els.nameInput.value = state.profile.name;
  document.getElementById("profileName").textContent = state.profile.name;
  document.getElementById("profileMeta").textContent = `${LEVELS[state.profile.level]?.label || "Intermediate"} · ${GOALS[state.profile.goal]?.label || "Speak Confidently"}`;
  els.dailyRange.value = String(state.profile.dailyMin);
  updateDial(state.profile.dailyMin);
  connectTutorSocket();
  if (state.profile.onboarded) {
    showScreen("home");
    renderProgress();
  } else {
    showScreen("welcome");
  }
}

boot().catch((err) => {
  setStatus(err.message);
});
