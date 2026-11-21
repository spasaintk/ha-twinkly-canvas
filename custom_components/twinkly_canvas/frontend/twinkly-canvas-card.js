/*
 * Twinkly Canvas card: paint single LEDs of a Twinkly light from a dashboard, play moving
 * effects and saved looks. Three tabs (Colours, Moving, Looks) and a visual editor.
 *
 * One file: no build step, no external resources. The card talks to the integration
 * through its websocket commands. LEDs count from 1 on screen and in selection text,
 * from 0 in the paint and find commands; canvases and preview frames are base64
 * R,G,B,W per LED.
 */

const DOMAIN = "twinkly_canvas";
const PAGE = 100; // LEDs per page, shown in rows of ROW
const ROW = 25;
const SWATCHES = ["red", "silver", "gold", "green", "warm white", "off"];
const WARM = [0xff, 0xe3, 0xb0]; // the white LED on screen, at full W
const SLOTS = 3; // colours of a moving effect
const SAMPLES = 64; // LEDs in a look's preview strip
const EFFECTS = {
  twinkle: "Twinkle", chase: "Chase", odd_even_swap: "Odd/even swap", wave: "Wave",
  comet: "Comet", candy_cane: "Candy cane", breathe: "Breathe", rainbow: "Rainbow",
};
const EDITOR_SCHEMA = [
  { name: "entity", selector: { entity: { integration: DOMAIN, domain: "light" } } },
];

// a trailing filter, after a space or a comma: "1-20 odd", "1-20,every 3 from 2"
const FILTER = /[\s,]\s*(odd|even|every\s+(\d{1,6})(?:\s+from\s+(\d{1,6}))?)\s*$/i;

/** The 0-based LEDs of a group's selection text: "1-20, 45 odd", "all", "even", "every 3 from 2". */
function parseSelection(text, count) {
  // the integration's grammar; a group's text never names another group
  let spec = text.trim().split(/\s+/).join(" ");
  if (FILTER.exec(` ${spec}`)?.index === 0) spec = `all ${spec}`; // a filter alone: all LEDs
  const filter = FILTER.exec(spec);
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
    if (step && Number(step) < 1) throw new Error("'every' needs a step of at least 1");
    const start = from ? Number(from) : Math.min(...chosen);
    chosen = chosen.filter((n) =>
      step ? n >= start && (n - start) % Number(step) === 0 : n % 2 === (/odd/i.test(kind) ? 1 : 0)
    );
  }
  if (!chosen.length) throw new Error("Selection matches no LEDs");
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

/** A colour as a screen reader says it: its name when it is a named colour, else as shown. */
function colourName(rgbw, colors) {
  return Object.keys(colors).find((name) => colors[name].every((v, k) => v === rgbw[k])) ?? shown(rgbw);
}

/** What the lights show, from state.active; "" for a type this card does not know. */
function nowShowing({ active, mode, app_movies: movies }) {
  if (mode === "off" || active.type === "off") return "Off";
  const movie = movies.find((m) => m.unique_id === active.ref);
  const text = {
    painted: "Showing your painting",
    custom_effect: "Playing your moving effect",
    look: `Showing the look ${active.ref}`,
    builtin: `Playing built-in effect ${Number(active.ref) + 1}`,
    app_movie: `Playing ${movie?.name || "a movie"} from the Twinkly app`,
    device_mode: "Playing content from the Twinkly app",
  };
  return text[active.type] ?? "";
}

/** Bytes of a base64 canvas or preview frame. */
const bytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

/** A look name as the integration matches it: ignoring case and surrounding spaces. */
const nameKey = (name) => name.trim().toLowerCase();

const el = (tag, className = "") => Object.assign(document.createElement(tag), { className });
const textEl = (tag, className, textContent) => Object.assign(el(tag, className), { textContent });

/** A marker such as "playing"; the element it marks says so with aria-current. */
function badge(text) {
  const span = textEl("span", "badge", text);
  span.setAttribute("aria-hidden", "true");
  return span;
}

/** One tab stop in a group of controls: the stop gets tabindex 0, the others -1. */
const rove = (items, stop) => items.forEach((item) => (item.tabIndex = item === stop ? 0 : -1));

