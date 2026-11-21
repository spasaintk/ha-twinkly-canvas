// A stand-in for Home Assistant's hass object with one Twinkly Canvas light of 400 LEDs.
// callWS answers twinkly_canvas/state from the fixture below and records every call in
// window.__calls. Set window.__fail to a message to make the next command fail like HA does.
(() => {
  const RED = [255, 0, 0, 0];
  const SILVER = [192, 192, 192, 0];
  const GOLD = [255, 215, 0, 0];
  const WARM = [0, 0, 0, 255];
  const OFF = [0, 0, 0, 0];
  const color = (n) => {  // n counts from 1
    if (n <= 37) return RED;
    if (n <= 80) return n % 2 ? SILVER : RED;
    if (n <= 200) return n % 5 === 0 ? GOLD : n % 2 ? RED : SILVER;
    if (n <= 380) return n % 5 === 0 ? GOLD : WARM;
    return OFF;
  };
  let bytes = "";
  for (let n = 1; n <= 400; n++) bytes += String.fromCharCode(...color(n));

  const state = {
    led_count: 400,
    strings: [[0, 200], [200, 200]],
    profile: "RGBW",
    canvas: btoa(bytes),  // R,G,B,W per LED
    groups: { Corners: "1, 100, 200, 300", Top: "301-400 odd" },
    looks: [],
    custom_effect: null,
    builtins: 5,
    app_movies: [{ id: 0, name: "App movie 1", frames: 120 }],
    memory: {
      used_pct: 4.9, available_frames: 2412, max_capacity: 2536,
      movies: 2, max_movies: 55, stranded: 0,
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
      if (msg.type === "twinkly_canvas/state") return structuredClone(state);
      if (msg.type === "twinkly_canvas/group/save") state.groups[msg.name] = msg.leds;
      return null;
    },
  };
})();
