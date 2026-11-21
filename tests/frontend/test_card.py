"""Browser tests of the canvas card against a fake hass (fake_hass.js).

Headless Chromium loads harness.html from disk: no web server, no network. The tests run
with the rest of the suite (.venv/bin/pytest, or .venv/bin/pytest tests/frontend) and skip
where Playwright is not installed. test_screenshots saves a PNG of each tab under
test-results/.

They use Playwright's async API: the sync API leaves its event loop marked as running,
which breaks the async fixtures of the pytest_homeassistant plugin.
"""

from pathlib import Path

import pytest

from custom_components.twinkly_canvas.selection import parse_selection

async_api = pytest.importorskip("playwright.async_api")
expect = async_api.expect

HARNESS = (Path(__file__).parent / "harness.html").as_uri()
RESULTS = Path(__file__).parents[2] / "test-results"
ODD_38_80 = list(range(38, 79, 2))  # 0-based indices of the odd LED numbers 39-79
ENTITY = {"entity_id": "light.test_tree"}
RED = [255, 0, 0, 0]
GREEN = [0, 200, 40, 0]
SNOWFALL = (
    "0a1b2c3d-0000-4000-8000-000000000004"  # unique_id of an app movie in the fixture
)


@pytest.fixture
async def page():
    """The harness with the card loaded; the test fails on a script error or a web request."""
    errors = []

    async def refuse(route) -> None:  # the card loads nothing from outside its file
        errors.append(route.request.url)
        await route.abort()

    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(5000)
        page.on("pageerror", lambda err: errors.append(err))
        page.on("console", lambda msg: msg.type == "error" and errors.append(msg.text))
        await page.route(lambda url: not url.startswith("file:"), refuse)
        await page.goto(HARNESS)
        await page.locator(".dot").first.wait_for()
        yield page
        await browser.close()
    assert not errors


async def _calls(page, command: str) -> list[dict]:
    """The messages of one websocket command the card sent, oldest first."""
    return await page.evaluate(
        "type => window.__calls.filter(c => c.type === type)",
        f"twinkly_canvas/{command}",
    )


async def _last(page, command: str) -> dict:
    await page.wait_for_function(
        "type => window.__calls.some(c => c.type === type)",
        arg=f"twinkly_canvas/{command}",
    )
    return (await _calls(page, command))[-1]


def _dot(page, number: int):
    return page.get_by_role("button", name=f"LED {number}", exact=True)


def _selected(page):
    return page.locator('.dot[aria-pressed="true"]')


async def _centre(page, number: int) -> tuple[float, float]:
    box = await _dot(page, number).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def _select_odd_38_80(page) -> None:
    await page.get_by_label("From").fill("38")
    await page.get_by_label("to", exact=True).fill("80")
    await page.get_by_role("button", name="Odd", exact=True).click()


async def _tab(page, name: str) -> None:
    await page.get_by_role("tab", name=name).click()


async def _set_state(page, fields: dict) -> None:
    """Change fields of the state fixture; the light's new state object makes the card reload."""
    await page.evaluate(
        """fields => {
            Object.assign(window.__state, fields);
            const id = "light.test_tree";
            const states = { ...window.hass.states, [id]: { ...window.hass.states[id] } };
            window.hass = { ...window.hass, states };
            document.querySelector("twinkly-canvas-card").hass = window.hass;
        }""",
        fields,
    )


def _colour_of(locator):
    return locator.evaluate("e => getComputedStyle(e).backgroundColor")


async def test_renders_dots(page) -> None:
    await expect(page.locator(".orow i")).to_have_count(400)
    await expect(page.locator(".dot")).to_have_count(100)
    await expect(page.locator(".rl")).to_have_text(["1", "26", "51", "76"])
    await expect(_dot(page, 1)).to_have_css("background-color", "rgb(255, 0, 0)")
    # LED 201 is warm white (W only): #FFE3B0 at full W
    await expect(page.locator(".orow i").nth(200)).to_have_css(
        "background-color", "rgb(255, 227, 176)"
    )
    await expect(page.get_by_text("Showing your painting")).to_be_visible()


async def test_page_switch(page) -> None:
    await page.get_by_role("button", name="Next page").click()
    await expect(page.locator(".rl")).to_have_text(["101", "126", "151", "176"])
    await expect(page.get_by_text("LEDs 101-200")).to_be_visible()
    await page.get_by_role("button", name="Show LEDs 301-400").click()
    await expect(page.locator(".rl").first).to_have_text("301")