/** The index a key moves focus to, from moves ({ key: index }); undefined for other keys. */
function keyTarget(e, moves) {
  if (e.altKey || e.ctrlKey || e.metaKey || !Object.hasOwn(moves, e.key)) return undefined;
  e.preventDefault(); // the page does not scroll
  return moves[e.key];
}

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
  button:disabled, button[aria-disabled="true"] { cursor: default; opacity: 0.45; }
  :focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .error {
    margin-bottom: 12px; padding: 8px 12px; border-radius: 8px;
    border: 1px solid var(--error-color); color: var(--error-color);
  }
  .head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 4px 12px; }
  .name { font-size: 18px; font-weight: 500; }
  .now, .hint { font-size: 12px; color: var(--secondary-text-color); }
  .tabs {
    display: flex; gap: 4px; margin-top: 12px; padding: 3px; border-radius: 10px;
    background: var(--secondary-background-color);
  }
  .tab {
    flex: 1; min-height: 32px; border: none; border-radius: 8px; background: none;
    font-size: 14px; color: var(--secondary-text-color);
  }
  .tab[aria-selected="true"] {
    background: var(--card-background-color); color: var(--primary-text-color); font-weight: 500;
  }
  .label {
    font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--secondary-text-color); margin: 16px 0 8px;
  }
  .section { margin-top: 20px; border-top: 1px solid var(--divider-color); }
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
  .orow i, .strip i { height: 8px; }
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
  .chip { min-height: 32px; max-width: 100%; padding: 0 12px; border-radius: 16px; overflow-wrap: anywhere; }
  .chip[aria-pressed="true"], .chip[aria-current="true"] {
    border-color: var(--primary-color);
    background: color-mix(in srgb, var(--primary-color) 20%, transparent);
  }
  select.chip { background: var(--card-background-color); }
  select.wide { width: 100%; }
  .badge {
    margin-left: 6px; padding: 1px 6px; border-radius: 6px; font-size: 11px; font-weight: 500;
    background: var(--primary-color); color: var(--text-primary-color);
  }
  .sub { margin: 4px 0 8px; }
  .movies { margin-top: 12px; }
  .stepper { display: flex; align-items: center; gap: 4px; }
  .icon { flex: none; width: 32px; height: 32px; padding: 0; border-radius: 50%; font-size: 16px; }
  .sw { width: 32px; height: 32px; padding: 0; border-radius: 50%; border: 2px solid var(--divider-color); }
  .sw.empty { border-style: dashed; border-color: var(--secondary-text-color); background: none; }
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
  .speed { display: block; width: 100%; margin: 0; accent-color: var(--primary-color); }
  .meter { height: 6px; margin-top: 6px; border-radius: 3px; overflow: hidden; background: var(--secondary-background-color); }
  .meter div { height: 100%; background: var(--primary-color); }
  .look {
    display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 4px 8px;
    padding: 4px 0; border-bottom: 1px solid var(--divider-color);
  }
  .apply-look {
    display: grid; gap: 6px; min-width: 0; padding: 6px; border: none; border-radius: 8px;
    background: none; text-align: left;
  }
  .title { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px; }
  .lname { font-weight: 500; overflow-wrap: anywhere; }
  .kind { margin-left: auto; padding-left: 8px; }
  .strip {
    display: grid; grid-template-columns: repeat(${SAMPLES}, minmax(0, 1fr));
    padding: 3px; border-radius: 6px; background: var(--secondary-background-color);
  }
  .menu { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 8px; padding: 0 6px 8px; }
  .gchip { display: inline-flex; align-items: center; gap: 2px; padding-right: 4px; }
  .gchip button {
    width: 24px; height: 24px; padding: 0; border: none; border-radius: 50%; background: none;
    font-size: 16px; line-height: 1;
  }
