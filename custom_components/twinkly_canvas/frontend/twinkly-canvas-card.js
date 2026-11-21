/*
 * Twinkly Canvas card: paint single LEDs of a Twinkly light from a dashboard.
 *
 * One file: no build step, no external resources. The card talks to the integration
 * through its websocket commands. LEDs count from 1 on screen and in selection text,
 * from 0 in the paint and find commands; canvases are base64 R,G,B,W per LED.
 */

const DOMAIN = "twinkly_canvas";
const PAGE = 100; // LEDs per page, shown in rows of ROW
const ROW = 25;
const SWATCHES = ["red", "silver", "gold", "green", "warm white", "off"];
const WARM = [0xff, 0xe3, 0xb0]; // the white LED on screen, at full W

/** The 0-based LEDs of a group's selection text: "1-20, 45 odd", "all", "every 3 from 2". */
function parseSelection(text, count) {
  // the integration's grammar; a group's text never names another group
  let spec = text.trim().split(/\s+/).join(" ");
  const filter = /[\s,]\s*(odd|even|every\s+(\d{1,6})(?:\s+from\s+(\d{1,6}))?)\s*$/i.exec(spec);
  spec = filter ? spec.slice(0, filter.index) : spec.replace(/,$/, "");
  let chosen = []; // numbers from 1
  for (const item of spec.split(",")) {
    const part = item.trim().toLowerCase();
    const range = part === "all" ? [part, 1, count] : /^(\d{1,6})(?:\s*-\s*(\d{1,6}))?$/.exec(part);
    if (!range) throw new Error(`'${item.trim()}' is not an LED number or range`);
    const [lo, hi] = [Number(range[1]), Number(range[2] ?? range[1])].sort((a, b) => a - b);
    if (lo < 1 || hi > count) throw new Error(`LED ${lo < 1 ? lo : hi} is outside 1-${count}`);
    for (let n = lo; n <= hi; n++) chosen.push(n);
  }
  if (filter) {
    const [, kind, step, from] = filter;
    const start = from ? Number(from) : Math.min(...chosen);
    chosen = chosen.filter((n) =>
      step ? n >= start && (n - start) % Number(step) === 0 : n % 2 === (/odd/i.test(kind) ? 1 : 0)
    );
  }
  return [...new Set(chosen)].sort((a, b) => a - b).map((n) => n - 1);
}

/** Compact 1-based text for 0-based LEDs, like the integration's format_selection: "1-3, 7". */
function formatSelection(indices) {
  const nums = [...new Set(indices)].sort((a, b) => a - b).map((i) => i + 1);
  const parts = [];
  for (let i = 0; i < nums.length; ) {
    let j = i;
    while (j + 1 < nums.length && nums[j + 1] === nums[j] + 1) j++;
    parts.push(i === j ? `${nums[i]}` : `${nums[i]}-${nums[j]}`);
    i = j + 1;
  }
  return parts.join(", ");
}

/** The CSS colour of [r, g, b, w]: the white channel shows as warm white. */
function shown([r, g, b, w]) {
  const mix = (c, k) => Math.min(255, Math.round(c + (w * k) / 255));
  return `rgb(${mix(r, WARM[0])}, ${mix(g, WARM[1])}, ${mix(b, WARM[2])})`;
}

/** What the lights show, from state.active; "" for a type this card does not know. */
function nowShowing({ active, mode }) {
  if (mode === "off" || active.type === "off") return "Off";
  const text = {
    painted: "Showing your painting",
    custom_effect: "Playing your moving effect",
    look: `Showing the look ${active.ref}`,
    builtin: `Playing built-in effect ${Number(active.ref) + 1}`,
    app_movie: "Playing a movie from the Twinkly app",
    device_mode: "Playing content from the Twinkly app",
  };
  return text[active.type] ?? "";
}

const el = (tag, className = "") => Object.assign(document.createElement(tag), { className });

