// A stand-in for Home Assistant's hass object with one Twinkly Canvas light of 400 LEDs.
// callWS answers twinkly_canvas/state from the fixture below (window.__state) and records
// every call in window.__calls; group and look commands change the fixture like the
// integration would. Set window.__fail to a message to make the next command fail like HA.
(() => {
  const RED = [255, 0, 0, 0];
  const SILVER = [192, 192, 192, 0];
  const GOLD = [255, 215, 0, 0];
  const WHITE = [255, 255, 255, 0];
  const WARM = [0, 0, 0, 255];
  const OFF = [0, 0, 0, 0];
  const color = (n) => {  // n counts from 1
    if (n <= 37) return RED;
    if (n <= 80) return n % 2 ? SILVER : RED;
    if (n <= 200) return n % 5 === 0 ? GOLD : n % 2 ? RED : SILVER;
    if (n <= 380) return n % 5 === 0 ? GOLD : WARM;
    return OFF;
  };
  const canvas = (pick) => {  // R,G,B,W per LED, one character per byte (btoa encodes it)
    let bytes = "";
    for (let n = 1; n <= 400; n++) bytes += String.fromCharCode(...pick(n));
    return bytes;
  };
  const bytes = canvas(color);
  const stripes = canvas((n) => (Math.floor((n - 1) / 16) % 2 ? WHITE : RED));

  const state = {
    led_count: 400,
    strings: [[0, 200], [200, 200]],
    profile: "RGBW",
    canvas: btoa(bytes),
    groups: { Corners: "1, 100, 200, 300", Top: "301-400 odd" },
    looks: [  // sorted by name ignoring case, like the integration
      {
        name: "Candy stripes", kind: "moving", canvas: btoa(stripes),
        effect: { kind: "candy_cane", leds: [0, 1, 2], colors: [], speed: 0.5, keep_others: true, seed: 1 },
      },
      { name: "Red and silver", kind: "static", canvas: btoa(bytes), effect: null },
    ],
    custom_effect: null,
    builtins: 5,
    app_movies: [
      { id: 0, name: "App movie 1", frames: 120, unique_id: "0a1b2c3d-0000-4000-8000-000000000001" },
      { id: 3, name: "Snowfall", frames: 60, unique_id: "0a1b2c3d-0000-4000-8000-000000000004" },
    ],
    memory: {
      used_pct: 4.9, available_frames: 2412, max_capacity: 2536,
      movies: 4, max_movies: 55, stranded: 0,
    },
    active: { type: "painted", ref: null },
    mode: "movie",
    colors: {
      off: [0, 0, 0, 0], red: [255, 0, 0, 0], green: [0, 200, 40, 0], blue: [0, 60, 255, 0],
      gold: [255, 215, 0, 0], silver: [192, 192, 192, 0], white: [255, 255, 255, 0],
      "warm white": [0, 0, 0, 255], orange: [255, 80, 0, 0], yellow: [255, 200, 0, 0],
      purple: [150, 0, 255, 0], pink: [255, 40, 120, 0], cyan: [0, 200, 255, 0],
    },
    effects: [
      "twinkle", "chase", "odd_even_swap", "wave", "comet", "candy_cane", "breathe", "rainbow",
    ],
  };
  // a preview loop: LED 1 green, then blue; the other LEDs as painted
  const frame = (rgbw) => btoa(String.fromCharCode(...rgbw) + bytes.slice(4));
  const render = { fps: 10, frames: [frame([0, 200, 40, 0]), frame([0, 60, 255, 0])] };
  const named = (name) => (look) => look.name.toLowerCase() === name.trim().toLowerCase();

  window.__state = state;
  window.__calls = [];
  window.__fail = null;
  window.hass = {
    states: {
      "light.test_tree": {
        entity_id: "light.test_tree",
        state: "on",
        attributes: { friendly_name: "Test Tree", effect: "Painted" },
      },
    },
    entities: { "light.test_tree": { entity_id: "light.test_tree", platform: "twinkly_canvas" } },
    themes: { darkMode: false, theme: "default", themes: {} },
    async callWS(msg) {
      window.__calls.push(msg);
      if (window.__fail) {
        const message = window.__fail;
        window.__fail = null;
        throw { code: "device_error", message };  // what hass.callWS rejects with
      }
      const command = msg.type.replace("twinkly_canvas/", "");
      if (command === "state") return structuredClone(state);
      if (command === "effect/render") return structuredClone(render);
      if (command === "group/save") state.groups[msg.name] = msg.leds;
      if (command === "group/delete") delete state.groups[msg.name];
      if (command === "look/delete") state.looks = state.looks.filter((l) => !named(msg.name)(l));
      if (command === "look/rename") state.looks.find(named(msg.name)).name = msg.new_name;
      if (command === "look/save") {
        state.looks = state.looks.filter((l) => !named(msg.name)(l));
        state.looks.push({ name: msg.name, kind: "static", canvas: state.canvas, effect: null });
      }
      return null;
    },
  };
})();
