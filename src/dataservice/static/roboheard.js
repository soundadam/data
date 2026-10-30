// The RoboHEARD viewer (kind roboheard): what rh_preview showed, in the browser.
//
//   #/                 the release: stats, mining queries, sessions
//   #/q/<query>/<n>    a mining query's hits from the n-th
//   #/s/<S>/<from>     a session's timeline, one window from <from> s
//   #/g/<seg_id>       one segment: context, channels, video
//
// The JSON comes from data-api (this origin). Audio never passes through the
// service: the page reads the wav header and then only the span's bytes from
// the presigned URL with HTTP Range, picks or averages channels here, and
// plays a mono WAV it builds. Video is the presigned mp4, muted and seeked,
// following the audio.
(function () {
  "use strict";

  const main = document.querySelector("main.console");
  const root = document.getElementById("view");
  const API = main.dataset.api;
  const WINDOW_S = 60;
  const PADS = [0, 0.5, 1, 2, 5, 10];
  const LANE = 18;
  const SVGNS = "http://www.w3.org/2000/svg";
  const CURVES = [["mic_db", "麦克风", 2], ["aec_db", "AEC", 3], ["ref_db", "回声参考", 4]];
  const SEG_HEAD = [["seg_id", "片段"], ["speaker", "说话人"], ["start", "开始 s"], ["dur", "时长 s"],
    ["n_ov_spk", "重叠人数"], ["ov_human", "人声重叠 s"], ["ov_robot", "机器人重叠 s"],
    ["snr_db", "SNR dB"], ["ref_db", "参考 dB"], ["flags", "标记"], ["text", "文本"]];
  let generation = 0;

  // --- DOM ---------------------------------------------------------------

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

  function s(tag, attrs, ...children) {
    const node = document.createElementNS(SVGNS, tag);
    for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
    node.append(...children);
    return node;
  }

  function num(value, digits = 1) {
    return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
  }

  function text(value) {
    return value === null || value === undefined || value === "" ? "—" : String(value);
  }

  function pct(part, whole) {
    return whole ? Math.round((100 * part) / whole) + "%" : "—";
  }

  function show(...nodes) {
    root.replaceChildren(...nodes);
  }

  function failed(error) {
    show(h("section", null, h("p", { class: "hint lead", role: "alert" }, "读不到：" + error.message)));
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

  function tiles(items) {
    return h("div", { class: "tiles" }, items.map(([label, figure]) =>
      h("div", { class: "tile" }, h("p", { class: "label" }, label), h("p", { class: "figure" }, figure))));
  }

  function table(heads, rows) {
    return h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", null, h("tr", null, heads.map(([label, numeric]) => h("th", { class: numeric ? "num" : null }, label)))),
      h("tbody", null, rows)));
  }

  function kv(pairs) {
    return h("dl", { class: "kv" }, pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => [h("dt", null, k), h("dd", null, typeof v === "number" ? num(v, 3) : String(v))]));
  }

  function back(...links) {
    return h("nav", { class: "segmented", "aria-label": "视图" },
      links.map(([href, label, current]) => h("a", { href, "aria-current": current ? "page" : null }, label)));
  }

  function segRow(seg, current) {
    return h("tr", null, SEG_HEAD.map(([key]) => {
      const value = seg[key];
      if (key === "seg_id") {
        return h("th", { scope: "row" }, h("a", { href: "#/g/" + value, "aria-current": value === current ? "true" : null }, value));
      }
      if (key === "text") return h("td", { class: "rh-long" }, text(value));
      if (typeof value === "number") return h("td", { class: "num" }, num(value, key.startsWith("n_") ? 0 : 2));
      return h("td", null, text(value || (key === "speaker" ? "robot" : value)));
    }));
  }

  // --- plots: energy, lanes, axis ------------------------------------------

  // Frame levels over [a, b), each curve a path broken where a frame is null.
  function energy(levels, a, b) {
    const values = CURVES.flatMap(([key]) => (levels[key] || []).filter((v) => v !== null));
    if (!values.length) return null;
    const low = Math.min(...values), high = Math.max(...values), span = Math.max(1, high - low);
    const height = 120;
    const svg = s("svg", { class: "rh-energy", viewBox: `0 0 1000 ${height}`, preserveAspectRatio: "none", height });
    for (const [key, , series] of CURVES) {
      const curve = levels[key];
      if (!curve) continue;
      let d = "", pen = false;
      curve.forEach((v, i) => {
        const t = levels.from + (i + 0.5) * levels.step;
        if (v === null || t < a || t > b) {
          pen = false;
          return;
        }
        const x = ((t - a) / (b - a)) * 1000, y = height - ((v - low) / span) * (height - 4) - 2;
        d += (pen ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
        pen = true;
      });
      svg.append(s("path", { d, class: "series-" + series }));
    }
    const legend = h("ul", { class: "legend" }, CURVES.filter(([key]) => levels[key])
      .map(([, label, series]) => h("li", { class: "series-" + series }, h("span", { class: "swatch" }), label)),
      h("li", null, `${num(low, 0)} … ${num(high, 0)} dB`));
    return [svg, legend];
  }

  function laneOf(seg) {
    return seg.kind === "robot" || !seg.speaker ? "robot" : seg.speaker;
  }

  // One row per speaker, robot last; a click opens the segment.
  function lanes(segments, a, b, current, mark) {
    const inside = segments.filter((g) => g.end > a && g.start < b);
    const names = [...new Set(inside.map(laneOf))].sort((x, y) => (x === "robot") - (y === "robot") || x.localeCompare(y));
    const height = Math.max(1, names.length) * LANE;
    const svg = s("svg", { class: "rh-lanes", viewBox: `0 0 1000 ${height}`, preserveAspectRatio: "none", height });
    if (mark) {
      svg.append(s("rect", { class: "rh-mark", x: ((mark[0] - a) / (b - a)) * 1000, y: 0,
        width: Math.max(1, ((mark[1] - mark[0]) / (b - a)) * 1000), height }));
    }
    names.forEach((_, i) => svg.append(s("line", { x1: 0, x2: 1000, y1: (i + 1) * LANE, y2: (i + 1) * LANE })));
    for (const g of inside) {
      const x0 = ((Math.max(g.start, a) - a) / (b - a)) * 1000, x1 = ((Math.min(g.end, b) - a) / (b - a)) * 1000;
      const rect = s("rect", {
        class: `seg series-${g.kind === "robot" ? 4 : 2}${g.seg_id === current ? " current" : ""}`,
        x: x0, y: names.indexOf(laneOf(g)) * LANE + 3, width: Math.max(1, x1 - x0), height: LANE - 6,
      }, s("title", null, `${g.seg_id} ${num(g.start, 2)}–${num(g.end, 2)} s\n${g.text || ""}${g.flags ? "\n" + g.flags : ""}`));
      rect.addEventListener("click", () => { window.location.hash = "#/g/" + g.seg_id; });
      svg.append(rect);
    }
    return [h("div", { class: "names", style: `grid-auto-rows: ${LANE}px` }, names.map((n) => h("span", { title: n }, n))), svg];
  }

  function axis(a, b) {
    return h("div", { class: "axis" }, [a, (a + b) / 2, b].map((t) => h("span", null, num(t, 1) + " s")));
  }

  function plot(segments, levels, a, b, current, mark) {
    const curve = levels ? energy(levels, a, b) : null;
    return h("div", { class: "rh-plot" },
      curve ? [h("div", { class: "names" }, h("span", null, "dB")), h("div", null, curve)] : null,
      lanes(segments, a, b, current, mark),
      axis(a, b));
  }

  // --- wav over HTTP Range ---------------------------------------------------

  const headers = new Map(); // object path → header, kept for the page's life
  const spans = new Map(); // `${which}|${a}|${b}` → {info, bytes}

  function objectPath(url) {
    return new URL(url).pathname;
  }

  async function ranged(url, first, last) {
    const abort = new AbortController();
    const response = await fetch(url, { headers: { Range: `bytes=${first}-${last}` }, signal: abort.signal });
    if (response.status !== 206) {
      abort.abort(); // a 200 would be the whole file
      throw new Error(response.status === 403 ? "expired" : "range " + response.status);
    }
    return response.arrayBuffer();
  }

  async function wavHeader(url) {
    const key = objectPath(url);
    if (headers.has(key)) return headers.get(key);
    const view = new DataView(await ranged(url, 0, 65535));
    const tag = (at) => String.fromCharCode(...new Uint8Array(view.buffer, at, 4));
    if (tag(0) !== "RIFF" || tag(8) !== "WAVE") throw new Error("not a wav");
    let at = 12, info = null;
    while (at + 8 <= view.byteLength) {
      const id = tag(at), size = view.getUint32(at + 4, true);
      if (id === "fmt ") {
        info = { format: view.getUint16(at + 8, true), channels: view.getUint16(at + 10, true),
          rate: view.getUint32(at + 12, true), block: view.getUint16(at + 20, true), bits: view.getUint16(at + 22, true) };
        if (info.format === 0xfffe) info.format = view.getUint16(at + 32, true);
      } else if (id === "data") {
        if (!info) break;
        info.offset = at + 8;
        info.frames = Math.floor(size / info.block);
        headers.set(key, info);
        return info;
      }
      at += 8 + size + (size % 2);
    }
    throw new Error("wav header not found");
  }

  async function spanBytes(which, url, a, b) {
    const key = `${which}|${a}|${b}`;
    if (spans.has(key)) return spans.get(key);
    const info = await wavHeader(url);
    const first = Math.min(info.frames, Math.floor(a * info.rate));
    const last = Math.min(info.frames, Math.ceil(b * info.rate));
    if (last <= first) throw new Error("span outside the recording");
    const bytes = await ranged(url, info.offset + first * info.block, info.offset + last * info.block - 1);
    const found = { info, bytes };
    spans.set(key, found);
    if (spans.size > 12) spans.delete(spans.keys().next().value);
    return found;
  }

  // The mean of `channels` (1-based) as float samples.
  function mono({ info, bytes }, channels) {
    const view = new DataView(bytes);
    const frames = Math.floor(bytes.byteLength / info.block), out = new Float32Array(frames);
    const width = info.bits / 8;
    const read = info.format === 3 && info.bits === 32 ? (at) => view.getFloat32(at, true)
      : info.bits === 16 ? (at) => view.getInt16(at, true) / 32768
        : info.bits === 32 ? (at) => view.getInt32(at, true) / 2147483648 : null;
    if (!read) throw new Error(`unsupported wav: format ${info.format}, ${info.bits} bit`);
    const picked = channels.filter((c) => c >= 1 && c <= info.channels);
    if (!picked.length) throw new Error(`the wav has ${info.channels} channels`);
    for (let i = 0; i < frames; i++) {
      let sum = 0;
      for (const c of picked) sum += read(i * info.block + (c - 1) * width);
      out[i] = sum / picked.length;
    }
    return out;
  }

  function wavBlob(samples, rate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2), view = new DataView(buffer);
    const put = (at, str) => [...str].forEach((ch, i) => view.setUint8(at + i, ch.charCodeAt(0)));
    put(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); put(8, "WAVE");
    put(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    put(36, "data"); view.setUint32(40, samples.length * 2, true);
    samples.forEach((v, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, v)) * 32767, true));
    return new Blob([buffer], { type: "audio/wav" });
  }

  function drawWave(canvas, samples) {
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(canvas.clientWidth * ratio)), height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    canvas.width = width;
    canvas.height = height;
    const pen = canvas.getContext("2d");
    pen.clearRect(0, 0, width, height);
    pen.fillStyle = getComputedStyle(canvas).getPropertyValue("--color-series-2").trim() || "#2f6fde";
    let peak = 1e-4;
    for (const v of samples) peak = Math.max(peak, Math.abs(v));
    const per = samples.length / width, mid = height / 2;
    for (let x = 0; x < width; x++) {
      let low = 0, high = 0;
      const end = Math.min(samples.length, Math.floor((x + 1) * per));
      for (let i = Math.floor(x * per); i < end; i++) {
        if (samples[i] < low) low = samples[i];
        if (samples[i] > high) high = samples[i];
      }
      pen.fillRect(x, mid - (high / peak) * mid, 1, Math.max(1, ((high - low) / peak) * mid));
    }
  }

  // --- views -----------------------------------------------------------------

  async function overview(mine) {
    const o = await get("/overview");
    if (mine !== generation) return;
    const st = o.stats, ix = o.index;
    const sessionHeads = [["会话"], ["时长 min", 1], ["说话人", 1], ["人声占比", 1], ["2+ 重叠 s", 1], ["机器人 s", 1], ["AEC"], ["坏通道"]];
    show(
      back(["#/", "概览", true]),
      tiles([
        ["会话", text(st.sessions && st.sessions.n)],
        ["时长", num(st.sessions && st.sessions.hours, 1) + " h"],
        ["人声片段", text(st.human_segments && st.human_segments.n)],
        ["说话人", text(st.speakers && st.speakers.in_csv)],
      ]),
      h("p", { class: "hint" }, `索引 v${text(ix.version)} · 构建 ${text(ix.built_at)} · 代码 ${text(ix.code)} · 发布 ${text(ix.published_at)} · ${o.license}，仅限非商业使用，不得转发原始数据。`),
      h("section", null, h("h2", null, "挖掘查询"), table([["查询"], ["说明"], ["片段", 1], ["会话", 1], ["分钟", 1]],
        o.queries.map((q) => h("tr", null,
          h("th", { scope: "row" }, h("a", { href: `#/q/${q.name}/0` }, q.name)), h("td", null, text(q.note)),
          h("td", { class: "num" }, text(q.segments)), h("td", { class: "num" }, text(q.sessions)), h("td", { class: "num" }, num(q.minutes, 1))))))),
      h("section", null, h("h2", null, "会话"), table(sessionHeads, o.sessions.map((x) => h("tr", null,
        h("th", { scope: "row" }, h("a", { href: `#/s/${x.session}/0` }, x.session)),
        h("td", { class: "num" }, num(x.dur / 60, 1)), h("td", { class: "num" }, text(x.n_speakers)),
        h("td", { class: "num" }, pct(x.human_speech, x.dur)), h("td", { class: "num" }, num(x.overlap_2plus, 0)),
        h("td", { class: "num" }, num(x.robot_speech, 0)), h("td", null, x.has_aec ? "有" : "—"), h("td", null, text(x.dead_channels))))))),
      h("details", null, h("summary", null, "stats.json"), h("pre", null, JSON.stringify(st, null, 2))),
    );
  }

  async function query(mine, name, offset) {
    const limit = 50;
    const q = await get(`/query/${encodeURIComponent(name)}?offset=${offset}&limit=${limit}`);
    if (mine !== generation) return;
    const pages = [];
    if (offset > 0) pages.push(["#/q/" + name + "/" + Math.max(0, offset - limit), "上一页"]);
    if (offset + limit < q.total) pages.push(["#/q/" + name + "/" + (offset + limit), "下一页"]);
    show(
      back(["#/", "概览"], [location.hash, name, true]),
      h("p", { class: "hint" }, `${q.total} 个片段，第 ${q.total ? offset + 1 : 0}–${Math.min(q.total, offset + limit)} 个。顺序与 rh_preview 的 seed 0 相同：先每个会话一个。`),
      table(SEG_HEAD.map(([, label]) => [label]), q.hits.map((g) => segRow(g))),
      pages.length ? h("nav", { class: "segmented", "aria-label": "翻页" }, pages.map(([href, label]) => h("a", { href }, label))) : null,
    );
  }

  const sessions = new Map(); // session → its JSON, for paging the window without asking again

  async function session(mine, id, from) {
    if (!sessions.has(id)) sessions.set(id, await get("/session/" + id));
    const data = sessions.get(id), row = data.session;
    const a = Math.max(0, Math.min(from, Math.max(0, row.dur - WINDOW_S))), b = Math.min(row.dur, a + WINDOW_S);
    const levels = await get(`/frames/${id}?from=${a}&to=${b}&points=1000`).catch(() => null);
    if (mine !== generation) return;
    const go = (t) => { window.location.hash = `#/s/${id}/${Math.round(Math.max(0, t))}`; };
    const jump = h("input", { type: "number", min: 0, max: Math.floor(row.dur), step: 5, value: Math.round(a) });
    jump.addEventListener("change", () => go(Number(jump.value) || 0));
    show(
      back(["#/", "概览"], [location.hash, id, true]),
      tiles([
        ["时长", num(row.dur / 60, 1) + " min"],
        ["说话人", text(row.speakers || row.n_speakers)],
        ["人声占比", pct(row.human_speech, row.dur)],
        ["2+ 重叠", num(row.overlap_2plus, 0) + " s"],
        ["机器人", num(row.robot_speech, 0) + " s"],
      ]),
      h("div", { class: "rh-toolbar" },
        h("button", { type: "button", class: "button secondary compact", onclick: () => go(a - WINDOW_S), disabled: a <= 0 }, "← 前 60 s"),
        h("label", null, "从 s", jump),
        h("button", { type: "button", class: "button secondary compact", onclick: () => go(a + WINDOW_S), disabled: b >= row.dur }, "后 60 s →")),
      plot(data.segments, levels, a, b),
      table(SEG_HEAD.map(([, label]) => [label]),
        data.segments.filter((g) => g.end > a && g.start < b).map((g) => segRow({ ...g, dur: g.end - g.start }))),
      h("details", null, h("summary", null, "会话字段"), kv(Object.entries(row))),
    );
  }

  let pad = 1;
  let mic = 1;

  async function segment(mine, segId) {
    let data = await get(`/segment/${segId}?pad=${pad}`);
    const levels = await get(`/frames/${data.segment.session}?from=${data.span[0]}&to=${data.span[1]}&points=1000`).catch(() => null);
    if (mine !== generation) return;
    const g = data.segment, [a, b] = data.span;
    const refresh = async () => { data = await get(`/segment/${segId}?pad=${pad}`); };

    const audio = h("audio", { controls: true, preload: "none" });
    const canvas = h("canvas", { "aria-hidden": "true" });
    const head = h("div", { class: "playhead", hidden: true });
    const wave = h("div", { class: "rh-wave", title: "点击跳到这里" }, canvas, head);
    const status = h("p", { class: "hint", role: "status" });
    const sources = [
      ["raw", () => [mic], () => `阵列 mic ${mic}`],
      ["mean", () => [1, 2, 3, 4, 5, 6], () => "6 麦平均"],
      ["aec", () => [mic], () => `AEC mic ${mic}`],
      ["ref", () => [7], () => "回声参考 ch7"],
    ];
    let source = "raw", samples = null;
    const buttons = h("nav", { class: "segmented", "aria-label": "声源" });

    const label = () => {
      buttons.replaceChildren(...sources.map(([key, , name]) => h("button", {
        type: "button", "aria-pressed": String(key === source), disabled: key === "aec" && !data.media.aec,
        onclick: () => { source = key; load(); },
      }, name())));
    };

    async function load(retried = false) {
      label();
      const [, channels, name] = sources.find(([key]) => key === source);
      const which = source === "aec" ? "aec" : "audio";
      if (!data.media[which]) {
        status.textContent = "这个会话没有发布这段音频。";
        return;
      }
      status.textContent = "读取中…";
      try {
        const span = await spanBytes(which, data.media[which], a, b);
        samples = mono(span, channels());
        const was = audio.src;
        audio.src = URL.createObjectURL(wavBlob(samples, span.info.rate));
        if (was) URL.revokeObjectURL(was);
        drawWave(canvas, samples);
        status.textContent = `${name()} · ${num(b - a, 2)} s · ${span.info.channels} 通道 ${span.info.rate} Hz`;
      } catch (error) {
        if (error.message === "expired" && !retried) {
          await refresh();
          return load(true);
        }
        status.textContent = "读不到音频：" + error.message;
      }
    }

    // Video: muted, seeked to the span, following the audio.
    const cams = Object.keys(data.media.video || {});
    let video = null, camPick = null;
    if (cams.length) {
      video = h("video", { muted: true, playsinline: true, preload: "metadata" });
      video.muted = true;
      const setCam = (cam) => {
        video.src = data.media.video[cam];
        video.addEventListener("loadedmetadata", () => { video.currentTime = a + (audio.currentTime || 0); }, { once: true });
      };
      camPick = h("select", null, cams.map((c) => h("option", { value: c }, c)));
      camPick.addEventListener("change", () => setCam(camPick.value));
      setCam(cams[0]);
      const follow = () => { if (Math.abs(video.currentTime - (a + audio.currentTime)) > 0.15) video.currentTime = a + audio.currentTime; };
      audio.addEventListener("play", () => { follow(); video.play().catch(() => {}); });
      audio.addEventListener("pause", () => video.pause());
      audio.addEventListener("seeked", follow);
      video.addEventListener("error", async () => {
        await refresh();
        if (data.media.video[camPick.value]) setCam(camPick.value);
      }, { once: true });
    }

    const tick = () => {
      if (!audio.duration) return;
      head.hidden = false;
      head.style.left = (100 * audio.currentTime) / audio.duration + "%";
      if (!audio.paused) requestAnimationFrame(tick);
    };
    audio.addEventListener("play", tick);
    audio.addEventListener("seeked", tick);
    wave.addEventListener("click", (event) => {
      if (!audio.duration) return;
      const box = wave.getBoundingClientRect();
      audio.currentTime = ((event.clientX - box.left) / box.width) * audio.duration;
    });

    const padPick = h("select", null, PADS.map((p) => h("option", { value: p, selected: p === pad }, p + " s")));
    padPick.addEventListener("change", () => { pad = Number(padPick.value); route(); });
    const micPick = h("select", null, [1, 2, 3, 4, 5, 6].map((c) => h("option", { value: c, selected: c === mic }, "mic " + c)));
    micPick.addEventListener("change", () => { mic = Number(micPick.value); load(); });

    const fields = Object.entries(g).filter(([k]) => !["seg_id", "text", "session"].includes(k));
    show(
      back(["#/", "概览"], [`#/s/${g.session}/${Math.max(0, Math.floor(a / WINDOW_S) * WINDOW_S)}`, g.session], [location.hash, g.seg_id, true]),
      h("p", { class: "hint" }, `${g.speaker || "robot"} · ${num(g.start, 2)}–${num(g.end, 2)} s（${num(g.dur, 2)} s）${g.flags ? " · " + g.flags : ""}`),
      h("p", { class: "rh-text" }, text(g.text)),
      data.nearby.length ? h("p", { class: "rh-near" }, "同一段里还有：" + data.nearby
        .map((n) => `${n.speaker || "robot"} ${num(n.start, 1)}–${num(n.end, 1)}：${n.text || ""}`).join(" | ")) : null,
      h("div", { class: "rh-toolbar" }, h("label", null, "前后", padPick), h("label", null, "通道", micPick),
        camPick ? h("label", null, "相机", camPick) : null),
      plot([g, ...data.nearby], levels, a, b, g.seg_id, [g.start, g.end]),
      buttons,
      h("div", { class: "rh-media" + (video ? " with-video" : "") }, h("div", null, wave, audio, status), video),
      h("details", null, h("summary", null, "字段"), kv(fields)),
    );
    load();
  }

  // --- routing ---------------------------------------------------------------

  function route() {
    const mine = ++generation;
    const parts = window.location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
    const work = parts[0] === "q" && parts[1] ? query(mine, parts[1], Math.max(0, parseInt(parts[2], 10) || 0))
      : parts[0] === "s" && parts[1] ? session(mine, parts[1], Math.max(0, parseInt(parts[2], 10) || 0))
        : parts[0] === "g" && parts[1] ? segment(mine, parts[1])
          : overview(mine);
    work.catch((error) => { if (mine === generation) failed(error); });
    window.scrollTo(0, 0);
  }

  window.addEventListener("hashchange", route);
  route();
})();