async def test_range_odd_selection(page) -> None:
    await _select_odd_38_80(page)
    await expect(_selected(page)).to_have_count(21)
    await expect(_dot(page, 39)).to_have_attribute("aria-pressed", "true")
    await expect(_dot(page, 40)).to_have_attribute("aria-pressed", "false")
    await expect(page.get_by_text("Colour for 21 LEDs")).to_be_visible()


async def test_drag_and_shift_click(page) -> None:
    async def drag(start: int, end: int) -> None:
        await page.mouse.move(*await _centre(page, start))
        await page.mouse.down()
        await page.mouse.move(*await _centre(page, end), steps=10)
        await page.mouse.up()

    await drag(1, 5)
    await expect(_selected(page)).to_have_count(5)
    await _dot(page, 30).click(modifiers=["Shift"])  # from the dot the drag started on
    await expect(_selected(page)).to_have_count(30)
    await drag(2, 3)  # starting on a selected dot deselects
    await expect(_selected(page)).to_have_count(28)
    await expect(_dot(page, 2)).to_have_attribute("aria-pressed", "false")


async def test_touch_drag(page) -> None:
    """A touch captures the pointer on the first dot; the drag still reaches the others."""
    cdp = await page.context.new_cdp_session(page)
    (x1, y), (x2, _) = await _centre(page, 3), await _centre(page, 9)

    async def touch(kind: str, x: float | None = None) -> None:
        points = [] if x is None else [{"x": x, "y": y}]
        await cdp.send(
            "Input.dispatchTouchEvent", {"type": kind, "touchPoints": points}
        )

    await touch("touchStart", x1)
    for step in range(1, 13):
        await touch("touchMove", x1 + (x2 - x1) * step / 12)
    await touch("touchEnd")
    await expect(_selected(page)).to_have_count(7)  # LEDs 3-9


async def test_group_select(page) -> None:
    await page.get_by_label("Select a saved group").select_option(
        "Top"
    )  # "301-400 odd"
    await expect(page.get_by_text("Colour for 50 LEDs")).to_be_visible()
    await expect(page.locator(".rl").first).to_have_text(
        "301"
    )  # the page of its first LED
    await expect(_dot(page, 301)).to_have_attribute("aria-pressed", "true")
    await expect(_dot(page, 302)).to_have_attribute("aria-pressed", "false")
    await page.get_by_role("button", name="Even", exact=True).click()  # every even LED
    await expect(page.get_by_text("Colour for 200 LEDs")).to_be_visible()
    await expect(page.locator(".rl").first).to_have_text("301")  # this page shows some


async def test_paint_call(page) -> None:
    await _select_odd_38_80(page)
    await page.get_by_role("button", name="Silver", exact=True).click()
    assert await _last(page, "paint") == {
        "type": "twinkly_canvas/paint",
        "entity_id": "light.test_tree",
        "leds": ODD_38_80,
        "color": [192, 192, 192, 0],
        "live": True,
    }

    await page.get_by_label("Custom colour").filter(visible=True).fill("#123456")
    assert (await _last(page, "paint"))["color"] == [0x12, 0x34, 0x56, 0]
    await expect(_dot(page, 39)).to_have_css(
        "background-color", "rgb(18, 52, 86)"
    )  # at once


async def test_live_off_shows_apply(page) -> None:
    apply = page.get_by_role("button", name="Apply")
    live = page.get_by_role("switch", name="Live on tree")
    await expect(apply).to_be_hidden()
    await live.uncheck()
    await expect(apply).to_be_visible()
    await _dot(page, 5).click()
    await page.get_by_role("button", name="Gold", exact=True).click()
    assert (await _last(page, "paint"))["live"] is False
    assert not await _calls(page, "commit")  # switching live off sends nothing
    await apply.click()
    await _last(page, "commit")

    await page.get_by_role("button", name="Red", exact=True).click()
    await live.check()  # what was painted meanwhile goes to the tree
    await expect(apply).to_be_hidden()
    assert len(await _calls(page, "commit")) == 2


async def test_find_call(page) -> None:
    find = page.get_by_role("button", name="Find on tree")
    await expect(find).to_be_disabled()  # nothing selected
    await _dot(page, 5).click()
    await find.click()
    assert await _last(page, "find") == {
        "type": "twinkly_canvas/find",
        "entity_id": "light.test_tree",
        "leds": [4],
    }