const STYLE = `
  :host { display: block; }
  ha-card {
    display: block; padding: 16px;
    background: var(--ha-card-background, var(--card-background-color));
    border-radius: var(--ha-card-border-radius, 12px);
    color: var(--primary-text-color);
  }
  [hidden] { display: none !important; }
  button, input, select { font: inherit; color: inherit; }
  button { cursor: pointer; }
  button:disabled { cursor: default; opacity: 0.45; }
  :focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .error {
    margin-bottom: 12px; padding: 8px 12px; border-radius: 8px;
    border: 1px solid var(--error-color); color: var(--error-color);
  }
  .head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 4px 12px; }
  .name { font-size: 18px; font-weight: 500; }
  .now, .hint { font-size: 12px; color: var(--secondary-text-color); }
  .label {
    font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--secondary-text-color); margin: 16px 0 8px;
  }
  .bar { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin: 16px 0 8px; }
  .bar .label { margin: 0; }
  .overview { display: grid; gap: 4px; padding: 6px; border-radius: 8px; background: var(--secondary-background-color); }
  /* one bar per LED: dots of 2 px blur, a strip keeps light colours visible */
  .orow {
    display: grid; grid-template-columns: repeat(${PAGE}, minmax(0, 1fr)); width: 100%;
    margin: 0; padding: 0; border: none; border-radius: 3px; overflow: hidden; background: none;
  }
  .orow.current { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .orow:focus-visible { outline-color: var(--primary-text-color); }
  .orow i { height: 8px; }
  .pager { display: flex; align-items: center; gap: 6px; }
  .prow { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 6px; align-items: center; margin-bottom: 4px; }
  .rl { font-size: 10px; text-align: right; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  .dots {
    display: grid; grid-template-columns: repeat(${ROW}, minmax(0, 1fr)); gap: 3px;
    touch-action: none; user-select: none; -webkit-user-select: none;
  }
  .dot {
    aspect-ratio: 1; min-width: 0; margin: 0; padding: 0; border-radius: 50%;
    border: 1px solid var(--divider-color);
  }
  .dot[aria-pressed="true"] { box-shadow: 0 0 0 1px var(--card-background-color), 0 0 0 3px var(--primary-color); }
  .dot:focus-visible { outline-color: var(--primary-text-color); outline-offset: 3px; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .between { justify-content: space-between; }
  .field { flex: 1 1 0; display: flex; align-items: center; gap: 8px; font-size: 14px; }
  .field input {
    flex: 1; width: 100%; min-width: 0; padding: 6px 8px; border-radius: 8px;
    border: 1px solid var(--divider-color); background: none;
  }
  .chip, .icon, .btn { border: 1px solid var(--divider-color); background: none; font-size: 14px; }
  .chip { min-height: 32px; padding: 0 12px; border-radius: 16px; }
  .chip[aria-pressed="true"] {
    border-color: var(--primary-color);
    background: color-mix(in srgb, var(--primary-color) 20%, transparent);
  }
  select.chip { background: var(--card-background-color); }
  .stepper { display: flex; align-items: center; gap: 4px; }
  .icon { width: 32px; height: 32px; padding: 0; border-radius: 50%; font-size: 16px; }
  .sw { width: 32px; height: 32px; padding: 0; border-radius: 50%; border: 2px solid var(--divider-color); }
  input.sw { appearance: none; -webkit-appearance: none; overflow: hidden; background: none; }
  input.sw::-webkit-color-swatch-wrapper { padding: 0; }
  input.sw::-webkit-color-swatch { border: none; background-image: conic-gradient(red, yellow, lime, cyan, blue, magenta, red); }
  input.sw::-moz-color-swatch { border: none; background-image: conic-gradient(red, yellow, lime, cyan, blue, magenta, red); }
  .toggle { display: flex; align-items: center; gap: 8px; font-size: 14px; cursor: pointer; }
  .switch {
    appearance: none; -webkit-appearance: none; position: relative; flex: none; margin: 0;
    width: 36px; height: 20px; border-radius: 10px; background: var(--disabled-color, var(--divider-color));
    cursor: pointer; transition: background 0.2s;
  }
  .switch::before {
    content: ""; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%;
    background: var(--card-background-color); transition: transform 0.2s;
  }
  .switch:checked { background: var(--primary-color); }
  .switch:checked::before { transform: translateX(16px); }
  .btn { min-height: 36px; padding: 0 16px; border-radius: 18px; font-weight: 500; }
  .btn.primary { border-color: var(--primary-color); background: var(--primary-color); color: var(--text-primary-color); }
`;

