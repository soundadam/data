// The sample bank viewer (kind corpus): a few dozen clips of every ASR corpus on hand.
//
//   #/          the corpora, grouped by kind of recording
//   #/c/<id>    one corpus: its facts, and its clips group by group
//
// Each clip is Ogg Opus, one file per signal (far, near or mono). Playing one
// fetches its bytes once: the <audio> plays them from a blob, and the page
// decodes the same bytes for the clip's spectrogram. Links expire, so a failed
// fetch asks for the corpus again and retries once.
(function () {
  "use strict";

  const main = document.querySelector("main.console");
  const root = document.getElementById("view");
  const API = main.dataset.api;
  const SIGNALS = { far: "远场", near: "近讲", mono: "单通道" };
  const STATUS = { sampled: "", excluded: "暂不使用", listed: "只列出" };
  const N_FFT = 512, HOP = 160, SPEC_H = 96, FLOOR_DB = -90;
  let generation = 0;
  let bank = null; // corpora.json, fetched once
  const audio = new Audio();
  let playing = null; // {button, url}

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

  function sec(value) {
    return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) + " s" : "";
  }

  // --- the corpora -------------------------------------------------------------

  function overview() {
    const out = [h("p", { class: "hint lead" },
      `每个语料抽几组、每组十来段，每段最长 20 秒，Opus ${bank.kbps} kbps、${bank.sr / 1000} kHz 单声道。` +
      "每段记着从哪台机器、哪个文件、哪几秒剪下来的。")];
    for (const kind of bank.kinds) {
      const corpora = bank.corpora.filter((c) => c.kind === kind.id);
      if (!corpora.length) continue;
      out.push(h("section", { class: "cb-kind" },
        h("h2", null, kind.title),
        h("p", { class: "hint" }, kind.note),
        h("div", { class: "cb-cards" }, corpora.map(card))));
    }
    out.push(h("p", { class: "hint" }, `版本 ${bank.version} · ${bank.license || ""}`));
    root.replaceChildren(...out);
  }

  function card(c) {
    const status = STATUS[c.status] ?? c.status;
    const body = [
      h("div", { class: "cb-title" }, h("strong", null, c.title),
        status ? h("span", { class: "cb-badge" }, status) : null,
        c.lang !== "zh" ? h("span", { class: "cb-badge" }, c.lang) : null),
      h("dl", null,
        h("dt", null, "时长"), h("dd", null, c.hours),
        h("dt", null, "通道"), h("dd", null, c.channels),
        h("dt", null, "标注"), h("dd", null, c.labels),
        h("dt", null, "许可"), h("dd", null, c.license)),
      c.note ? h("p", { class: "hint" }, c.note) : null,
      c.clips ? h("p", { class: "cb-count" }, `${c.clips} 段 · ${c.groups.join(" / ")}`) : null,
    ];
    return c.clips
      ? h("a", { class: "cb-card", href: "#/c/" + c.id }, body)
      : h("div", { class: "cb-card cb-quiet" }, body);
  }

  // --- one corpus --------------------------------------------------------------

  function corpusPage(mine, data) {
    const c = data.corpus;
    const groups = new Map();
    for (const clip of data.clips) {
      if (!groups.has(clip.group)) groups.set(clip.group, []);
      groups.get(clip.group).push(clip);
    }
    const kind = bank.kinds.find((k) => k.id === c.kind);
    const out = [
      h("p", null, h("a", { href: "#/" }, "← 全部语料")),
      h("h2", null, c.title, " ", h("span", { class: "hint" }, kind ? kind.title : c.kind)),
      h("p", { class: "hint lead" }, [c.hours, c.channels, c.format, c.labels, c.license].join(" · ")),
      c.note ? h("p", { class: "hint" }, c.note) : null,
    ];
    for (const [name, clips] of groups) {
      out.push(h("section", { class: "cb-group" },
        h("h3", null, name, " ", h("span", { class: "hint" }, `${clips.length} 段`)),
        h("ol", { class: "cb-clips" }, clips.map((clip) => clipRow(mine, c.id, clip)))));
    }
    root.replaceChildren(...out);
  }

  function clipRow(mine, corpus, clip) {
    const canvas = h("canvas", { class: "cb-spec", height: SPEC_H, hidden: true });
    const buttons = Object.entries(clip.signals).map(([name]) => {
      const b = h("button", { type: "button", class: "button secondary cb-play", "aria-pressed": "false" },
        SIGNALS[name] || name);
      b.addEventListener("click", () => play(mine, corpus, clip, name, b, canvas));
      return b;
    });
    const where = clip.end != null && clip.start != null
      ? `${clip.src} @ ${clip.start.toFixed(2)}–${clip.end.toFixed(2)} s` : clip.src;
    const extra = Object.entries(clip.extra).map(([k, v]) => `${k} ${v}`).join(" · ");
    return h("li", { class: "cb-clip" },
      h("div", { class: "cb-head" }, buttons,
        h("span", { class: "cb-meta" }, [sec(clip.dur), clip.speaker, extra, clip.flags].filter(Boolean).join(" · "))),
      h("p", { class: "cb-text" }, clip.text || "（无文本）"),
      canvas,
      h("p", { class: "cb-src", title: clip.clip_id }, where));
  }

  async function fetchClip(corpus, clip, name) {
    let response = await fetch(linked(clip.signals[name]), { credentials: "same-origin" });
    if (!response.ok) {
      // the link may have expired: ask for fresh ones
      const again = await get("/corpus/" + corpus);
      const fresh = again.clips.find((c) => c.clip_id === clip.clip_id);
      if (!fresh || !fresh.signals[name]) throw new Error(String(response.status));
      clip.signals = fresh.signals;
      response = await fetch(linked(clip.signals[name]), { credentials: "same-origin" });
      if (!response.ok) throw new Error(String(response.status));
    }
    return response.arrayBuffer();
  }

  async function play(mine, corpus, clip, name, button, canvas) {
    if (playing && playing.button === button && !audio.paused) {
      audio.pause();
      return;
    }
    if (playing) playing.button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-pressed", "true");
    button.setAttribute("aria-busy", "true");
    try {
      const bytes = await fetchClip(corpus, clip, name);
      if (mine !== generation) return;
      if (playing && playing.url) URL.revokeObjectURL(playing.url);
      const url = URL.createObjectURL(new Blob([bytes], { type: "audio/ogg" }));
      playing = { button, url };
      audio.src = url;
      audio.play().catch(() => {});
      spectrogram(bytes.slice(0), canvas);
    } catch (error) {
      button.setAttribute("aria-pressed", "false");
      button.title = "读不到：" + error.message;
    } finally {
      button.removeAttribute("aria-busy");
    }
  }

  audio.addEventListener("pause", () => { if (playing) playing.button.setAttribute("aria-pressed", "false"); });
  audio.addEventListener("play", () => { if (playing) playing.button.setAttribute("aria-pressed", "true"); });

  // --- spectrogram -------------------------------------------------------------

  let decoder = null;

  async function spectrogram(bytes, canvas) {
    let x;
    try {
      decoder = decoder || new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 1, 16000);
      x = (await decoder.decodeAudioData(bytes)).getChannelData(0);
    } catch {
      return; // a browser that cannot decode Ogg Opus: sound only
    }
    const frames = Math.max(1, Math.floor((x.length - N_FFT) / HOP) + 1);
    const bins = N_FFT / 2;
    canvas.width = frames;
    canvas.hidden = false;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(frames, SPEC_H);
    const win = new Float32Array(N_FFT).map((_, i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / N_FFT));
    const re = new Float32Array(N_FFT), im = new Float32Array(N_FFT);
    for (let f = 0; f < frames; f++) {
      for (let i = 0; i < N_FFT; i++) {
        re[i] = (x[f * HOP + i] || 0) * win[i];
        im[i] = 0;
      }
      fft(re, im);
      for (let row = 0; row < SPEC_H; row++) {
        const k = Math.min(bins - 1, Math.floor(((SPEC_H - 1 - row) / SPEC_H) * bins));
        const db = 10 * Math.log10(re[k] * re[k] + im[k] * im[k] + 1e-12);
        const v = Math.min(1, Math.max(0, (db - FLOOR_DB) / -FLOOR_DB));
        const [r, g, b] = magma(v);
        const o = (row * frames + f) * 4;
        img.data[o] = r; img.data[o + 1] = g; img.data[o + 2] = b; img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  function fft(re, im) {
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) {
        [re[i], re[j]] = [re[j], re[i]];
        [im[i], im[j]] = [im[j], im[i]];
      }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const a = (-2 * Math.PI) / len, wr = Math.cos(a), wi = Math.sin(a);
      for (let i = 0; i < n; i += len) {
        let cr = 1, ci = 0;
        for (let j = 0; j < len / 2; j++) {
          const p = i + j, q = p + len / 2;
          const tr = re[q] * cr - im[q] * ci, ti = re[q] * ci + im[q] * cr;
          re[q] = re[p] - tr; im[q] = im[p] - ti;
          re[p] += tr; im[p] += ti;
          [cr, ci] = [cr * wr - ci * wi, cr * wi + ci * wr];
        }
      }
    }
  }

  // magma, five stops, as in the RoboHEARD sheets
  const STOPS = [[0, 0, 4], [81, 18, 124], [183, 55, 121], [252, 137, 97], [252, 253, 191]];

  function magma(v) {
    const t = v * (STOPS.length - 1), i = Math.min(STOPS.length - 2, Math.floor(t)), u = t - i;
    return STOPS[i].map((c, j) => Math.round(c + (STOPS[i + 1][j] - c) * u));
  }

  // --- routing -----------------------------------------------------------------

  async function route() {
    const mine = ++generation;
    audio.pause();
    try {
      if (!bank) bank = await get("/corpora");
      const asked = (location.hash.match(/^#\/c\/([a-z0-9-]+)$/) || [])[1];
      if (!asked) {
        if (mine === generation) overview();
        return;
      }
      const data = await get("/corpus/" + asked);
      if (mine === generation) corpusPage(mine, data);
    } catch (error) {
      if (mine === generation) failed(error);
    }
  }

  window.addEventListener("hashchange", route);
  route();
})();