async def test_save_group_call(page) -> None:
    await _select_odd_38_80(page)
    await page.evaluate("window.prompt = () => 'Odd ones'")
    await page.get_by_role("button", name="Save as group").click()
    call = await _last(page, "group/save")
    assert call["name"] == "Odd ones"
    assert parse_selection(call["leds"], 400) == ODD_38_80
    await expect(page.get_by_label("Select a saved group")).to_contain_text("Odd ones")


async def test_error_shown(page) -> None:
    await page.evaluate("window.__fail = 'Twinkly did not answer'")
    await _dot(page, 1).click()
    await page.get_by_role("button", name="Off", exact=True).click()
    await expect(page.get_by_role("alert")).to_have_text("Twinkly did not answer")
    await expect(_dot(page, 1)).to_have_css(
        "background-color", "rgb(255, 0, 0)"
    )  # reloaded


async def test_card_api(page) -> None:
    assert await page.evaluate(
        "customElements.get('twinkly-canvas-card').getStubConfig(window.hass)"
    ) == {"entity": "light.test_tree"}
    assert (
        await page.evaluate(
            "document.querySelector('twinkly-canvas-card').getCardSize()"
        )
        == 8
    )
    assert await page.evaluate(
        "window.customCards.some(c => c.type === 'twinkly-canvas-card')"
    )


async def test_tabs(page) -> None:
    await expect(page.get_by_role("tab", name="Colours")).to_have_attribute(
        "aria-selected", "true"
    )
    await _tab(page, "Moving")
    await expect(page.get_by_role("tab", name="Moving")).to_have_attribute(
        "aria-selected", "true"
    )
    await expect(page.get_by_role("tab", name="Colours")).to_have_attribute(
        "aria-selected", "false"
    )
    await expect(_dot(page, 1)).to_be_visible()  # the preview plays on the dots
    await expect(page.get_by_label("From")).to_be_hidden()
    await _tab(page, "Looks")
    await expect(_dot(page, 1)).to_be_hidden()
    await expect(page.get_by_role("button", name="Save what's on now")).to_be_visible()


async def test_rebuild_keeps_switch_and_tab(page) -> None:
    """HA's editor preview calls setConfig again: the card rebuilds as the user left it."""

    async def reconfigure() -> None:  # and wait for the new markup
        await page.locator(".head").evaluate("e => e.dataset.old = 'yes'")
        await page.evaluate(
            "document.querySelector('twinkly-canvas-card').setConfig({entity: 'light.test_tree'})"
        )
        await page.locator(".head:not([data-old])").wait_for(state="attached")

    await page.get_by_role("switch", name="Live on tree").uncheck()
    await reconfigure()
    await expect(page.get_by_role("switch", name="Live on tree")).not_to_be_checked()
    await expect(page.get_by_role("button", name="Apply")).to_be_visible()
    await _tab(page, "Looks")
    await reconfigure()
    await expect(page.get_by_role("tab", name="Looks")).to_have_attribute(
        "aria-selected", "true"
    )
    await expect(page.get_by_role("button", name="Save what's on now")).to_be_visible()


async def test_moving_play_call(page) -> None:
    await _tab(page, "Moving")
    await page.get_by_role("button", name="Chase", exact=True).click()
    await page.get_by_role("button", name="Red", exact=True).click()
    await page.get_by_role("button", name="Silver", exact=True).click()
    await page.get_by_label("Runs on").select_option(label="All LEDs")
    await page.get_by_label("Speed").fill("70")
    await page.get_by_role("button", name="Play on tree").click()
    assert await _last(page, "effect/play") == {
        "type": "twinkly_canvas/effect/play",
        **ENTITY,
        "spec": {
            "kind": "chase",
            "colors": [[255, 0, 0, 0], [192, 192, 192, 0]],
            "leds": "all",
            "speed": 70,
            "keep_others": True,
        },
    }


async def test_moving_defaults_slots_and_runs_on(page) -> None:
    await _select_odd_38_80(page)
    await _tab(page, "Moving")
    runs = page.get_by_label("Runs on")
    await expect(runs.locator("option:checked")).to_have_text("Selected LEDs (21)")
    for name in ("Gold", "Green", "Red"):
        await page.get_by_role("button", name=name, exact=True).click()
    await expect(
        page.get_by_role("button", name="Silver", exact=True)
    ).to_be_disabled()  # 3 max
    await page.get_by_role("button", name="Remove colour 1, Gold").click()
    await page.get_by_role("switch", name="Other LEDs keep their colours").uncheck()
    await page.get_by_role("button", name="Play on tree").click()
    assert (await _last(page, "effect/play"))["spec"] == {
        "kind": "twinkle",
        "colors": [GREEN, RED],
        "leds": ODD_38_80,
        "speed": 50,
        "keep_others": False,
    }

    await runs.select_option(label="Top")  # a group goes by its name
    await page.get_by_role("button", name="Play on tree").click()
    assert (await _calls(page, "effect/play"))[-1]["spec"]["leds"] == "Top"


