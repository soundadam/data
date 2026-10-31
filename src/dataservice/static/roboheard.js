// The RoboHEARD viewer (kind roboheard): rh_preview's whole-session player.
//
//   #/s/<S>   one session: video, a chosen signal, its spectrogram, the
//             speaker lanes and the transcript on one clock
//
// Every signal was encoded once at publish: listen/<S>/<signal>.mp3, and its
// spectrogram as one JPEG of tiles stacked top to bottom. Audio is the clock;
// the video is the published mp4, muted and following it. Media links are
// relative to the API base and expire, so a media error fetches the session
// again and carries the position over.
(function () {
  "use strict";

  const main = document.querySelector("main.console");
  const root = document.getElementById("view");
  const API = main.dataset.api;
  const SIGNALS = [["mic", "阵列麦克风"], ["mean", "6 路平均"], ["aec", "AEC"], ["ref", "回声参考"]];
  const MICS = [1, 2, 3, 4, 5, 6];
  const WINDOWS = [5, 10, 30, 60];
  const RATES = [0.75, 1, 1.25, 1.5, 2];
  const ROBOT = "机器人";
  const LANE = 20, OV_SPEC = 44, OV_LANE = 5, AXIS = 16, DET_SPEC = 170;
  const PREFS = "rh-player", POSITION = "rh-position";
  let generation = 0;
  let sessions = null; // the release's list, fetched once
  let active = null; // the player on screen: {toggle, seek, size, stop}

  // --- DOM and storage ---------------------------------------------------------

  function h(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "class") node.className = value;
      else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
      else node.setAttribute(name, value === true ? "" : value);
    }
    node.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
    return node;
  }

  function stored(kind, key) {
    try {
      return JSON.parse(window[kind].getItem(key) || "null");
    } catch {
      return null;
    }
  }

  function keep(kind, key, value) {
    try {
      window[kind].setItem(key, JSON.stringify(value));
    } catch {
      // a private window: nothing is remembered
    }
  }

  const prefs = { sig: "mic", mic: 1, cam: "", win: 10, rate: 1, ...stored("localStorage", PREFS) };

  function fmt(t) {
    t = Math.max(0, t);
    return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  }

  function fixed(value, digits) {
    return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : null;
  }

  function failed(error) {
    root.replaceChildren(h("p", { class: "hint lead", role: "alert" }, "读不到：" + error.message));
  }

  async function get(path) {
    const response = await fetch(API + path, { credentials: "same-origin" });
    if (!response.ok) {
      let reason = String(response.status);
      try {
        reason = (await response.json()).error || reason;
      } catch {
        // not JSON
      }
      throw new Error(reason);
    }
    return response.json();
  }

  function linked(url) {
    return new URL(url, new URL(API + "/", location.href)).href;
  }

  function select(label, options, value, onchange, disabled) {
    const pick = h("select", { disabled }, options.map(([v, text]) => h("option", { value: v, selected: String(v) === String(value) }, text)));
    pick.addEventListener("change", () => onchange(pick.value));
    return h("label", { class: "field" }, h("span", null, label), pick);
  }

  // --- the session header --------------------------------------------------------

  function sessionLabel(s) {
    return `${s.session} · ${fixed(s.dur / 60, 1)} 分钟 · ${s.n_speakers} 人`;
  }

  function header(row, speakers) {
    const figures = [
      ["时长", fixed(row.dur / 60, 1) && fixed(row.dur / 60, 1) + " 分钟"],
      ["说话人", row.n_speakers],
      ["人声占比", row.dur ? Math.round((100 * row.human_speech) / row.dur) + "%" : null],
      ["机器人语音", fixed(row.robot_speech, 0) && fixed(row.robot_speech, 0) + " s"],
      ["2 人以上重叠", fixed(row.overlap_2plus, 0) && fixed(row.overlap_2plus, 0) + " s"],
      ["唤醒词", row.n_wake],
    ].filter(([, v]) => v !== null && v !== undefined && !Number.isNaN(v));
    const who = speakers.map((s) => h("li", null, h("b", null, s.speaker), " · " + [
      s.gender, fixed(s.age, 0), s.origin, `${s.turns} 轮，${fixed(s.speech, 0)} s`,
    ].filter((x) => x !== null && x !== undefined && x !== "").join(" · ")));
    const levels = [1, 2, 3, 4, 5, 6, 7, 8].filter((c) => fixed(row[`raw_ch${c}_db`], 0) !== null)
      .map((c) => `${c} ${fixed(row[`raw_ch${c}_db`], 0)}`);
    const recording = [
      row.raw_channels !== undefined ? `阵列 ${row.raw_channels} 路，AEC ${row.aec_channels ?? "—"} 路（${row.has_aec ? "有" : "缺"}）` : null,
      fixed(row.floor_db, 0) !== null ? `底噪 ${fixed(row.floor_db, 0)} dB（AEC ${fixed(row.aec_floor_db, 0) ?? "—"} dB）` : null,
      row.dead_channels !== undefined ? `坏通道 ${row.dead_channels || "无"}` : null,
      fixed(row.raw_clip_max, 2) !== null ? `峰值 ${fixed(row.raw_clip_max, 2)}` : null,
      levels.length ? "各通道电平 dB：" + levels.join(" · ") : null,
    ].filter(Boolean);
    return [
      h("div", { class: "tiles" }, figures.map(([label, figure]) =>
        h("div", { class: "tile" }, h("p", { class: "label" }, label), h("p", { class: "figure" }, String(figure))))),
      who.length ? h("ul", { class: "rh-speakers" }, who) : null,
      recording.length ? h("details", { class: "rh-recording" }, h("summary", null, "录音"), h("p", null, recording.join(" · "))) : null,
    ];
  }

  // --- the player -------------------------------------------------------------------

  function signalKey(sig, mic) {
    return sig === "mic" || sig === "aec" ? sig + mic : sig;
  }

  function player(mine, sid, data) {
    if (active) active.stop();
    const ids = sessions.sessions.map((s) => s.session);
    const at = ids.indexOf(sid);
    const go = (id) => { if (id) location.hash = "#/s/" + id; };
    const L = data.listen;
    const signals = data.signals;
    const cams = Object.keys(data.video);
    const offered = SIGNALS.filter(([sig]) => (sig === "mic" || sig === "aec"
      ? MICS.some((c) => signals[sig + c]) : signals[sig]));
    if (!offered.some(([sig]) => sig === prefs.sig) && offered.length) prefs.sig = offered[0][0];
    if (!signals[signalKey(prefs.sig, prefs.mic)]) {
      prefs.mic = MICS.find((c) => signals[signalKey(prefs.sig, c)]) || prefs.mic;
    }
    const cam = cams.includes(prefs.cam) ? prefs.cam : cams[0];

    const picker = h("div", { class: "rh-session" },
      select("会话", sessions.sessions.map((s) => [s.session, sessionLabel(s)]), sid, go),
      h("button", { type: "button", class: "button secondary", disabled: at <= 0, title: "上一个", "aria-label": "上一个会话", onclick: () => go(ids[at - 1]) }, "‹"),
      h("button", { type: "button", class: "button secondary", disabled: at >= ids.length - 1, title: "下一个", "aria-label": "下一个会话", onclick: () => go(ids[at + 1]) }, "›"));
    const top = [h("div", { class: "rh-toolbar" }, picker), ...header(data.session, data.speakers)];
    if (!offered.length) {
      root.replaceChildren(...top, h("p", { class: "hint" }, "这个会话还没有试听文件。"));
      active = null;
      return;
    }

    // segments, lanes and the transcript
    const segs = data.segments.map((g) => ({ s: g.start, e: g.end, k: g.kind, w: g.kind === "robot" || !g.speaker ? ROBOT : g.speaker, x: g.text || "" }));
    const lanes = [...new Set(segs.map((g) => g.w))].sort((a, b) => (a === ROBOT) - (b === ROBOT) || a.localeCompare(b));
    segs.forEach((g) => { g.l = lanes.indexOf(g.w); });
    let dur = Math.max(data.session.dur || 0, ...segs.map((g) => g.e));

    const audio = h("audio", { preload: "auto" });
    const video = cams.length ? h("video", { muted: true, playsinline: true, preload: "auto" }) : null;
    if (video) video.muted = true;
    const sheet = new Image();
    const tx = h("div", { class: "rh-tx" }, segs.map((g) =>
      h("div", { class: "rh-line " + g.k, onclick: () => seek(g.s) },
        h("span", { class: "t" }, fmt(g.s)), h("span", { class: "who" }, g.w), h("span", null, g.x || "—"))));
    const rows = [...tx.children];
    const ov = h("canvas", { class: "rh-ov", "aria-label": "整段" });
    const det = h("canvas", { class: "rh-det", "aria-label": "当前窗口" });
    const play = h("button", { type: "button", class: "button", title: "空格", onclick: () => toggle() }, "▶ 播放");
    const clock = h("span", { class: "rh-clock" }, "0:00 / " + fmt(dur));
    const note = h("span", { class: "hint rh-note" });

    // where the position, the signal and the camera come from
    let pending = null; // a position to restore once the new source has its metadata
    let restarts = 0;
    const saved = stored("sessionStorage", POSITION);
    if (saved && saved.sid === sid) pending = saved.t;

    function setSignal() {
      const key = signalKey(prefs.sig, prefs.mic);
      const s = signals[key];
      if (pending === null && audio.currentSrc) pending = audio.currentTime;
      const playing = !audio.paused;
      audio.src = linked(s.mp3);
      audio.defaultPlaybackRate = audio.playbackRate = prefs.rate;
      audio.addEventListener("loadedmetadata", () => {
        if (pending !== null) audio.currentTime = Math.min(pending, audio.duration || pending);
        pending = null;
        dur = Math.max(dur, audio.duration || 0);
        if (playing) audio.play().catch(() => {});
      }, { once: true });
      sheet.src = linked(s.jpg);
      const shift = L.aec_shift_s;
      note.textContent = prefs.sig === "aec" && shift ? `AEC 晚读 ${Math.round(shift * 1000)} ms，与标注对齐` : "";
    }

    function setCamera(name) {
      if (!video) return;
      video.src = linked(data.video[name]);
      video.playbackRate = prefs.rate;
      video.addEventListener("loadedmetadata", () => {
        video.currentTime = audio.currentTime;
        if (!audio.paused) video.play().catch(() => {});
      }, { once: true });
    }

    // Links expire: fetch them again once, then put the source back where it was.
    async function refresh(then) {
      if (restarts++ > 3) return failed(new Error("媒体链接失效"));
      const fresh = await get("/session/" + sid);
      if (mine !== generation) return;
      Object.assign(signals, fresh.signals);
      Object.assign(data.video, fresh.video);
      then();
    }
    const onError = (then) => () => { if (mine === generation) refresh(then).catch(failed); };
    audio.addEventListener("error", () => { pending = audio.currentTime || pending; });
    audio.addEventListener("error", onError(setSignal));
    sheet.addEventListener("error", onError(() => { sheet.src = linked(signals[signalKey(prefs.sig, prefs.mic)].jpg); }));
    if (video) video.addEventListener("error", onError(() => setCamera(camPick.value)));
    audio.addEventListener("playing", () => { restarts = 0; });

    const savePrefs = () => keep("localStorage", PREFS, prefs);
    const sigPick = select("信号", offered.map(([v, label]) => [v, label]), prefs.sig, (v) => {
      prefs.sig = v;
      if (!signals[signalKey(v, prefs.mic)]) prefs.mic = MICS.find((c) => signals[signalKey(v, c)]) || prefs.mic;
      micPick.querySelector("select").disabled = !(v === "mic" || v === "aec");
      micPick.querySelector("select").value = String(prefs.mic);
      savePrefs();
      setSignal();
    });
    const micPick = select("麦克风", MICS.map((c) => [c, String(c)]), prefs.mic, (v) => {
      if (!signals[signalKey(prefs.sig, v)]) return;
      prefs.mic = Number(v);
      savePrefs();
      setSignal();
    }, !(prefs.sig === "mic" || prefs.sig === "aec"));
    const camField = cams.length ? select("相机", cams.map((c) => [c, c]), cam, (v) => { prefs.cam = v; savePrefs(); setCamera(v); }) : null;
    const camPick = camField ? camField.querySelector("select") : null;
    const ratePick = select("速度", RATES.map((r) => [r, r + "×"]), prefs.rate, (v) => {
      prefs.rate = Number(v);
      audio.playbackRate = audio.defaultPlaybackRate = prefs.rate;
      if (video) video.playbackRate = prefs.rate;
      savePrefs();
    });
    const winPick = select("窗口", WINDOWS.map((w) => [w, w + " s"]), prefs.win, (v) => { prefs.win = Number(v); savePrefs(); });
    top[0].append(sigPick, micPick, camField || "", note);

    root.replaceChildren(...top, h("div", { class: "rh-player" },
      h("div", { class: "rh-top" + (video ? "" : " novideo") }, video ? h("div", { class: "rh-video" }, video) : null, tx),
      h("div", { class: "rh-bar" }, play, clock, ratePick, winPick),
      ov, det, audio));

    // --- drawing ---------------------------------------------------------------------

    let dpr = 1, W = 0, ovImg = null, colours = null, theme = "";

    function palette() {
      const style = getComputedStyle(main);
      const v = (k) => style.getPropertyValue(k).trim();
      return {
        surface: v("--color-surface"), contrast: v("--color-contrast"), muted: v("--color-muted"),
        border: v("--color-border"), head: v("--color-accent-marker"), human: v("--rh-human"),
        robot: v("--rh-robot"), on: v("--rh-on-series"), font: v("--font-sans"),
      };
    }

    function size() {
      dpr = window.devicePixelRatio || 1;
      W = det.clientWidth;
      ov.style.height = OV_SPEC + lanes.length * OV_LANE + 2 + "px";
      det.style.height = DET_SPEC + AXIS + lanes.length * LANE + "px";
      for (const c of [ov, det]) {
        c.width = Math.round(c.clientWidth * dpr);
        c.height = Math.round(c.clientHeight * dpr);
      }
      drawOverview();
    }

    function drawSpec(g, t0, t1, x, y, w, ht) {
      if (!sheet.complete || !sheet.naturalWidth || !L.rows || !L.tile) return;
      const per = 1 / L.hop_s, c0 = t0 * per, c1 = t1 * per;
      const tiles = Math.round(sheet.naturalHeight / L.rows);
      g.imageSmoothingQuality = "high";
      for (let k = 0; k < tiles; k++) {
        const a0 = k * L.tile, s0 = Math.max(c0, a0), s1 = Math.min(c1, a0 + L.tile);
        if (s1 <= s0) continue;
        g.drawImage(sheet, s0 - a0, k * L.rows, s1 - s0, L.rows,
          x + ((s0 - c0) / (c1 - c0)) * w, y, ((s1 - s0) / (c1 - c0)) * w, ht);
      }
    }

    function drawOverview() {
      if (!W) return;
      colours = palette();
      theme = JSON.stringify(colours);
      const c = document.createElement("canvas");
      c.width = ov.width;
      c.height = ov.height;
      const g = c.getContext("2d");
      g.scale(dpr, dpr);
      g.fillStyle = colours.surface;
      g.fillRect(0, 0, W, ov.clientHeight);
      drawSpec(g, 0, dur, 0, 0, W, OV_SPEC);
      for (const s of segs) {
        g.fillStyle = s.k === "robot" ? colours.robot : colours.human;
        g.fillRect((s.s / dur) * W, OV_SPEC + 2 + s.l * OV_LANE, Math.max(1, ((s.e - s.s) / dur) * W), OV_LANE - 1);
      }
      ovImg = c;
    }

    // the playhead sits a third of the way in, so what is coming is on screen
    function view(t) {
      const t0 = Math.min(Math.max(0, t - prefs.win / 3), Math.max(0, dur - prefs.win));
      return [t0, t0 + prefs.win];
    }

    let lastOn = -1, lastSync = 0, lastSave = 0;
    function frame() {
      if (mine !== generation) return;
      const now = palette();
      if (JSON.stringify(now) !== theme) drawOverview();
      const C = colours || now;
      const t = audio.currentTime;
      const [t0, t1] = view(t);

      const o = ov.getContext("2d");
      o.setTransform(1, 0, 0, 1, 0, 0);
      o.clearRect(0, 0, ov.width, ov.height);
      if (ovImg) o.drawImage(ovImg, 0, 0);
      o.scale(dpr, dpr);
      o.strokeStyle = C.contrast;
      o.lineWidth = 1;
      o.strokeRect((t0 / dur) * W + 0.5, 0.5, Math.max(2, ((t1 - t0) / dur) * W), ov.clientHeight - 1);
      o.fillStyle = C.head;
      o.fillRect((t / dur) * W - 1, 0, 2, ov.clientHeight);

      const g = det.getContext("2d");
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      const H = det.clientHeight;
      g.fillStyle = C.surface;
      g.fillRect(0, 0, W, H);
      drawSpec(g, t0, t1, 0, 0, W, DET_SPEC);
      const X = (s) => ((s - t0) / (t1 - t0)) * W;
      g.fillStyle = C.muted;
      g.font = `11px ${C.font}`;
      g.textBaseline = "middle";
      const step = prefs.win <= 10 ? 1 : prefs.win <= 30 ? 5 : 10;
      for (let s = Math.ceil(t0 / step) * step; s <= t1; s += step) {
        g.fillRect(X(s), DET_SPEC, 1, 4);
        g.fillText(fmt(s), X(s) + 3, DET_SPEC + AXIS / 2 + 1);
      }
      g.fillStyle = C.border;
      lanes.forEach((_, i) => g.fillRect(0, DET_SPEC + AXIS + i * LANE, W, 1));
      for (const s of segs) {
        if (s.e < t0 || s.s > t1) continue;
        const x0 = Math.max(0, X(s.s)), x1 = Math.min(W, X(s.e)), y = DET_SPEC + AXIS + s.l * LANE;
        g.fillStyle = s.k === "robot" ? C.robot : C.human;
        g.globalAlpha = t >= s.s && t < s.e ? 1 : 0.7;
        g.fillRect(x0, y + 2, Math.max(1, x1 - x0), LANE - 4);
        g.globalAlpha = 1;
        g.save();
        g.beginPath();
        g.rect(x0, y, x1 - x0, LANE);
        g.clip();
        g.fillStyle = C.on;
        g.fillText(s.x, x0 + 4, y + LANE / 2);
        g.restore();
      }
      lanes.forEach((name, i) => { // lane names last, over the bars
        const y = DET_SPEC + AXIS + i * LANE;
        g.fillStyle = C.surface;
        g.globalAlpha = 0.85;
        g.fillRect(0, y + 2, g.measureText(name).width + 8, LANE - 4);
        g.globalAlpha = 1;
        g.fillStyle = C.contrast;
        g.fillText(name, 4, y + LANE / 2);
      });
      g.fillStyle = C.head;
      g.fillRect(X(t) - 1, 0, 2, H);

      clock.textContent = `${fmt(t)} / ${fmt(dur)}`;
      follow(t);
      requestAnimationFrame(frame);
    }

    function follow(t) {
      let on = -1;
      for (let i = 0; i < segs.length; i++) if (segs[i].s <= t && t < segs[i].e) on = i;
      if (on < 0) for (let i = 0; i < segs.length; i++) if (segs[i].s <= t) on = i;
      if (on !== lastOn) {
        if (lastOn >= 0) rows[lastOn].classList.remove("on");
        if (on >= 0) {
          rows[on].classList.add("on");
          tx.scrollTop = rows[on].offsetTop - tx.clientHeight / 3;
        }
        lastOn = on;
      }
      const now = performance.now();
      if (video && video.readyState > 0 && now - lastSync > 400) {
        lastSync = now;
        if (Math.abs(video.currentTime - t) > 0.3) video.currentTime = t;
      }
      if (now - lastSave > 1000 && pending === null) {
        lastSave = now;
        keep("sessionStorage", POSITION, { sid, t });
      }
    }

    function seek(t) {
      audio.currentTime = Math.min(Math.max(0, t), dur);
      if (video && video.readyState > 0) video.currentTime = audio.currentTime;
    }

    function toggle() {
      if (audio.paused) audio.play().catch(() => {});
      else audio.pause();
    }

    audio.addEventListener("play", () => { play.textContent = "❚❚ 暂停"; if (video) video.play().catch(() => {}); });
    audio.addEventListener("pause", () => { play.textContent = "▶ 播放"; if (video) video.pause(); });
    audio.addEventListener("seeked", () => { if (video && video.readyState > 0) video.currentTime = audio.currentTime; });
    sheet.addEventListener("load", drawOverview);
    ov.addEventListener("click", (e) => seek((e.offsetX / ov.clientWidth) * dur));
    det.addEventListener("click", (e) => {
      const [t0, t1] = view(audio.currentTime);
      seek(t0 + (e.offsetX / det.clientWidth) * (t1 - t0));
    });

    active = {
      toggle,
      seek: (by) => seek(audio.currentTime + by),
      size,
      stop() {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
        if (video) {
          video.pause();
          video.removeAttribute("src");
          video.load();
        }
      },
    };
    setSignal();
    if (cam) setCamera(cam);
    size();
    requestAnimationFrame(frame);
  }

  // --- routing ---------------------------------------------------------------------

  async function route() {
    const mine = ++generation;
    try {
      if (!sessions) sessions = await get("/sessions");
      const ids = sessions.sessions.map((s) => s.session);
      if (!ids.length) throw new Error("这个版本没有会话");
      const asked = (location.hash.match(/^#\/s\/(S[0-9]{3,4})$/) || [])[1];
      const last = stored("sessionStorage", POSITION);
      const sid = ids.includes(asked) ? asked : last && ids.includes(last.sid) ? last.sid : ids[0];
      if (asked !== sid) history.replaceState(null, "", "#/s/" + sid);
      const data = await get("/session/" + sid);
      if (mine === generation) player(mine, sid, data);
    } catch (error) {
      if (mine === generation) failed(error);
    }
  }

  document.addEventListener("keydown", (e) => {
    if (!active || e.altKey || e.ctrlKey || e.metaKey) return;
    if (["SELECT", "INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
    if (e.code === "Space" && ["BUTTON", "SUMMARY", "A"].includes(e.target.tagName)) return;
    if (e.code === "Space") {
      e.preventDefault();
      active.toggle();
    } else if (e.code === "ArrowLeft") active.seek(-5);
    else if (e.code === "ArrowRight") active.seek(5);
  });
  window.addEventListener("resize", () => { if (active) active.size(); });
  window.addEventListener("hashchange", route);
  route();
})();