// Static markup only: names from users and the device go in through textContent.
const BODY = `
  <div class="head"><span class="name"></span><span class="now"></span></div>
  <div class="bar"><span class="label total"></span><span class="hint">tap to jump</span></div>
  <div class="overview"></div>
  <div class="bar">
    <span class="label pagelabel"></span>
    <span class="pager">
      <button class="icon prev" aria-label="Previous page">&lsaquo;</button>
      <span class="hint pos"></span>
      <button class="icon next" aria-label="Next page">&rsaquo;</button>
    </span>
  </div>
  <div class="page"></div>
  <div class="label">Select</div>
  <div class="row">
    <label class="field">From <input class="from" type="number" inputmode="numeric" min="1"></label>
    <label class="field">to <input class="to" type="number" inputmode="numeric" min="1"></label>
  </div>
  <div class="row" style="margin-top: 8px">
    <button class="chip" data-filter="all">All</button>
    <button class="chip" data-filter="odd">Odd</button>
    <button class="chip" data-filter="even">Even</button>
    <span class="stepper">
      <button class="chip" data-filter="every">Every <span class="step"></span></button>
      <button class="icon minus" aria-label="Decrease step">&minus;</button>
      <button class="icon plus" aria-label="Increase step">+</button>
    </span>
    <select class="chip group" aria-label="Select a saved group"></select>
    <button class="chip save">Save as group</button>
  </div>
  <div class="label colourlabel"></div>
  <div class="row swatches"></div>
  <div class="row between" style="margin-top: 16px">
    <label class="toggle"><input class="switch live" type="checkbox" role="switch" checked>Live on tree</label>
    <span class="row">
      <button class="btn primary apply" hidden>Apply</button>
      <button class="btn find">Find on tree</button>
    </span>
  </div>
`;

class TwinklyCanvasCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" }).innerHTML =
      `<style>${STYLE}</style><ha-card><div class="error" role="alert" hidden></div><div class="body"></div></ha-card>`;
    this._error = this.shadowRoot.querySelector(".error");
    this._body = this.shadowRoot.querySelector(".body");
    this._sel = new Set(); // 0-based
    this._step = 3;
    this._live = true;
    this._paints = 0;
    this._endDrag = () => (this._drag = undefined);
  }

  static getStubConfig(hass) {
    const ids = Object.keys(hass.entities ?? {});
    const light = ids.find((id) => id.startsWith("light.") && hass.entities[id].platform === DOMAIN);
    return { entity: light ?? "" };
  }

  setConfig(config) {
    if (!config?.entity) throw new Error("Set entity to a Twinkly Canvas light");
    this._config = config;
    this._stateObj = null; // never a state: the next hass loads this light
    this._built = this._canvas = undefined;
    this._sel = new Set();
    if (this._hass) this.hass = this._hass;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const stateObj = hass.states[this._config.entity];
    if (stateObj === this._stateObj) return;
    this._stateObj = stateObj;
    this._load(); // the light changed: other clients or automations may have painted
  }

  getCardSize() {
    return 8;
  }

  connectedCallback() {
    window.addEventListener("pointerup", this._endDrag);
    window.addEventListener("pointercancel", this._endDrag);
  }

  disconnectedCallback() {
    window.removeEventListener("pointerup", this._endDrag);
    window.removeEventListener("pointercancel", this._endDrag);
    this._drag = undefined;
  }

  async _ws(command, data = {}) {
    const msg = { type: `${DOMAIN}/${command}`, entity_id: this._config.entity, ...data };
    const result = await this._hass.callWS(msg);
    this._fail(null);
    return result;
  }

  /** Run a command the user asked for; false (and the error shown) when it fails. */
  async _act(command, data) {
    try {
      await this._ws(command, data);
      return true;
    } catch (err) {
      this._fail(err);
      return false;
    }
  }

  _fail(err) {
    this._error.hidden = !err;
    this._error.textContent = err ? err.message || String(err) : "";
  }

  async _load() {
    const paints = this._paints;
    let state;
    try {
      state = await this._ws("state");
    } catch (err) {
      this._fail(err);
      return;
    }
    // a paint sent after this request may be missing from its canvas: keep the card's own
    if (paints === this._paints || this._canvas?.length !== state.led_count * 4) {
      this._canvas = Uint8Array.from(atob(state.canvas), (c) => c.charCodeAt(0));
    }
    this._state = state;
    if (this._built !== state.led_count) this._build();
    this._$.name.textContent = this._stateObj?.attributes.friendly_name ?? this._config.entity;
    this._$.now.textContent = nowShowing(state);
    const groups = Object.keys(state.groups);
    this._$.group.replaceChildren(
      new Option(groups.length ? "Group" : "No groups yet", ""),
      ...groups.map((name) => new Option(name, name))
    );
    this._$.group.disabled = !groups.length;
    this._draw();
  }

  _build() {
    const { led_count: count, colors } = this._state;
    this._built = count;
    this._pages = Math.ceil(count / PAGE);
    this._page = 0;
    this._sel = new Set([...this._sel].filter((i) => i < count));
    this._body.innerHTML = BODY;
    const q = (selector) => this._body.querySelector(selector);
    const $ = (this._$ = {});
    for (const name of [
      "name", "now", "total", "pagelabel", "prev", "pos", "next", "from", "to", "step",
      "minus", "plus", "group", "save", "colourlabel", "swatches", "live", "apply", "find",
    ]) {
      $[name] = q(`.${name}`);
    }
    $.total.textContent = `All ${count} LEDs`;
    $.from.max = $.to.max = count;
    $.from.placeholder = "1";
    $.to.placeholder = String(count);

    // the overview: one strip per page, a bar per LED
    this._minis = [];
    this._orows = [];
    for (let p = 0; p < this._pages; p++) {
      const last = Math.min(count, (p + 1) * PAGE);
      const row = q(".overview").appendChild(el("button", "orow"));
      row.setAttribute("aria-label", `Show LEDs ${p * PAGE + 1}-${last}`);
      row.addEventListener("click", () => this._show(p));
      for (let i = p * PAGE; i < last; i++) this._minis.push(row.appendChild(el("i")));
      this._orows.push(row);
    }

    // the page: 4 rows of 25 dots, reused for every page
    this._dots = [];
    this._labels = [];
    for (let r = 0; r < PAGE / ROW; r++) {
      const row = q(".page").appendChild(el("div", "prow"));
      this._labels.push(row.appendChild(el("span", "rl")));
      const dots = row.appendChild(el("div", "dots"));
      for (let c = 0; c < ROW; c++) {
        const dot = dots.appendChild(el("button", "dot"));
        const led = () => this._page * PAGE + r * ROW + c;
        dot.addEventListener("pointerdown", (e) => {
          dot.releasePointerCapture(e.pointerId); // touch captures it: let the next dots see it
          this._press(led(), e, true);
        });
        dot.addEventListener("pointerenter", (e) => this._enter(led(), e));
        dot.addEventListener("click", (e) => e.detail === 0 && this._press(led(), e)); // keyboard
        this._dots.push(dot);
      }
    }

    $.prev.addEventListener("click", () => this._show(this._page - 1));
    $.next.addEventListener("click", () => this._show(this._page + 1));
    $.from.addEventListener("input", () => this._selectRange());
    $.to.addEventListener("input", () => this._selectRange());
    for (const chip of this._body.querySelectorAll("[data-filter]")) {
      chip.addEventListener("click", () => {
        this._filter = chip.dataset.filter;
        this._selectRange();
      });
    }
    $.minus.addEventListener("click", () => this._setStep(this._step - 1));
    $.plus.addEventListener("click", () => this._setStep(this._step + 1));
    $.group.addEventListener("change", () => this._selectGroup());
    $.save.addEventListener("click", () => this._saveGroup());
    $.live.addEventListener("change", () => this._setLive($.live.checked));
    $.apply.addEventListener("click", () => this._commit());
    $.find.addEventListener("click", () => this._find());

    this._paintControls = [];
    for (const name of SWATCHES.filter((n) => n in colors)) {
      const swatch = $.swatches.appendChild(el("button", "sw"));
      swatch.title = name[0].toUpperCase() + name.slice(1);
      swatch.setAttribute("aria-label", swatch.title);
      swatch.style.background = shown(colors[name]);
      swatch.addEventListener("click", () => this._paint(colors[name]));
      this._paintControls.push(swatch);
    }
    const picker = $.swatches.appendChild(el("input", "sw"));
    picker.type = "color";
    picker.title = "Custom colour";
    picker.setAttribute("aria-label", picker.title);
    picker.addEventListener("change", () => {
      const hex = picker.value;
      picker.value = "#000000"; // so picking the same colour again still counts as a change
      this._paint([1, 3, 5].map((k) => parseInt(hex.slice(k, k + 2), 16)).concat(0));
    });
    this._paintControls.push(picker);
  }

  /** Bring every dot and control in line with the canvas, the selection and the page. */
  _draw() {
    const count = this._state.led_count;
    const page = this._page;
    const $ = this._$;
    const color = (i) => shown(this._canvas.subarray(i * 4, i * 4 + 4));
    this._minis.forEach((mini, i) => (mini.style.background = color(i)));
    this._orows.forEach((row, p) => row.classList.toggle("current", p === page));
    $.pagelabel.textContent = `LEDs ${page * PAGE + 1}-${Math.min(count, (page + 1) * PAGE)}`;
    $.pos.textContent = `${page + 1} of ${this._pages}`;
    $.prev.disabled = page === 0;
    $.next.disabled = page === this._pages - 1;
    this._labels.forEach((label, r) => {
      const first = page * PAGE + r * ROW;
      label.textContent = first + 1;
      label.parentElement.hidden = first >= count;
    });
    this._dots.forEach((dot, k) => {
      const i = page * PAGE + k;
      dot.hidden = i >= count;
      if (i >= count) return;
      dot.style.background = color(i);
      dot.setAttribute("aria-label", `LED ${i + 1}`);
      dot.setAttribute("aria-pressed", String(this._sel.has(i)));
    });
    for (const chip of this._body.querySelectorAll("[data-filter]")) {
      chip.setAttribute("aria-pressed", String(chip.dataset.filter === this._filter));
    }
    $.step.textContent = this._step;
    const n = this._sel.size;
    $.colourlabel.textContent = n ? `Colour for ${n} LED${n === 1 ? "" : "s"}` : "Colour: select LEDs first";
    for (const control of this._paintControls) control.disabled = !n;
    $.save.disabled = !n;
    $.find.disabled = !n || this._finding;
    $.find.textContent = this._finding ? "Finding..." : "Find on tree";
    $.apply.hidden = this._live;
  }

  _show(page) {
    this._page = Math.max(0, Math.min(this._pages - 1, page));
    this._draw();
  }

  /** A dot pressed: toggle it and start a drag, or with Shift add the range from the anchor. */
  _press(i, e, pointer = false) {
    if (e.shiftKey && this._anchor !== undefined) {
      const [a, b] = [this._anchor, i].sort((x, y) => x - y);
      for (let k = a; k <= b; k++) this._sel.add(k);
    } else {
      this._anchor = i;
      const add = !this._sel.has(i);
      this._mark(i, add);
      if (pointer) this._drag = add; // the dots dragged over follow the first one
    }
    this._draw();
  }

  _enter(i, e) {
    if (this._drag === undefined || !e.buttons) return;
    this._mark(i, this._drag);
    this._draw();
  }

  _mark(i, selected) {
    if (selected) this._sel.add(i);
    else this._sel.delete(i);
  }

  /** Stay on this page if it shows a selected LED, else go to the first one. */
  _reveal() {
    const leds = [...this._sel];
    const here = !leds.length || leds.some((i) => Math.floor(i / PAGE) === this._page);
    this._show(here ? this._page : Math.floor(Math.min(...leds) / PAGE));
  }

  /** Select the From-to range (empty ends: the first and last LED), narrowed by the filter. */
  _selectRange() {
    const count = this._state.led_count;
    const read = (input, empty) => {
      const value = input.valueAsNumber;
      return Number.isNaN(value) ? empty : Math.max(1, Math.min(count, Math.round(value)));
    };
    const [a, b] = [read(this._$.from, 1), read(this._$.to, count)].sort((x, y) => x - y);
    const keep = {
      odd: (n) => n % 2 === 1,
      even: (n) => n % 2 === 0,
      every: (n) => (n - a) % this._step === 0,
    }[this._filter];
    this._sel = new Set();
    for (let n = a; n <= b; n++) if (!keep || keep(n)) this._sel.add(n - 1);
    this._reveal();
  }

  _setStep(step) {
    this._step = Math.max(2, step);
    if (this._filter === "every") this._selectRange();
    else this._draw();
  }

  _selectGroup() {
    const name = this._$.group.value;
    this._$.group.value = ""; // back to the placeholder: the same group can be picked again
    if (!name) return;
    let leds;
    try {
      leds = parseSelection(this._state.groups[name], this._state.led_count);
    } catch (err) {
      this._fail(err);
      return;
    }
    this._sel = new Set(leds);
    this._filter = undefined;
    this._$.from.value = this._$.to.value = "";
    this._reveal();
  }

  async _saveGroup() {
    const name = window.prompt("Name of the new group")?.trim();
    if (!name) return;
    if (await this._act("group/save", { name, leds: formatSelection(this._sel) })) this._load();
  }

  /** Paint the selection: the dots change at once, the lights when live is on. */
  async _paint(color) {
    const leds = [...this._sel].sort((a, b) => a - b);
    for (const i of leds) this._canvas.set(color, i * 4);
    this._paints++;
    this._unapplied = !this._live;
    this._draw();
    try {
      await this._ws("paint", { leds, color, live: this._live });
    } catch (err) {
      await this._load(); // show what the integration kept
      this._fail(err);
    }
  }

  _setLive(live) {
    this._live = live;
    this._draw();
    if (live && this._unapplied) this._commit(); // the tree catches up with the card
  }

  async _commit() {
    if (await this._act("commit")) this._unapplied = false;
  }

  async _find() {
    this._finding = true;
    this._draw();
    await this._act("find", { leds: [...this._sel].sort((a, b) => a - b) });
    this._finding = false;
    this._draw();
  }
}

if (!customElements.get("twinkly-canvas-card")) {
  customElements.define("twinkly-canvas-card", TwinklyCanvasCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "twinkly-canvas-card",
    name: "Twinkly Canvas",
    description: "Paint single LEDs of a Twinkly light.",
  });
}