async def test_preview_animates(page) -> None:
    await _tab(page, "Moving")
    dot = _dot(page, 1)
    await expect(dot).to_have_css("background-color", "rgb(255, 0, 0)")
    await page.get_by_role("button", name="Preview").click()
    assert await _last(page, "effect/render") == {
        "type": "twinkly_canvas/effect/render",
        **ENTITY,
        "spec": {
            "kind": "twinkle",
            "colors": [],
            "leds": "all",
            "speed": 50,
            "keep_others": True,
        },
    }
    await expect(dot).to_have_css("background-color", "rgb(0, 200, 40)", timeout=1000)
    await expect(dot).to_have_css(
        "background-color", "rgb(0, 60, 255)", timeout=1000
    )  # next
    await expect(page.locator(".orow i").first).to_have_css(
        "background-color", "rgb(0, 200, 40)"
    )


@pytest.mark.parametrize(
    "action",
    [
        ("button", "Play on tree"),
        ("button", "Wave"),  # the settings changed
        ("button", "Built-in 2"),
        ("tab", "Colours"),
    ],
)
async def test_preview_stops_at_another_action(page, action: tuple[str, str]) -> None:
    await _tab(page, "Moving")
    dot = _dot(page, 1)
    await page.get_by_role("button", name="Preview").click()
    await expect(dot).not_to_have_css("background-color", "rgb(255, 0, 0)")
    await page.get_by_role(action[0], name=action[1], exact=True).click()
    await expect(dot).to_have_css("background-color", "rgb(255, 0, 0)")
    await page.wait_for_timeout(300)  # three frames at 10 fps
    assert await _colour_of(dot) == "rgb(255, 0, 0)"


async def test_preview_timer_stops_when_removed(page) -> None:
    await page.evaluate(
        """() => {
            const set = window.setInterval, clear = window.clearInterval;
            window.__timers = new Set();
            window.setInterval = (...args) => {
                const id = set(...args);
                window.__timers.add(id);
                return id;
            };
            window.clearInterval = (id) => { window.__timers.delete(id); clear(id); };
        }"""
    )
    await _tab(page, "Moving")
    await page.get_by_role("button", name="Preview").click()
    await expect(_dot(page, 1)).not_to_have_css("background-color", "rgb(255, 0, 0)")
    assert await page.evaluate("window.__timers.size") == 1
    await page.evaluate("document.querySelector('twinkly-canvas-card').remove()")
    assert await page.evaluate("window.__timers.size") == 0


async def test_builtin_call(page) -> None:
    await _tab(page, "Moving")
    await expect(
        page.get_by_role("button", name="Built-in 5", exact=True)
    ).to_be_visible()
    await page.get_by_role("button", name="Built-in 3", exact=True).click()
    assert await _last(page, "builtin") == {
        "type": "twinkly_canvas/builtin",
        **ENTITY,
        "effect_id": 2,  # the device's id counts from 0
    }


async def test_app_movie_call(page) -> None:
    await _tab(page, "Moving")
    await page.get_by_role("button", name="Snowfall", exact=True).click()
    assert await _last(page, "app_movie") == {
        "type": "twinkly_canvas/app_movie",
        **ENTITY,
        "movie_id": 3,
    }


