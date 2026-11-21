// 조립 도우미 화면. 3D 뷰어는 팀원 모듈이 window.viewer3d 로 붙는다:
//   window.viewer3d = { goto(stepId), highlight(partIds), onStep(cb) }   // cb(stepId, by)
// 없으면 아래 Mock3D가 같은 역할을 흉내 낸다.
const $ = (id) => document.getElementById(id);
let ws, sessionId, KB, stepIdx = 0, llmOn = false;

// ---------- 3D 연결 (임시 대역) ----------
const Mock3D = {
  cb: null,
  onStep(cb) { this.cb = cb; },
  goto(stepId) {
    const i = KB.steps.findIndex((s) => s.step_id === stepId);
    if (i < 0) return;
    stepIdx = i;
    const s = KB.steps[i];
    $("mockStep").textContent = `STEP ${String(s.order).padStart(2, "0")} · ${s.name}`;
    this.highlight(s.parts);
    this.cb && this.cb(stepId, "ui");
  },
  highlight(ids) {
    $("highlight").innerHTML = ids.length
      ? ids.map((p) => `<div class="prow"><span class="mono">${esc(p)}</span><span>${esc(KB.parts[p]?.name || p)}</span></div>`).join("")
      : `<p class="mono dim">— NONE —</p>`;
  },
};
const viewer = () => window.viewer3d || Mock3D;

function navigate(m) {
  let i = stepIdx;
  if (m.cmd === "next") i = Math.min(stepIdx + 1, KB.steps.length - 1);
  else if (m.cmd === "prev") i = Math.max(stepIdx - 1, 0);
  else if (m.cmd === "first") i = 0;
  else if (m.to) i = Math.min(Math.max(m.to - 1, 0), KB.steps.length - 1);
  if (m.cmd === "replay") { toast("이번 단계를 다시 보여 줘요"); return; }
  viewer().goto(KB.steps[i].step_id);
}