`;

// Static markup only: names from users and the device go in through textContent.
const BODY = `
  <div class="head"><span class="name"></span><span class="now"></span></div>
  <div class="tabs" role="tablist">
    <button class="tab" role="tab" id="tab-colours" aria-controls="panel-colours" data-tab="colours">Colours</button>
    <button class="tab" role="tab" id="tab-moving" aria-controls="panel-moving" data-tab="moving">Moving</button>
    <button class="tab" role="tab" id="tab-looks" aria-controls="panel-looks" data-tab="looks">Looks</button>
  </div>
  <div class="leds">
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
  </div>
  <div role="tabpanel" id="panel-colours" data-tab="colours" aria-labelledby="tab-colours">
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
        <button class="btn showpainting" hidden>Show painting</button>
        <button class="btn primary apply" hidden>Apply</button>
        <button class="btn find">Find on tree</button>
      </span>
    </div>
  </div>
  <div role="tabpanel" id="panel-moving" data-tab="moving" aria-labelledby="tab-moving">
    <div class="label">Make your own</div>
    <div class="row kinds"></div>
    <div class="label">Colours</div>
    <div class="row"><span class="row slots"></span><span class="hint slothint"></span></div>
    <div class="row mswatches" style="margin-top: 8px"></div>
    <div class="label">Runs on</div>
    <select class="chip wide runs" aria-label="Runs on"></select>
    <label class="toggle" style="margin-top: 12px">
      <input class="switch keep" type="checkbox" role="switch" checked>Other LEDs keep their colours
    </label>
    <div class="label">Speed</div>
    <input class="speed" type="range" min="0" max="100" value="50" aria-label="Speed">
    <div class="row between hint"><span>slow</span><span>fast</span></div>
    <div class="row between" style="margin-top: 16px">
      <button class="btn preview">Preview</button>
      <button class="btn primary play">Play on tree</button>
    </div>
    <div class="section">
      <div class="label">Already on the lights</div>
      <div class="builtins" role="group" aria-labelledby="builtins-label">
        <div class="hint sub" id="builtins-label">Built-in effects</div>
        <div class="row"></div>
      </div>
      <div class="movies" role="group" aria-labelledby="movies-label">
        <div class="hint sub" id="movies-label">From the Twinkly app</div>
        <div class="row"></div>
      </div>
      <div class="hint memtext" style="margin-top: 12px"></div>
      <div class="meter" aria-hidden="true"><div class="meterbar"></div></div>
    </div>
  </div>
  <div role="tabpanel" id="panel-looks" data-tab="looks" aria-labelledby="tab-looks">
    <div class="label">Looks you saved</div>
    <div class="looks"></div>
    <div class="row between" style="margin-top: 12px">
      <span class="hint lookhint"></span>
      <button class="btn primary savelook">Save what's on now</button>
    </div>
    <div class="section">
      <div class="label">Groups you saved</div>
      <div class="row groups"></div>
      <div class="hint grouphint" style="margin-top: 8px"></div>
    </div>
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
    this._tab = "colours";
    this._slots = []; // the moving effect's colours: { color, name }
    this._ticket = 0; // counts preview stops: a render still on its way is dropped
    this._endDrag = () => (this._drag = undefined);
  }

  static getStubConfig(hass) {
    const ids = Object.keys(hass.entities ?? {});
    const light = ids.find((id) => id.startsWith("light.") && hass.entities[id].platform === DOMAIN);
    return { entity: light ?? "" };
  }

  static getConfigElement() {
    return document.createElement("twinkly-canvas-card-editor");
  }

  setConfig(config) {
    if (!config?.entity) throw new Error("Set entity to a Twinkly Canvas light");
    this._config = config;
    this._stateObj = null; // never a state: the next hass loads this light
    this._built = this._canvas = undefined;
    this._sel = new Set();
    this._fail(null); // the user changed the card: an older error no longer applies
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
    // HA re-attaches cached cards when the user comes back to the view: show what changed
    if (this._config && this._hass) this._load();
  }

  disconnectedCallback() {
    window.removeEventListener("pointerup", this._endDrag);
    window.removeEventListener("pointercancel", this._endDrag);
    this._drag = undefined;
    this._stopPreview();
  }

  async _ws(command, data = {}) {
    const msg = { type: `${DOMAIN}/${command}`, entity_id: this._config.entity, ...data };
    return this._hass.callWS(msg);
  }

  /** Run a command the user asked for; false (and the error shown) when it fails. */
  async _act(command, data) {
    this._stopPreview(); // another action ends a preview
    this._fail(null); // and replaces an error the user has seen
    try {
      await this._ws(command, data);
      return true;
    } catch (err) {
      this._fail(err);
      return false;
    }
  }

  /** Run a command, then show the state it left. */
  async _run(command, data) {
    if (await this._act(command, data)) await this._load();
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
      this._canvas = bytes(state.canvas);
    }
    this._state = state;
    if (this._built !== state.led_count) this._build();
    this._$.name.textContent = this._stateObj?.attributes.friendly_name ?? this._config.entity;
    this._$.now.textContent = nowShowing(state);
    // the light plays something else: Show painting (a commit) brings the painting back
    const { active, mode } = state;
    this._$.showpainting.hidden = mode === "off" || ["off", "painted"].includes(active.type);
    const groups = Object.keys(state.groups);
    this._$.group.replaceChildren(
      new Option(groups.length ? "Group" : "No groups yet", ""),
      ...groups.map((name) => new Option(name, name))
    );
    this._$.group.disabled = !groups.length;
    this._fillRuns(groups);
    this._renderLists();
    this._draw();
  }

  _build() {
    const { led_count: count, effects } = this._state;
    this._stopPreview(false); // its frames have another LED count
    this._built = count;
    this._pages = Math.ceil(count / PAGE);
    this._page = 0;
    this._sel = new Set([...this._sel].filter((i) => i < count));
    this._runsPicked = false;
    this._stop = undefined; // the dots' tab stop: the dot focused last, else a page's first
    const old = this._$; // a rebuild keeps the Moving settings as the user left them
    this._body.innerHTML = BODY;
    const q = (selector) => this._body.querySelector(selector);
    const $ = (this._$ = {});
    for (const name of [
      "name", "now", "leds", "total", "pagelabel", "prev", "pos", "next", "from", "to", "step",
      "minus", "plus", "group", "save", "colourlabel", "swatches", "live", "showpainting", "apply",
      "find", "kinds", "slots", "slothint", "mswatches", "runs", "keep", "speed", "preview", "play",
      "builtins", "movies", "memtext", "meterbar", "looks", "lookhint", "savelook", "groups",
      "grouphint",
    ]) {
      $[name] = q(`.${name}`);
    }
    $.total.textContent = `All ${count} LEDs`;
    $.from.max = $.to.max = count;
    $.from.placeholder = "1";
    $.to.placeholder = String(count);
    $.live.checked = this._live; // a rebuild keeps the switch as the user left it
    if (old) {
      $.speed.value = old.speed.value;
      $.keep.checked = old.keep.checked;
    }

    // the overview: one strip per page, a bar per LED
    this._minis = [];
    this._orows = [];
    for (let p = 0; p < this._pages; p++) {
      const last = Math.min(count, (p + 1) * PAGE);
      const row = q(".overview").appendChild(el("button", "orow"));
      row.setAttribute("aria-label", `Show LEDs ${p * PAGE + 1}-${last}`);
      row.addEventListener("click", () => this._show(p));
      // one tab stop: arrow keys move between the rows, Enter jumps to that page
      row.addEventListener("focus", () => rove(this._orows, row));
      row.addEventListener("keydown", (e) => {
        const to = keyTarget(e, {
          ArrowUp: p - 1, ArrowLeft: p - 1, ArrowDown: p + 1, ArrowRight: p + 1,
          Home: 0, End: this._pages - 1,
        });
        this._orows[to]?.focus();
      });
      for (let i = p * PAGE; i < last; i++) this._minis.push(row.appendChild(el("i")));
      this._orows.push(row);
    }
    rove(this._orows, this._orows[0]);

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
        // one tab stop: arrow keys move by one, up and down by a row, Home and End to its ends
        dot.addEventListener("focus", () => {
          this._stop = led();
          rove(this._dots, dot);
        });
        dot.addEventListener("keydown", (e) => {
          const k = r * ROW + c;
          const used = this._state.led_count - this._page * PAGE; // dots in use on this page
          const to = this._dots[keyTarget(e, {
            ArrowLeft: k - 1, ArrowRight: k + 1, ArrowUp: k - ROW, ArrowDown: k + ROW,
            Home: r * ROW, End: Math.min(r * ROW + ROW, used) - 1,
          })];
          if (to && !to.hidden) to.focus();
        });
        this._dots.push(dot);
      }
    }

    this._tabs = [...this._body.querySelectorAll('[role="tab"]')];
    this._panels = [...this._body.querySelectorAll('[role="tabpanel"]')];
    this._tabs.forEach((tab, k) => {
      tab.addEventListener("click", () => this._showTab(tab.dataset.tab));
      // the tabs pattern: one tab stop, arrow keys select
      tab.addEventListener("keydown", (e) => {
        const n = this._tabs.length;
        const to = this._tabs[keyTarget(e, {
          ArrowLeft: (k + n - 1) % n, ArrowRight: (k + 1) % n, Home: 0, End: n - 1,
        })];
        if (!to) return;
        this._showTab(to.dataset.tab);
        to.focus();
      });
    });
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
    $.showpainting.addEventListener("click", () => this._commit());
    $.apply.addEventListener("click", () => this._commit());
    $.find.addEventListener("click", () => this._find());
    this._paintControls = this._swatches($.swatches, (color) => this._paint(color));

    // the Moving tab: every change redraws, which also ends a preview of other settings
    if (!effects.includes(this._kind)) this._kind = effects[0];
    this._kinds = effects.map((kind) => {
      const chip = $.kinds.appendChild(textEl("button", "chip", EFFECTS[kind] ?? kind));
      chip.dataset.kind = kind;
      chip.addEventListener("click", () => {
        this._kind = kind;
        this._draw();
      });
      return chip;
    });
    this._slotButtons = Array.from({ length: SLOTS }, (_, k) => {
      const slot = $.slots.appendChild(el("button", "sw"));
      slot.addEventListener("click", () => {
        this._slots.splice(k, 1);
        this._draw();
      });
      return slot;
    });
    this._slotControls = this._swatches($.mswatches, (color, name) => {
      if (this._slots.length < SLOTS) this._slots.push({ color, name });
      this._draw();
    });
    $.runs.addEventListener("change", () => {
      this._runsPicked = true;
      this._draw();
    });
    $.keep.addEventListener("change", () => this._draw());
    $.speed.addEventListener("input", () => this._draw());
    $.preview.addEventListener("click", () => this._preview());
    $.play.addEventListener("click", () => this._play());
    $.savelook.addEventListener("click", () => this._saveLook());
    this._showTab(this._tab);
  }

  /** The named swatches and a colour picker, in row; pick gets [r, g, b, w] and a name. */
  _swatches(row, pick) {
    const { colors } = this._state;
    const controls = SWATCHES.filter((name) => name in colors).map((name) => {
      const swatch = row.appendChild(el("button", "sw"));
      swatch.title = name[0].toUpperCase() + name.slice(1);
      swatch.setAttribute("aria-label", swatch.title);
      swatch.style.background = shown(colors[name]);
      swatch.addEventListener("click", () => pick(colors[name], swatch.title));
      return swatch;
    });
    const picker = row.appendChild(el("input", "sw"));
    picker.type = "color";
    picker.title = "Custom colour";
    picker.setAttribute("aria-label", picker.title);
    picker.addEventListener("change", () => {
      const hex = picker.value;
      picker.value = "#000000"; // so picking the same colour again still counts as a change
      pick([1, 3, 5].map((k) => parseInt(hex.slice(k, k + 2), 16)).concat(0), hex);
    });
    return [...controls, picker];
  }

  _showTab(tab) {
    this._tab = tab;
    this._stopPreview();
    for (const button of this._tabs) {
      const selected = button.dataset.tab === tab;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1; // the tab list's one tab stop
    }
    for (const panel of this._panels) panel.hidden = panel.dataset.tab !== tab;
    this._$.leds.hidden = tab === "looks"; // the dots serve painting and the preview
  }

  /** Bring every dot and control in line with the canvas, the selection and the page. */
  _draw() {
    const count = this._state.led_count;
    const page = this._page;
    const $ = this._$;
    this._drawLeds();
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
    const stop = Math.floor(this._stop / PAGE) === page ? this._stop % PAGE : 0;
    this._dots.forEach((dot, k) => {
      const i = page * PAGE + k;
      dot.hidden = i >= count;
      dot.tabIndex = k === stop ? 0 : -1;
      if (i >= count) return;
      const name = colourName(this._canvas.subarray(i * 4, i * 4 + 4), this._state.colors);
      dot.setAttribute("aria-label", `LED ${i + 1}, ${name}`);
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
    this._drawMoving();
  }

  /** The overview and the page's dots: the preview's frame while it plays, else the canvas. */
  _drawLeds() {
    const leds = this._frame ?? this._canvas;
    const color = (i) => shown(leds.subarray(i * 4, i * 4 + 4));
    this._minis.forEach((mini, i) => (mini.style.background = color(i)));
    const first = this._page * PAGE;
    this._dots.forEach((dot, k) => {
      if (first + k < this._state.led_count) dot.style.background = color(first + k);
    });
  }

  _drawMoving() {
    const $ = this._$;
    const n = this._sel.size;
    for (const chip of this._kinds) {
      chip.setAttribute("aria-pressed", String(chip.dataset.kind === this._kind));
    }
    this._slotButtons.forEach((slot, k) => {
      const pick = this._slots[k];
      slot.disabled = !pick;
      slot.classList.toggle("empty", !pick);
      slot.style.background = pick ? shown(pick.color) : "";
      slot.setAttribute("aria-label", pick ? `Remove colour ${k + 1}, ${pick.name}` : `Colour ${k + 1}, empty`);
    });
    for (const control of this._slotControls) control.disabled = this._slots.length === SLOTS;
    $.play.textContent = this._playing ? "Playing..." : "Play on tree";
    $.play.setAttribute("aria-disabled", String(Boolean(this._playing)));
    $.slothint.textContent = this._slots.length
      ? "Tap a chosen colour to remove it"
      : "None chosen: the effect's own colours";
    const selection = $.runs.options[0];
    selection.textContent = `Selected LEDs (${n})`;
    selection.disabled = !n;
    // follows the selection until the user picks; an emptied selection falls back to all
    if (!this._runsPicked || (!n && selection.selected)) $.runs.value = n ? "" : "all";
    if (this._previewed !== undefined && JSON.stringify(this._spec()) !== this._previewed) {
      this._stopPreview();
    }
  }

  /** Runs on: the selection (""), each group (its name), all ("all", never a group's name). */
  _fillRuns(groups) {
    const runs = this._$.runs;
    const chosen = runs.value;
    runs.replaceChildren(
      new Option("", ""),
      ...groups.map((name) => new Option(name, name)),
      new Option("All LEDs", "all")
    );
    runs.value = chosen;
    if (runs.value !== chosen) this._runsPicked = false; // the chosen group is gone
  }

  /** The moving effect the Moving tab describes, as effect/render and effect/play take it. */
  _spec() {
    const runs = this._$.runs.value;
    return {
      kind: this._kind,
      leds: runs === "" ? [...this._sel].sort((a, b) => a - b) : runs,
      colors: this._slots.map((slot) => slot.color),
      speed: this._$.speed.valueAsNumber,
      keep_others: this._$.keep.checked,
    };
  }

  /** Play the effect's frames on the dots until another action or other settings. */
  async _preview() {
    this._stopPreview();
    this._fail(null);
    const ticket = this._ticket;
    const spec = this._spec();
    this._previewed = JSON.stringify(spec);
    let reply;
    try {
      reply = await this._ws("effect/render", { spec });
    } catch (err) {
      this._fail(err);
      return;
    }
    if (ticket !== this._ticket || !reply.frames.length) return; // something else came first
    const frames = reply.frames.map(bytes);
    let k = 0;
    const next = () => {
      this._frame = frames[k++ % frames.length];
      this._drawLeds();
    };
    next();
    this._timer = setInterval(next, 1000 / reply.fps);
  }

  /** End the preview, or drop the one on its way; draw = false while the dots are replaced. */
  _stopPreview(draw = true) {
    this._ticket++;
    this._previewed = undefined;
    if (this._timer === undefined) return;
    clearInterval(this._timer);
    this._timer = this._frame = undefined;
    if (draw) this._drawLeds();
  }

  /** The lists from state: what is on the lights, the looks and the groups; focus stays put. */
  _renderLists(focus = this.shadowRoot.activeElement?.dataset.key) {
    this._renderOnLights();
    this._renderLooks();
    this._renderGroups();
    if (focus) {
      const again = [...this._body.querySelectorAll("[data-key]")].find((e) => e.dataset.key === focus);
      again?.focus({ preventScroll: true });
    }
  }

  _renderOnLights() {
    const { builtins, app_movies: movies, active, mode, memory } = this._state;
    const on = mode !== "off"; // off: nothing plays, active names what comes back
    const item = (text, key, current, command, data) => {
      const chip = textEl("button", "chip", text);
      chip.dataset.key = key;
      if (current) {
        chip.setAttribute("aria-current", "true");
        chip.append(badge("playing"));
      }
      chip.addEventListener("click", () => this._run(command, data));
      return chip;
    };
    const effects = [];
    for (let n = 1; n <= builtins; n++) {
      const current = on && active.type === "builtin" && Number(active.ref) === n - 1;
      effects.push(item(`Built-in ${n}`, `builtin ${n}`, current, "builtin", { effect_id: n - 1 }));
    }
    const apps = movies.map((movie) => {
      const current = on && active.type === "app_movie" && movie.unique_id === active.ref;
      const name = movie.name || "Unnamed movie";
      return item(name, `movie ${movie.id}`, current, "app_movie", { movie_id: movie.id });
    });
    for (const [group, chips] of [[this._$.builtins, effects], [this._$.movies, apps]]) {
      group.hidden = !chips.length;
      group.lastElementChild.replaceChildren(...chips);
    }
    const stranded = memory.stranded > 0 ? `, ${memory.stranded} stranded` : "";
    this._$.memtext.textContent = `Memory on the lights: ${Math.round(memory.used_pct)}% used${stranded}`;
    this._$.meterbar.style.width = `${Math.min(100, memory.used_pct)}%`;
  }

  _renderLooks() {
    const { looks, active, mode, led_count: count } = this._state;
    const on = mode !== "off" && active.type === "look" ? nameKey(active.ref ?? "") : undefined;
    const rows = looks.map((look) => {
      const key = nameKey(look.name);
      const row = el("div", "look");
      const apply = row.appendChild(el("button", "apply-look"));
      apply.dataset.key = `look ${key}`;
      apply.setAttribute("aria-label", look.name);
      apply.setAttribute("aria-disabled", String(this._applying !== undefined));
      const title = apply.appendChild(el("span", "title"));
      title.append(textEl("span", "lname", look.name));
      if (key === on) {
        apply.setAttribute("aria-current", "true");
        title.append(badge("on now"));
      }
      title.append(textEl("span", "hint kind", key === this._applying ? "Applying..." : look.kind));
      const strip = apply.appendChild(el("span", "strip"));
      const canvas = bytes(look.canvas);
      for (let k = 0; k < SAMPLES; k++) {
        const i = Math.floor((k * count) / SAMPLES);
        strip.appendChild(el("i")).style.background = shown(canvas.subarray(i * 4, i * 4 + 4));
      }
      apply.addEventListener("click", () => this._applyLook(look.name));

      const more = row.appendChild(textEl("button", "icon", "\u22ee"));
      more.dataset.key = `menu ${key}`;
      more.setAttribute("aria-label", `Options for ${look.name}`);
      more.setAttribute("aria-expanded", String(key === this._menu));
      more.addEventListener("click", () => {
        this._menu = key === this._menu ? undefined : key;
        this._renderLists();
      });
      const menu = row.appendChild(el("div", "menu"));
      menu.hidden = key !== this._menu;
      for (const [text, action] of [
        ["Rename", () => this._renameLook(look.name)],
        ["Update from what's on now", () => this._updateLook(look.name)],
        ["Delete", () => this._deleteLook(look.name)],
      ]) {
        menu.appendChild(textEl("button", "btn", text)).addEventListener("click", () => {
          this._menu = undefined;
          this._renderLists(more.dataset.key); // the menu closes, focus goes back to its button
          action();
        });
      }
      return row;
    });
    this._$.looks.replaceChildren(...rows);
    this._$.lookhint.textContent = looks.length
      ? "\u22ee to rename, update or delete"
      : "No looks yet: paint or play something, then save it.";
  }

  _renderGroups() {
    const names = Object.keys(this._state.groups);
    const chips = names.map((name) => {
      const chip = el("span", "chip gchip");
      chip.append(textEl("span", "", name));
      const remove = chip.appendChild(textEl("button", "", "\u00d7"));
      remove.setAttribute("aria-label", `Delete group ${name}`);
      remove.addEventListener("click", () => this._deleteGroup(name));
      return chip;
    });
    this._$.groups.replaceChildren(...chips);
    this._$.grouphint.textContent = names.length
      ? "Usable in Colours, Moving and automations."
      : "No groups yet: select LEDs in Colours and save them as a group.";
  }

  async _saveLook() {
    const name = window.prompt("Name of the new look")?.trim();
    if (!name) return;
    const taken = this._state.looks.find((look) => nameKey(look.name) === nameKey(name));
    if (taken && !window.confirm(`Replace the look "${taken.name}"?`)) return;
    await this._run("look/save", { name });
  }

  /** Apply a look; clicks while one applies (a moving look uploads for seconds) do nothing. */
  async _applyLook(name) {
    if (this._applying !== undefined) return;
    this._applying = nameKey(name);
    this._renderLists();
    await this._run("look/apply", { name });
    this._applying = undefined;
    this._renderLists();
  }

  async _updateLook(name) {
    if (window.confirm(`Replace the look "${name}" with what's on now?`)) {
      await this._run("look/update", { name });
    }
  }

  async _renameLook(name) {
    const newName = window.prompt(`New name for the look "${name}"`, name)?.trim();
    if (newName && newName !== name) await this._run("look/rename", { name, new_name: newName });
  }

  async _deleteLook(name) {
    if (window.confirm(`Delete the look "${name}"?`)) await this._run("look/delete", { name });
  }

  async _deleteGroup(name) {
    if (window.confirm(`Delete the group "${name}"?`)) await this._run("group/delete", { name });
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
    // the integration matches group names ignoring case and runs of spaces
    const key = nameKey(name.replace(/\s+/g, " "));
    const taken = Object.keys(this._state.groups).find((group) => nameKey(group) === key);
    if (taken && !window.confirm(`Replace the group "${taken}"?`)) return;
    await this._run("group/save", { name, leds: formatSelection(this._sel) });
  }

  /** Paint the selection: the dots change at once, the lights when live is on. */
  async _paint(color) {
    const leds = [...this._sel].sort((a, b) => a - b);
    for (const i of leds) this._canvas.set(color, i * 4);
    this._paints++;
    this._unapplied = !this._live;
    this._draw();
    this._fail(null);
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

  /** Show the painting on the tree (Apply, Show painting), then the state it left. */
  async _commit() {
    const unapplied = this._unapplied;
    this._unapplied = false; // a paint while the commit runs sets it again
    if (await this._act("commit")) await this._load();
    else this._unapplied ||= unapplied;
  }

  /** Play on tree; clicks while the effect uploads do nothing. */
  async _play() {
    if (this._playing) return;
    this._playing = true;
    this._draw();
    await this._run("effect/play", { spec: this._spec() });
    this._playing = false;
    this._draw();
  }

  async _find() {
    this._finding = true;
    this._draw();
    await this._act("find", { leds: [...this._sel].sort((a, b) => a - b) });
    this._finding = false;
    this._draw();
  }
}

/** The visual editor: an entity picker, through Home Assistant's own ha-form. */
class TwinklyCanvasCardEditor extends HTMLElement {
  constructor() {
    super();
    // HA defines ha-form lazily; a built-in card's editor loads it (the usual custom-card idiom)
    // a failed chunk load leaves the form undefined, without an unhandled rejection
    if (!customElements.get("ha-form")) {
      customElements.get("hui-button-card")?.getConfigElement?.()?.catch?.(() => {});
    }
    this._form = document.createElement("ha-form");
    this._form.schema = EDITOR_SCHEMA;
    this._form.computeLabel = () => "Entity"; // the schema's one field
    this._form.addEventListener("value-changed", (e) => {
      this._config = { ...this._config, ...e.detail.value };
      const detail = { config: this._config };
      this.dispatchEvent(new CustomEvent("config-changed", { detail, bubbles: true, composed: true }));
    });
    this.attachShadow({ mode: "open" }).append(this._form);
  }

  setConfig(config) {
    this._config = config;
    this._form.data = config;
  }

  set hass(hass) {
    this._form.hass = hass;
  }
}

if (!customElements.get("twinkly-canvas-card")) {
  customElements.define("twinkly-canvas-card", TwinklyCanvasCard);
  customElements.define("twinkly-canvas-card-editor", TwinklyCanvasCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "twinkly-canvas-card",
    name: "Twinkly Canvas",
    description: "Paint single LEDs of a Twinkly light, play effects and saved looks.",
  });
}