@pytest.mark.parametrize(
    ("active", "mode", "now", "marked"),
    [
        (
            {"type": "builtin", "ref": "2"},
            "effect",
            "Playing built-in effect 3",
            "Built-in 3",
        ),
        (
            {"type": "app_movie", "ref": SNOWFALL},
            "movie",
            "Playing Snowfall from the Twinkly app",
            "Snowfall",
        ),
        (
            {"type": "device_mode", "ref": "playlist"},
            "playlist",
            "Playing content from the Twinkly app",
            None,
        ),
        (
            {"type": "builtin", "ref": "2"},
            "off",
            "Off",
            None,
        ),  # what comes back when on
        ({"type": "off", "ref": None}, "off", "Off", None),
        (
            {"type": "custom_effect", "ref": None},
            "movie",
            "Playing your moving effect",
            None,
        ),
        (
            {"type": "look", "ref": "Red and silver"},
            "movie",
            "Showing the look Red and silver",
            None,
        ),
        ({"type": "painted", "ref": None}, "movie", "Showing your painting", None),
        ({"type": "from_a_newer_version", "ref": "x"}, "movie", "", None),
    ],
)
async def test_active_shown(
    page, active: dict, mode: str, now: str, marked: str | None
) -> None:
    await _tab(page, "Moving")
    await _set_state(page, {"active": active, "mode": mode})
    await expect(page.locator(".now")).to_have_text(now)
    current = page.locator('[aria-current="true"]:visible')
    await expect(current).to_have_count(1 if marked else 0)
    if marked:
        await expect(current).to_have_accessible_name(marked)
        await expect(current).to_contain_text("playing")


async def test_memory_meter_text(page) -> None:
    await _tab(page, "Moving")
    await expect(
        page.get_by_text("Memory on the lights: 5% used", exact=True)
    ).to_be_visible()
    await _set_state(page, {"memory": {"used_pct": 18.2, "stranded": 2}})
    text = page.get_by_text("Memory on the lights: 18% used, 2 stranded", exact=True)
    await expect(text).to_be_visible()
    assert await page.locator(".meter div").evaluate("e => e.style.width") == "18.2%"


async def test_looks_list_and_apply(page) -> None:
    await _tab(page, "Looks")
    looks = page.locator(".look")
    await expect(looks).to_have_count(2)
    await expect(looks.nth(0)).to_contain_text("Candy stripes")
    await expect(looks.nth(0)).to_contain_text("moving")
    await expect(looks.nth(1)).to_contain_text("Red and silver")
    await expect(looks.nth(1)).to_contain_text("static")
    strip = looks.nth(0).locator(".strip i")
    await expect(strip).to_have_count(64)
    # sample k shows LED floor(k * 400 / 64): LED index 0 is in a red stripe, 18 in a white one
    await expect(strip.nth(0)).to_have_css("background-color", "rgb(255, 0, 0)")
    await expect(strip.nth(3)).to_have_css("background-color", "rgb(255, 255, 255)")

    await page.get_by_role("button", name="Red and silver", exact=True).click()
    assert await _last(page, "look/apply") == {
        "type": "twinkly_canvas/look/apply",
        **ENTITY,
        "name": "Red and silver",
    }

    await _set_state(
        page, {"active": {"type": "look", "ref": "red AND silver"}}
    )  # Ruling 19
    applied = page.get_by_role("button", name="Red and silver", exact=True)
    await expect(applied).to_have_attribute("aria-current", "true")
    await expect(looks.nth(1)).to_contain_text("on now")
    await expect(
        page.get_by_role("button", name="Candy stripes", exact=True)
    ).not_to_have_attribute("aria-current", "true")


async def test_look_rename_update_delete_calls(page) -> None:
    await _tab(page, "Looks")
    more = page.get_by_role("button", name="Options for Red and silver")
    await expect(more).to_have_attribute("aria-expanded", "false")
    await expect(page.get_by_role("button", name="Rename", exact=True)).to_have_count(0)
    await more.click()
    await expect(more).to_have_attribute("aria-expanded", "true")
    await page.evaluate("window.prompt = () => 'Warm glow'")
    await page.get_by_role("button", name="Rename", exact=True).click()
    assert await _last(page, "look/rename") == {
        "type": "twinkly_canvas/look/rename",
        **ENTITY,
        "name": "Red and silver",
        "new_name": "Warm glow",
    }
    await expect(
        page.get_by_role("button", name="Warm glow", exact=True)
    ).to_be_visible()

    await page.get_by_role("button", name="Options for Warm glow").click()
    await page.get_by_role("button", name="Update from what's on now").click()
    assert await _last(page, "look/update") == {
        "type": "twinkly_canvas/look/update",
        **ENTITY,
        "name": "Warm glow",
    }

    await page.evaluate("window.confirm = () => false")
    await page.get_by_role("button", name="Options for Warm glow").click()
    await page.get_by_role("button", name="Delete", exact=True).click()
    assert not await _calls(page, "look/delete")  # not confirmed
    await page.evaluate("window.confirm = () => true")
    await page.get_by_role("button", name="Options for Warm glow").click()
    await page.get_by_role("button", name="Delete", exact=True).click()
    assert await _last(page, "look/delete") == {
        "type": "twinkly_canvas/look/delete",
        **ENTITY,
        "name": "Warm glow",
    }
    await expect(page.locator(".look")).to_have_count(1)