// ---------- 시작 ----------
// 시작 화면 없이 바로 시작. 작업자 이름은 주소로 바꿀 수 있다: /?op=worker2
(async () => {
  KB = await fetch("/api/kb").then((r) => r.json()); llmOn = KB.llm;
  const builder = new URLSearchParams(location.search).get("op") || "worker1";
  const r = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ builder }) }).then((r) => r.json());
  sessionId = r.session_id;
  $("opName").textContent = builder.toUpperCase();
  $("aiLink").textContent = llmOn ? "ONLINE" : "LOCAL";
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${sessionId}`);
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onopen = () => {
    viewer().onStep((stepId, by) => send({ type: "step", step_id: stepId, by }));
    if (r.resume) handle(r.resume);
    viewer().goto(r.resume?.step_id || KB.steps[0].step_id);
  };
})();

const send = (m) => ws && ws.readyState === 1 && ws.send(JSON.stringify(m));

// ---------- 서버 메시지 ----------
function handle(m) {
  if (m.type === "step_card") return renderStep(m);
  if (m.type === "answer") return renderAnswer(m);
  if (m.type === "prompt") return renderPrompt(m);
  if (m.type === "highlight") { viewer().highlight(m.part_ids); window.dispatchEvent(new CustomEvent("agent:highlight", { detail: m.part_ids })); return; }
  if (m.type === "nav") return navigate(m);
  if (m.type === "quiet") { if (m.speech) say(m.speech); return toast("조용히 모드: 안전 경고만"); }
  if (m.type === "toast") return toast(m.text);
  if (m.type === "archive") return openArchive(m);
  if (m.type === "archive_close") return ($("archive").hidden = true);
}

// ---------- 기록 보관함 ----------
let ARCH = null;
function openArchive(m) {
  ARCH = m;
  $("archive").hidden = false;
  const focus = m.focus || m.steps[0]?.step_id;
  $("archTabs").innerHTML = m.steps.map((s) =>
    `<button role="tab" data-sid="${s.step_id}" aria-selected="${s.step_id === focus}">S${String(s.order).padStart(2, "0")} · ${esc(s.name)}</button>`).join("");
  $("archTabs").querySelectorAll("button").forEach((b) => (b.onclick = () => showArchStep(b.dataset.sid)));
  showArchStep(focus);
}
function showArchStep(sid) {
  const s = ARCH.steps.find((x) => x.step_id === sid) || ARCH.steps[0];
  sid = s.step_id;
  $("archTabs").querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", b.dataset.sid === sid));
  const item = (t, sub) => `<p class="arch-item">${esc(t)}${sub ? `<small>${esc(sub)}</small>` : ""}</p>`;
  const empty = (t) => `<p class="arch-empty">${t}</p>`;
  const d = s.designer;
  $("archDesigner").innerHTML = (d.warnings.map((w) => item(w, `CAUTION · SLIDE ${d.slides.join(", ")}`)).join("") +
    d.speech.map((x) => item(x.text, x.source.replace("설계자 설명", "DESIGNER VOICE"))).join("")) || empty("// NO DESIGNER DATA");
  const w = s.workers;
  $("archWorkers").innerHTML = w.n
    ? item(`OPERATORS ${w.n} · AVG ${mmss(w.median_sec || 0)} · DIFFICULTY ${w.difficulty ?? "-"}`) +
      w.tips.map((t) => item(t, "OPERATOR NOTE")).join("") +
      w.faq.map((q) => item(q.question, `ASKED ×${q.count} · A: ${q.answer}`)).join("")
    : empty("// NO BUILD RECORDS YET");
  $("archMarks").innerHTML = s.marks.length
    ? s.marks.map((k) => item(k.note || "★ MARK", `${k.builder.toUpperCase()} · ${new Date(k.t * 1000).toLocaleString("en-GB")}${k.media ? " · VIDEO" : ""}`)).join("")
    : empty("// NO MARKED SCENES — SAY \"잘 봐 둬\" OR PRESS ★ MARK");
}
$("archClose").onclick = () => ($("archive").hidden = true);
$("openArch").onclick = () => send({ type: "ask", text: "기록 보여줘" });

function renderStep(m) {
  const s = m.step;
  stepIdx = m.index - 1;
  const pad = (n) => String(n).padStart(2, "0");
  $("stepNo").textContent = `STEP ${pad(m.index)} OF ${pad(m.total)} // ${s.step_id}`;
  $("stepIdx").textContent = pad(m.index); $("stepTot").textContent = "/" + pad(m.total);
  const C = 2 * Math.PI * 44;
  $("ringArc").style.strokeDasharray = `${(C * m.index) / m.total} ${C}`;
  $("segs").innerHTML = KB.steps.map((x, i) => `<span class="seg ${i < stepIdx ? "done" : i === stepIdx ? "cur" : ""}">S${pad(i + 1)}</span>`).join("");
  stepStart = Date.now(); stepEst = s.baseline_sec || 300;
  $("stepEst").textContent = mmss(stepEst);
  $("taskCount").textContent = `${pad(s.instructions.length)} ITEMS`;
  $("stepName").textContent = s.name;
  $("todo").innerHTML = s.instructions.map((t) => `<li>${esc(t)}</li>`).join("") +
    (s.bolts || []).map((b) => `<li class="bolt">볼트 ${esc(b)}</li>`).join("");
  $("idle").hidden = false;
  $("warn").hidden = !s.warnings.length;
  $("warnList").innerHTML = s.warnings.map((t) => `<li>${esc(t)}</li>`).join("");
  $("hints").innerHTML = m.hints.map((h) => `<p>· ${esc(h)}</p>`).join("");
  $("answer").hidden = true; $("prompt").hidden = true;
}

function renderAnswer(m) {
  $("answer").hidden = false; $("idle").hidden = true;
  $("answerQ").textContent = "> " + m.question;
  $("answerText").textContent = m.text || m.speech;
  const slide = (m.visual || []).find((v) => v.type === "slide");
  $("answerSlide").hidden = !slide;
  if (slide) $("answerSlide").src = "/" + slide.image;
  $("answerSrc").textContent = m.sources?.length ? "SRC // " + m.sources.join(" · ") : "SRC // —";
  say(m.speech);
}

function renderPrompt(m) {
  const box = $("prompt");
  box.hidden = false; $("idle").hidden = true;
  box.className = "prompt " + (m.level || "info");
  box.innerHTML = esc(m.text || m.speech || "");
  if (m.ask_comment) {
    box.innerHTML += `<textarea id="comment" rows="2" placeholder="막혔던 점 한마디"></textarea><button class="btn" id="sendComment">TRANSMIT ▸</button>`;
    $("sendComment").onclick = () => { send({ type: "final_comment", text: $("comment").value }); box.hidden = true; };
  }
  if (m.speech) say(m.speech);
}

