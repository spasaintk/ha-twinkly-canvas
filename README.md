# Twinkly Canvas

Paint individual LEDs of a Twinkly Gen II light from Home Assistant. Pick LEDs on a
picture of your tree or by number, give them a colour, and the result plays on the
lights as a stored movie. Everything runs locally; there is no cloud and no extra
Python package.

## What it does

- A light entity per Twinkly device, with the usual on/off and brightness.
- A dashboard card (`twinkly-canvas-card`) to paint LEDs, with live painting on the tree.
- Actions to set LEDs, play moving effects, save and apply looks, and save LED groups.
- Turning the light off and on brings back what was playing, including the Twinkly
  app's effects, movies and playlists.

## Requirements

- A Twinkly Gen II light that supports the movie list API, firmware 2.5.6 or newer.
- Compatibility Mode switched on in the Twinkly app.
- Home Assistant 2026.11 or newer.

## Installation

1. In Home Assistant open Settings > Marketplace, choose Add from link and enter
   `https://github.com/spasaintk/ha-twinkly-canvas` with category Integration.
2. Install Twinkly Canvas and restart Home Assistant.

## Setup

Settings > Devices & services > Add integration > Twinkly Canvas, then enter the host
address of the light (for example `192.0.2.10`). Reserve that address for the light in
your router so it does not change.

## The card

```yaml
type: custom:twinkly-canvas-card
entity: light.test_tree
```

Tap LEDs to select them, choose a colour and paint. With "Live on tree" on, the tree
follows while you paint; with it off, press Apply to send the result. Find on tree
flashes the selected LEDs for 6 seconds so you can see where they are.

## Actions

All actions target a Twinkly Canvas light.

Set LEDs 38 to 80, every other one, to silver:

```yaml
action: twinkly_canvas.set_leds
target:
  entity_id: light.test_tree
data:
  leds: "38-80 odd"
  color_name: silver
```

Play a moving effect:

```yaml
action: twinkly_canvas.play_effect
target:
  entity_id: light.test_tree
data:
  kind: chase
  colors: ["red", [192, 192, 192]]
  leds: "1-100"
  speed: 50
```

Save what is painted as a look, and apply it later:

```yaml
action: twinkly_canvas.save_look
target:
  entity_id: light.test_tree
data:
  name: Red and gold
```

```yaml
action: twinkly_canvas.apply_look
target:
  entity_id: light.test_tree
data:
  look: Red and gold
```

Flash LEDs to find them:

```yaml
action: twinkly_canvas.find_leds
target:
  entity_id: light.test_tree
data:
  leds: "1-10"
```

Save and delete a named group of LEDs:

```yaml
action: twinkly_canvas.save_group
target:
  entity_id: light.test_tree
data:
  name: Top
  leds: "350-400"
```

```yaml
action: twinkly_canvas.delete_group
target:
  entity_id: light.test_tree
data:
  name: Top
```

## Selecting LEDs

LED numbers start at 1. A selection is a comma separated list of:

- a number (`7`) or a range (`1-20`);
- `all`;
- the name of a saved group.

A filter at the end applies to the whole selection: `odd`, `even`, `every 3` or
`every 3 from 2`. Write it after a space or a comma: `1-20 odd` and `1-20,odd` mean the
same. Example: `1-20, 50, Top odd`.

## Notes

- One login at a time: the Twinkly app and this integration log each other out, so
  after using the app the integration logs in again on its next poll.
- Movies made in the Twinkly app are never deleted. The integration only removes movies
  it created itself.
- The light has limited movie memory. The card's Moving tab shows a memory meter. A
  movie of this integration that sits before a movie made in the Twinkly app is shown
  as "N stranded": it is not removed automatically, because the integration only
  removes its own movies after the last app movie. To free that memory, delete the app
  movies after it in the Twinkly app; the next upload then removes the stranded ones.
- Spatial effects are not supported yet.

## Development

```
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The card is also tested in headless Chromium with Playwright (skipped when it is not
installed):

```
.venv/bin/pip install playwright && .venv/bin/playwright install chromium
.venv/bin/pytest tests/frontend
```

## Licence

MIT