async def test_save_look_call(page) -> None:
    await _tab(page, "Looks")
    await page.evaluate("window.prompt = () => 'Evening'")
    await page.get_by_role("button", name="Save what's on now").click()
    assert await _last(page, "look/save") == {
        "type": "twinkly_canvas/look/save",
        **ENTITY,
        "name": "Evening",
    }
    await expect(page.get_by_role("button", name="Evening", exact=True)).to_be_visible()

    # Ruling 19: a name taken ignoring case and spaces asks before replacing that look
    await page.evaluate(
        "window.prompt = () => ' red AND silver ';"
        "window.confirm = (question) => { window.__asked = question; return false; }"
    )
    await page.get_by_role("button", name="Save what's on now").click()
    assert await page.evaluate("window.__asked") == 'Replace the look "Red and silver"?'
    assert len(await _calls(page, "look/save")) == 1


async def test_group_delete_call(page) -> None:
    await _tab(page, "Moving")
    runs = page.get_by_label("Runs on")
    await expect(runs.locator("option")).to_have_text(
        ["Selected LEDs (0)", "Corners", "Top", "All LEDs"]
    )
    await runs.select_option(label="Corners")
    await _tab(page, "Looks")
    await page.evaluate("window.confirm = () => true")
    await page.get_by_role("button", name="Delete group Corners").click()
    assert await _last(page, "group/delete") == {
        "type": "twinkly_canvas/group/delete",
        **ENTITY,
        "name": "Corners",
    }
    await expect(page.get_by_role("button", name="Delete group Corners")).to_have_count(
        0
    )
    await expect(page.get_by_role("button", name="Delete group Top")).to_be_visible()
    await expect(page.get_by_label("Select a saved group")).not_to_contain_text(
        "Corners"
    )
    await expect(runs.locator("option:checked")).to_have_text(
        "All LEDs"
    )  # its group is gone


async def test_editor_renders(page) -> None:
    """The editor wraps HA's ha-form, which the harness lacks: the element stays undefined."""
    result = await page.evaluate(
        """async () => {
            const editor = customElements.get("twinkly-canvas-card").getConfigElement();
            document.body.append(editor);
            editor.setConfig({ type: "custom:twinkly-canvas-card", entity: "light.test_tree" });
            editor.hass = window.hass;
            const form = editor.shadowRoot.querySelector("ha-form");
            const changed = new Promise((resolve) =>
                editor.addEventListener("config-changed", (e) => resolve(e.detail.config)));
            form.dispatchEvent(new CustomEvent("value-changed",
                { detail: { value: { entity: "light.other_tree" } } }));
            return {
                tag: editor.localName,
                schema: form.schema,
                data: form.data,
                hass: form.hass === window.hass,
                label: form.computeLabel(form.schema[0]),
                config: await changed,
            };
        }"""
    )
    assert result == {
        "tag": "twinkly-canvas-card-editor",
        "schema": [
            {
                "name": "entity",
                "selector": {
                    "entity": {"integration": "twinkly_canvas", "domain": "light"}
                },
            }
        ],
        "data": {"type": "custom:twinkly-canvas-card", "entity": "light.test_tree"},
        "hass": True,
        "label": "Entity",
        "config": {"type": "custom:twinkly-canvas-card", "entity": "light.other_tree"},
    }


@pytest.mark.parametrize(("width", "query"), [(1280, ""), (390, "?dark")])
async def test_screenshots(page, width: int, query: str) -> None:
    await page.set_viewport_size({"width": width, "height": 900})
    await page.goto(HARNESS + query)
    await _select_odd_38_80(page)
    RESULTS.mkdir(exist_ok=True)
    for tab in ("Colours", "Moving", "Looks"):
        await _tab(page, tab)
        if tab == "Moving":
            await page.get_by_role("button", name="Chase", exact=True).click()
            await page.get_by_role("button", name="Red", exact=True).click()
            await page.get_by_role("button", name="Silver", exact=True).click()
        if tab == "Looks":
            await page.get_by_role("button", name="Options for Red and silver").click()
        assert (
            await page.evaluate("document.documentElement.scrollWidth") <= width
        )  # no sideways
        await page.screenshot(
            path=RESULTS / f"{tab.lower()}-{width}.png", full_page=True
        )