// ---------- 질문 보내기 ----------
function ask(text) {
  text = text.trim();
  if (!text) return;
  if (/^(설명|녹화)\s*(시작|켜)/.test(text)) return Cap.start();
  if (/^(설명|녹화)\s*(끝|종료|그만|꺼)/.test(text)) return Cap.stop();
  send({ type: "ask", text });
}

// ---------- 설계자 설명 녹화: 화면은 위쪽 표시만 ----------
// 카메라로 슬라이드가 바뀔 때마다 사진을 찍고, 말은 통째로 녹음한다.
// 끝나면 서버에 올리고 "분석 중" → 지식에 반영되면 알림.
const CAP_SCREEN = new URLSearchParams(location.search).get("cap") === "screen";
const Cap = {
  stream: null, rec: null, audio: [], frames: [], t0: 0, timer: null, small: null, sctx: null, last: null, prev: null, pend: 0,
  set(cls, text) { const b = $("capBtn"); b.className = "cap " + cls; b.textContent = text; },
  now() { return (performance.now() - this.t0) / 1000; },
  async start() {
    if (this.rec) return;
    try {
      if (CAP_SCREEN) {   // ?cap=screen : 같은 노트북에 띄운 PPT를 화면째로 찍고, 말은 마이크로
        const scr = await navigator.mediaDevices.getDisplayMedia({ video: { width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false });
        const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.stream = new MediaStream([...scr.getVideoTracks(), ...mic.getAudioTracks()]);
      } else {
        this.stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: true });
      }
    } catch (e) { return toast("카메라·마이크를 열 수 없어요 (" + e.name + "). Quest는 https 주소가 필요해요"); }
    $("capVideo").srcObject = this.stream;
    this.frames = []; this.audio = []; this.last = null; this.prev = null; this.pend = 0;
    this.rec = new MediaRecorder(new MediaStream(this.stream.getAudioTracks()));
    this.rec.ondataavailable = (e) => e.data.size && this.audio.push(e.data);
    this.rec.start(1000); this.t0 = performance.now();
    this.small = document.createElement("canvas"); this.small.width = 48; this.small.height = 27;
    this.sctx = this.small.getContext("2d", { willReadFrequently: true });
    setTimeout(() => this.shot(), 1500);
    this.timer = setInterval(() => { this.watch(); this.set("rec", `● 녹화 중 ${mmss(this.now())} · 슬라이드 ${this.frames.length}`); }, 700);
    this.set("rec", "● 녹화 중");
  },
  gray() {
    this.sctx.drawImage($("capVideo"), 0, 0, 48, 27);
    const d = this.sctx.getImageData(0, 0, 48, 27).data, g = new Uint8Array(48 * 27);
    for (let i = 0; i < g.length; i++) g[i] = (d[i * 4] * 3 + d[i * 4 + 1] * 6 + d[i * 4 + 2]) / 10;
    return g;
  },
  watch() {  // 많이 바뀌었고 화면이 멈춰 있으면(넘기는 중 아님) 두 번 확인 후 새 슬라이드로 찍기
    if (!this.last) return;
    const g = this.gray(), dif = (a, b) => a.reduce((s, v, i) => s + Math.abs(v - b[i]), 0) / a.length;
    const moved = dif(g, this.last), settle = this.prev ? dif(g, this.prev) : 99; this.prev = g;
    if (moved > 16 && settle < 5) { if (++this.pend >= 2) { this.pend = 0; this.shot(); } } else this.pend = 0;
  },
  shot() {
    const v = $("capVideo"); if (!v.videoWidth) return;
    const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    const t = this.now();
    c.toBlob((b) => { this.frames.push({ b, t }); this.last = this.gray(); }, "image/jpeg", 0.88);
  },
  async stop() {
    if (!this.rec) return;
    clearInterval(this.timer);
    await new Promise((res) => { this.rec.onstop = res; this.rec.stop(); });
    this.stream.getTracks().forEach((t) => t.stop()); this.rec = null;
    if (!this.frames.length) { this.set("", "● 설명 녹화"); return toast("찍힌 슬라이드가 없어요"); }
    this.set("busy", "◌ 분석 중…");
    const fd = new FormData();
    this.frames.forEach((f, i) => fd.append("frames", f.b, `f_${i + 1}.jpg`));
    fd.append("frame_times", JSON.stringify(this.frames.map((f) => f.t)));
    fd.append("audio", new Blob(this.audio, { type: this.audio[0]?.type || "audio/webm" }), "audio.webm");
    const j = await fetch("/api/briefing", { method: "POST", body: fd }).then((r) => r.json());
    const poll = async () => {
      const s = await fetch("/api/briefing/" + j.id).then((r) => r.json());
      if (s.ok === null || s.ok === undefined) { this.set("busy", "◌ 분석 중… " + (s.stage || "")); return setTimeout(poll, 1500); }
      this.set("", "● 설명 녹화");
      toast(s.ok ? `설명 반영 완료: 슬라이드 ${s.slides}장, 말 ${s.segments}문장` : "분석 실패: " + (s.log || []).slice(-1)[0]);
    };
    poll();
  },
};
$("capBtn").onclick = () => (Cap.rec ? Cap.stop() : Cap.start());
$("askText").addEventListener("keydown", (e) => { if (e.key === "Enter") { ask(e.target.value); e.target.value = ""; } });
document.querySelectorAll("[data-nav]").forEach((b) => (b.onclick = () => navigate({ cmd: b.dataset.nav })));
$("mark").onclick = () => send({ type: "bookmark" });
$("finish").onclick = () => send({ type: "finish" });

// ---------- 음성: 누르고 말하기 ----------
let rec, chunks = [], recognizer;
const talk = $("talk");
const startTalk = async () => {
  talk.classList.add("rec"); send({ type: "speaking", on: true });
  if (navigator.mediaDevices?.getUserMedia && window.MediaRecorder) {  // 서버 음성 인식 (키 없으면 PC의 Whisper)
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    rec = new MediaRecorder(stream); chunks = [];
    rec.ondataavailable = (e) => chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const fd = new FormData(); fd.append("audio", new Blob(chunks, { type: rec.mimeType }), "q.webm");
      toast("듣는 중…");
      const r = await fetch("/api/stt", { method: "POST", body: fd }).then((r) => r.json()).catch(() => ({}));
      if (r.text) { toast("들은 말: " + r.text); ask(r.text); }
      else toast(r.error || "잘 못 들었어요. 다시 말해 주세요");
    };
    rec.start();
  } else {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { toast("이 브라우저는 음성 인식이 안 돼요. 입력창을 써 주세요."); return; }
    recognizer = new SR(); recognizer.lang = "ko-KR";
    recognizer.onresult = (e) => { const t = e.results[0][0].transcript; toast("들은 말: " + t); ask(t); };
    recognizer.start();
  }
};
const stopTalk = () => {
  talk.classList.remove("rec"); send({ type: "speaking", on: false });
  if (rec && rec.state === "recording") rec.stop();
  if (recognizer) { recognizer.stop(); recognizer = null; }
};
talk.addEventListener("pointerdown", startTalk);
talk.addEventListener("pointerup", stopTalk);
talk.addEventListener("pointerleave", () => talk.classList.contains("rec") && stopTalk());

// ---------- 음성 답 ----------
let audio;
async function say(text) {
  if (!text) return;
  try {  // 서버 음성 (키 없으면 무료 edge-tts). 실패하면 브라우저 음성
    const r = await fetch("/api/tts?text=" + encodeURIComponent(text));
    if (r.status === 200) { audio?.pause(); audio = new Audio(URL.createObjectURL(await r.blob())); await audio.play(); return; }
  } catch (e) { /* 아래로 */ }
  if ("speechSynthesis" in window) {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text); u.lang = "ko-KR"; u.rate = 1.05;
    speechSynthesis.speak(u);
  }
}

// ---------- 실시간 계기판: 시계, 단계 경과 시간 ----------
let stepStart = Date.now(), stepEst = 300;
const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
setInterval(() => {
  const c = $("clock"); if (c) c.textContent = new Date().toLocaleTimeString("en-GB", { hour12: false });
  const el = (Date.now() - stepStart) / 1000;
  if ($("stepTimer")) {
    $("stepTimer").textContent = mmss(el);
    const bar = $("dwellBar");
    bar.style.width = Math.min(100, (el / (stepEst * 1.5)) * 100) + "%";
    bar.classList.toggle("over", el > stepEst);
  }
}, 1000);

function toast(t) { const el = $("toast"); el.textContent = t; el.hidden = false; clearTimeout(el._t); el._t = setTimeout(() => (el.hidden = true), 2500); }
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
