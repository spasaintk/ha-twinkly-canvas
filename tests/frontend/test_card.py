"""Browser tests of the canvas card against a fake hass (fake_hass.js).

Headless Chromium loads harness.html from disk: no web server, no network. The tests run
with the rest of the suite (.venv/bin/pytest, or .venv/bin/pytest tests/frontend) and skip
where Playwright is not installed. test_screenshots saves a PNG of each tab under
test-results/.

They use Playwright's async API: the sync API leaves its event loop marked as running,
which breaks the async fixtures of the pytest_homeassistant plugin.
"""

import re
from pathlib import Path

import pytest

from custom_components.twinkly_canvas.selection import SelectionError, parse_selection

async_api = pytest.importorskip("playwright.async_api")
expect = async_api.expect

HARNESS = (Path(__file__).parent / "harness.html").as_uri()
RESULTS = Path(__file__).parents[2] / "test-results"
ODD_38_80 = list(range(38, 79, 2))  # 0-based indices of the odd LED numbers 39-79
ENTITY = {"entity_id": "light.test_tree"}
RED = [255, 0, 0, 0]
GREEN = [0, 200, 40, 0]
# unique_id of an app movie in the fixture
SNOWFALL = "0a1b2c3d-0000-4000-8000-000000000004"
BUILTIN_1 = {"active": {"type": "builtin", "ref": "0"}, "mode": "effect"}
# Selection texts by kind: the card's parser must agree with the integration's.
SELECTIONS = {
    "ranges": ["1-20", "20-1", "1 - 20", "400", "1-400"],
    "lists": ["1, 5, 9", "1-3, 7", "  1-3 ,  7  ", "1, 1", "1-3,"],
    "filters": [
        "38-80 odd",
        "38-80, even",
        "38-80,odd",
        "1-20 ODD",
        "1-10 every 3",
        "1-10 every 3 from 2",
    ],
    "bare filters": ["odd", "even", "every 3", "every 3 from 2", "Every 3 From 0"],
    # the fixture's group texts; a group's name is refused (stored texts never name one)
    "groups": ["1, 100, 200, 300", "301-400 odd", "Top"],
    "all": ["all", "ALL", "all, 5", "all every 3 from 2"],
    "out of range": ["0", "401", "1-401", "every 3 from 500", "1 even"],
    "malformed": [
        "",
        "1,,2",
        "1-",
        "-5",
        "1-2-3",
        "abc",
        "1 2",
        "odd,",
        ",odd",
        "every 0",
        "1-10 every 0",
        "1234567",
    ],
}


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
    """A dot by its LED number; its name also tells the colour: "LED 5, red"."""
    return page.get_by_role("button", name=re.compile(rf"^LED {number},"))


def _selected(page):
    return page.locator('.dot[aria-pressed="true"]')


async def _centre(page, number: int) -> tuple[float, float]:
    box = await _dot(page, number).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def _select_odd_38_80(page) -> None:
    await page.get_by_label("From", exact=True).fill("38")
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


async def _reconfigure(page) -> None:
    """setConfig again, as HA's editor preview does on every edit; waits for the new markup."""
    await page.locator(".head").evaluate("e => e.dataset.old = 'yes'")
    await page.evaluate(
        "document.querySelector('twinkly-canvas-card').setConfig({entity: 'light.test_tree'})"
    )
    await page.locator(".head:not([data-old])").wait_for(state="attached")


async def _hold(page, command: str) -> None:
    """Hold the fake's next reply to command until _release."""
    await page.evaluate("command => (window.__hold = command)", command)


async def _release(page) -> None:
    """Send the held reply; returns once the card has handled it."""
    await page.evaluate("window.__release()")


def _colour_of(locator):
    return locator.evaluate("e => getComputedStyle(e).backgroundColor")


async def test_renders_dots(page) -> None:
    await expect(page.locator(".orow i")).to_have_count(400)
    await expect(page.locator(".dot")).to_have_count(100)
    await expect(page.locator(".rl")).to_have_text(["1", "26", "51", "76"])
    await expect(_dot(page, 1)).to_have_css("background-color", "rgb(255, 0, 0)")
    await expect(_dot(page, 1)).to_have_accessible_name("LED 1, red")
    await expect(_dot(page, 39)).to_have_accessible_name("LED 39, silver")
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
    await expect(_dot(page, 381)).to_have_accessible_name("LED 381, off")


async def test_range_odd_selection(page) -> None:
    await _select_odd_38_80(page)
    await expect(_selected(page)).to_have_count(21)
    await expect(_dot(page, 39)).to_have_attribute("aria-pressed", "true")
    await expect(_dot(page, 40)).to_have_attribute("aria-pressed", "false")
    await expect(page.get_by_text("Colour for 21 LEDs")).to_be_visible()


async def test_every_n_chip_and_stepper(page) -> None:
    await page.get_by_label("From", exact=True).fill("10")
    await page.get_by_label("to", exact=True).fill("30")
    every = page.locator('[data-filter="every"]')
    await every.click()
    await expect(every).to_have_attribute("aria-pressed", "true")
    # from the range's first LED: 10, 13, ..., 28
    await expect(_selected(page)).to_have_count(7)
    await expect(_dot(page, 13)).to_have_attribute("aria-pressed", "true")
    await expect(_dot(page, 11)).to_have_attribute("aria-pressed", "false")
    await page.get_by_role("button", name="Increase step").click()
    await expect(page.get_by_role("button", name="Every 4", exact=True)).to_be_visible()
    await expect(_selected(page)).to_have_count(6)  # 10, 14, ..., 30
    for _ in range(3):
        await page.get_by_role("button", name="Decrease step").click()
    # the step stops at 2: 10, 12, ..., 30
    await expect(page.get_by_role("button", name="Every 2", exact=True)).to_be_visible()
    await expect(_selected(page)).to_have_count(11)
    await page.get_by_role("button", name="Odd", exact=True).click()
    await page.get_by_role("button", name="Increase step").click()
    # with another filter the stepper leaves the selection alone
    await expect(page.get_by_role("button", name="Every 3", exact=True)).to_be_visible()
    await expect(_selected(page)).to_have_count(10)


async def test_drag_and_shift_click(page) -> None:
    async def drag(start: int, end: int) -> None:
        await page.mouse.move(*await _centre(page, start))
        await page.mouse.down()
        await page.mouse.move(*await _centre(page, end), steps=10)
        await page.mouse.up()

    await drag(1, 5)
    await expect(_selected(page)).to_have_count(5)
    # from the dot the drag started on
    await _dot(page, 30).click(modifiers=["Shift"])
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


async def test_keyboard_tab_stops(page) -> None:
    """The overview rows and the dots are one tab stop each: From is a few presses away."""
    await expect(page.locator('.orow[tabindex="0"]')).to_have_count(1)
    await expect(page.locator('.dot[tabindex="0"]')).to_have_count(1)
    await page.get_by_role("tab", name="Colours").focus()
    start = page.get_by_label("From", exact=True)
    for _ in range(8):
        await page.keyboard.press("Tab")
        if await start.evaluate("e => e.getRootNode().activeElement === e"):
            break
    await expect(start).to_be_focused()

    # the overview rows: arrow keys move, Enter jumps to that page
    await page.get_by_role("button", name="Show LEDs 1-100").focus()
    await page.keyboard.press("ArrowDown")
    await page.keyboard.press("ArrowDown")
    third = page.get_by_role("button", name="Show LEDs 201-300")
    await expect(third).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(page.locator(".rl").first).to_have_text("201")
    await expect(page.locator('.orow[tabindex="0"]')).to_have_count(1)
    await expect(third).to_have_attribute("tabindex", "0")
    await page.keyboard.press("End")
    await expect(page.get_by_role("button", name="Show LEDs 301-400")).to_be_focused()


async def test_keyboard_dots(page) -> None:
    """Arrow keys move between the dots, Enter and Space toggle one as a click does."""
    await _dot(page, 1).focus()
    for key, number in [
        ("ArrowRight", 2),
        ("ArrowDown", 27),
        ("End", 50),
        ("Home", 26),
        ("ArrowLeft", 25),  # by one: the end of the row above
        ("ArrowUp", 25),  # no row above
    ]:
        await page.keyboard.press(key)
        await expect(_dot(page, number)).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(_dot(page, 25)).to_have_attribute("aria-pressed", "true")
    await page.keyboard.press("Space")
    await expect(_dot(page, 25)).to_have_attribute("aria-pressed", "false")
    # one tab stop: the dot focused last, or the first dot of another page
    await expect(page.locator('.dot[tabindex="0"]')).to_have_count(1)
    await expect(_dot(page, 25)).to_have_attribute("tabindex", "0")
    await page.get_by_role("button", name="Next page").click()
    await expect(_dot(page, 101)).to_have_attribute("tabindex", "0")
    await page.get_by_role("button", name="Previous page").click()
    await expect(_dot(page, 25)).to_have_attribute("tabindex", "0")


async def test_group_select(page) -> None:
    # "301-400 odd"
    await page.get_by_label("Select a saved group").select_option("Top")
    await expect(page.get_by_text("Colour for 50 LEDs")).to_be_visible()
    # the page of its first LED
    await expect(page.locator(".rl").first).to_have_text("301")
    await expect(_dot(page, 301)).to_have_attribute("aria-pressed", "true")
    await expect(_dot(page, 302)).to_have_attribute("aria-pressed", "false")
    await page.get_by_role("button", name="Even", exact=True).click()  # every even LED
    await expect(page.get_by_text("Colour for 200 LEDs")).to_be_visible()
    await expect(page.locator(".rl").first).to_have_text("301")  # this page shows some

    # a filter alone means all LEDs with that filter: 1, 5, ..., 397
    await _set_state(page, {"groups": {"Fourths": "every 4"}})
    await page.get_by_label("Select a saved group").select_option("Fourths")
    await expect(page.get_by_text("Colour for 100 LEDs")).to_be_visible()


@pytest.mark.parametrize("kind", SELECTIONS)
async def test_parser_matches_python(page, kind: str) -> None:
    """The card's parseSelection and the integration's parse_selection agree."""
    texts = SELECTIONS[kind]
    card = await page.evaluate(
        """texts => texts.map((text) => {
            try { return parseSelection(text, 400); } catch { return null; }
        })""",
        texts,
    )
    for text, leds in zip(texts, card, strict=True):
        try:
            expected = parse_selection(text, 400)
        except SelectionError:
            expected = None  # refused
        assert leds == expected, text


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
    # at once
    await expect(_dot(page, 39)).to_have_css("background-color", "rgb(18, 52, 86)")
    await expect(_dot(page, 39)).to_have_accessible_name("LED 39, rgb(18, 52, 86)")


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


async def test_paint_during_apply_stays_unapplied(page) -> None:
    """With Live off, a paint that lands while Apply runs still goes to the tree later."""
    live = page.get_by_role("switch", name="Live on tree")
    await live.uncheck()
    await _dot(page, 5).click()
    await page.get_by_role("button", name="Gold", exact=True).click()
    await _hold(page, "commit")
    await page.get_by_role("button", name="Apply").click()
    await _last(page, "commit")
    await page.get_by_role("button", name="Red", exact=True).click()  # meanwhile
    await _release(page)
    await expect(page.get_by_role("button", name="Apply")).to_be_visible()
    await expect(_dot(page, 5)).to_have_css("background-color", "rgb(255, 0, 0)")
    # the card still knows the red paint is not on the tree: Live on catches up
    await live.check()
    assert len(await _calls(page, "commit")) == 2


async def test_show_painting(page) -> None:
    """While something else plays, Show painting plays the painting again (commit)."""
    show = page.get_by_role("button", name="Show painting")
    await expect(show).to_be_hidden()  # the painting plays
    await _set_state(page, BUILTIN_1)
    await expect(show).to_be_visible()
    await show.click()
    assert await _last(page, "commit") == {"type": "twinkly_canvas/commit", **ENTITY}
    await expect(page.locator(".now")).to_have_text("Showing your painting")
    await expect(show).to_be_hidden()
    look = {"type": "look", "ref": "Red and silver"}
    await _set_state(page, {"active": look, "mode": "off"})
    await expect(page.locator(".now")).to_have_text("Off")
    await expect(show).to_be_hidden()  # off: nothing plays
    await _set_state(page, {"active": look, "mode": "movie"})
    await expect(show).to_be_visible()


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


async def test_save_group_asks_before_replacing(page) -> None:
    """A name taken ignoring case and spaces asks before replacing that group."""
    await _select_odd_38_80(page)
    await page.evaluate(
        "window.prompt = () => ' top ';"
        "window.confirm = (question) => { window.__asked = question; return false; }"
    )
    await page.get_by_role("button", name="Save as group").click()
    assert await page.evaluate("window.__asked") == 'Replace the group "Top"?'
    assert not await _calls(page, "group/save")
    await page.evaluate("window.confirm = () => true")
    await page.get_by_role("button", name="Save as group").click()
    assert (await _last(page, "group/save"))["name"] == "top"


async def test_error_shown(page) -> None:
    await page.evaluate("window.__fail = 'Twinkly did not answer'")
    await _dot(page, 1).click()
    await page.get_by_role("button", name="Off", exact=True).click()
    await expect(page.get_by_role("alert")).to_have_text("Twinkly did not answer")
    # reloaded
    await expect(_dot(page, 1)).to_have_css("background-color", "rgb(255, 0, 0)")


async def test_error_stays_after_background_reload(page) -> None:
    """Only the user's next action clears an error, not a reload of the state."""
    alert = page.get_by_role("alert")
    await page.evaluate("window.__fail = 'Twinkly did not answer'")
    await _dot(page, 5).click()
    await page.get_by_role("button", name="Find on tree").click()
    await expect(alert).to_have_text("Twinkly did not answer")
    await _set_state(page, BUILTIN_1)
    await expect(page.locator(".now")).to_have_text("Playing built-in effect 1")
    await expect(alert).to_have_text("Twinkly did not answer")
    await page.get_by_role("button", name="Find on tree").click()
    await expect(alert).to_be_hidden()


async def test_stale_render_keeps_error(page) -> None:
    """Preview in flight, Play fails, then the old render reply arrives: the error stays."""
    await _tab(page, "Moving")
    alert = page.get_by_role("alert")
    await _hold(page, "effect/render")
    await page.get_by_role("button", name="Preview").click()
    await _last(page, "effect/render")
    await page.evaluate("window.__fail = 'Upload failed'")
    await page.get_by_role("button", name="Play on tree").click()
    await expect(alert).to_have_text("Upload failed")
    await _release(page)
    await expect(alert).to_have_text("Upload failed")
    # its frames were dropped too
    assert await _colour_of(_dot(page, 1)) == "rgb(255, 0, 0)"


async def test_reattach_reloads(page) -> None:
    """HA re-attaches cached cards when the user returns to the view: the card reloads."""
    await page.evaluate(
        """async () => {
            // another client paints LED 1; the light's state object stays the same
            await window.hass.callWS({
                type: "twinkly_canvas/paint", entity_id: "light.test_tree",
                leds: [0], color: [0, 200, 40, 0],
            });
            const card = document.querySelector("twinkly-canvas-card");
            card.remove();
            document.body.append(card);
        }"""
    )
    await expect(_dot(page, 1)).to_have_css("background-color", "rgb(0, 200, 40)")


async def test_card_api(page) -> None:
    stub = "customElements.get('twinkly-canvas-card').getStubConfig(window.hass)"
    assert await page.evaluate(stub) == {"entity": "light.test_tree"}
    size = "document.querySelector('twinkly-canvas-card').getCardSize()"
    assert await page.evaluate(size) == 8
    assert await page.evaluate(
        "window.customCards.some(c => c.type === 'twinkly-canvas-card')"
    )


async def test_tabs(page) -> None:
    colours = page.get_by_role("tab", name="Colours")
    moving = page.get_by_role("tab", name="Moving")
    await expect(colours).to_have_attribute("aria-selected", "true")
    await _tab(page, "Moving")
    await expect(moving).to_have_attribute("aria-selected", "true")
    await expect(colours).to_have_attribute("aria-selected", "false")
    await expect(_dot(page, 1)).to_be_visible()  # the preview plays on the dots
    await expect(page.get_by_label("From", exact=True)).to_be_hidden()
    await _tab(page, "Looks")
    await expect(_dot(page, 1)).to_be_hidden()
    await expect(page.get_by_role("button", name="Save what's on now")).to_be_visible()


async def test_keyboard_tabs(page) -> None:
    """The tabs pattern: one tab stop, arrow keys select, each tab controls its panel."""
    names = ("Colours", "Moving", "Looks")
    tabs = {name: page.get_by_role("tab", name=name) for name in names}
    await tabs["Colours"].focus()
    for key, name in [
        ("ArrowRight", "Moving"),
        ("End", "Looks"),
        ("ArrowRight", "Colours"),  # round from the last
        ("ArrowLeft", "Looks"),
        ("Home", "Colours"),
        ("ArrowRight", "Moving"),
    ]:
        await page.keyboard.press(key)
        await expect(tabs[name]).to_be_focused()
        await expect(tabs[name]).to_have_attribute("aria-selected", "true")
        await expect(tabs[name]).to_have_attribute("tabindex", "0")
        await expect(page.get_by_role("tabpanel", name=name)).to_be_visible()
    await expect(page.locator('[role="tab"][tabindex="0"]')).to_have_count(1)
    await expect(page.get_by_role("button", name="Play on tree")).to_be_visible()
    for tab in tabs.values():
        panel = page.locator(f"#{await tab.get_attribute('aria-controls')}")
        await expect(panel).to_have_attribute("role", "tabpanel")
        await expect(panel).to_have_attribute(
            "aria-labelledby", await tab.get_attribute("id")
        )


async def test_rebuild_keeps_switch_and_tab(page) -> None:
    """HA's editor preview calls setConfig again: the card rebuilds as the user left it."""
    await page.get_by_role("switch", name="Live on tree").uncheck()
    await _reconfigure(page)
    await expect(page.get_by_role("switch", name="Live on tree")).not_to_be_checked()
    await expect(page.get_by_role("button", name="Apply")).to_be_visible()
    await _tab(page, "Looks")
    await _reconfigure(page)
    await expect(page.get_by_role("tab", name="Looks")).to_have_attribute(
        "aria-selected", "true"
    )
    await expect(page.get_by_role("button", name="Save what's on now")).to_be_visible()


async def test_rebuild_keeps_moving_settings(page) -> None:
    await _tab(page, "Moving")
    await page.get_by_label("Speed").fill("70")
    keep = page.get_by_role("switch", name="Other LEDs keep their colours")
    await keep.uncheck()
    await _reconfigure(page)
    await expect(page.get_by_label("Speed")).to_have_value("70")
    await expect(keep).not_to_be_checked()
    await page.get_by_role("button", name="Play on tree").click()
    spec = (await _last(page, "effect/play"))["spec"]
    assert (spec["speed"], spec["keep_others"]) == (70, False)


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


async def test_play_busy(page) -> None:
    """Play on tree shows Playing... while the upload runs; a second click sends nothing."""
    await _tab(page, "Moving")
    play = page.locator(".play")
    await _hold(page, "effect/play")
    await play.evaluate("b => { b.click(); b.click(); }")
    await expect(play).to_have_text("Playing...")
    await expect(play).to_have_attribute("aria-disabled", "true")
    assert len(await _calls(page, "effect/play")) == 1
    await _release(page)
    await expect(play).to_have_text("Play on tree")
    await expect(play).to_have_attribute("aria-disabled", "false")
    assert len(await _calls(page, "effect/play")) == 1

    # it comes back when the call fails, too
    await page.evaluate("window.__fail = 'Upload failed'")
    await _hold(page, "effect/play")
    await play.click()
    await expect(play).to_have_text("Playing...")
    await _release(page)
    await expect(page.get_by_role("alert")).to_have_text("Upload failed")
    await expect(play).to_have_text("Play on tree")


async def test_moving_defaults_slots_and_runs_on(page) -> None:
    await _select_odd_38_80(page)
    await _tab(page, "Moving")
    runs = page.get_by_label("Runs on")
    await expect(runs.locator("option:checked")).to_have_text("Selected LEDs (21)")
    for name in ("Gold", "Green", "Red"):
        await page.get_by_role("button", name=name, exact=True).click()
    # 3 at most
    await expect(page.get_by_role("button", name="Silver", exact=True)).to_be_disabled()
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
    # the next frame
    await expect(dot).to_have_css("background-color", "rgb(0, 60, 255)", timeout=1000)
    await expect(page.locator(".orow i").first).to_have_css(
        "background-color", "rgb(0, 200, 40)"
    )


async def test_preview_fractional_fps(page) -> None:
    """effect/render may answer a fractional fps: the frame interval is not rounded."""
    await page.evaluate(
        """() => {
            window.__render.fps = 5.5;
            const set = window.setInterval;
            window.setInterval = (run, ms) => { window.__interval = ms; return set(run, ms); };
        }"""
    )
    await _tab(page, "Moving")
    await page.get_by_role("button", name="Preview").click()
    await page.wait_for_function("window.__interval !== undefined")
    assert await page.evaluate("window.__interval") == pytest.approx(1000 / 5.5)


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
    builtin_5 = page.get_by_role("button", name="Built-in 5", exact=True)
    await expect(builtin_5).to_be_visible()
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


async def test_on_lights_groups(page) -> None:
    """Built-in effects and the app's movies under their own labels, as in the mockup."""
    await _tab(page, "Moving")
    builtins = page.get_by_role("group", name="Built-in effects")
    movies = page.get_by_role("group", name="From the Twinkly app")
    await expect(builtins.get_by_role("button")).to_have_text(
        [f"Built-in {n}" for n in range(1, 6)]
    )
    await expect(movies.get_by_role("button")).to_have_text(["App movie 1", "Snowfall"])
    # no app movies: no app group
    await _set_state(page, {"app_movies": []})
    await expect(movies).to_have_count(0)
    await expect(page.get_by_text("From the Twinkly app")).to_be_hidden()
    await expect(builtins.get_by_role("button")).to_have_count(5)


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
            {"type": "app_movie", "ref": "0a1b2c3d-0000-4000-8000-00000000000f"},
            "movie",
            "Playing a movie from the Twinkly app",
            None,
        ),
        (
            {"type": "device_mode", "ref": "playlist"},
            "playlist",
            "Playing content from the Twinkly app",
            None,
        ),
        # what comes back when on
        ({"type": "builtin", "ref": "2"}, "off", "Off", None),
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
    five = page.get_by_text("Memory on the lights: 5% used", exact=True)
    await expect(five).to_be_visible()
    await _set_state(page, {"memory": {"used_pct": 18.2, "stranded": 2}})
    text = page.get_by_text("Memory on the lights: 18% used, 2 stranded", exact=True)
    await expect(text).to_be_visible()
    assert await page.locator(".meter div").evaluate("e => e.style.width") == "18.2%"


async def test_focus_restore_does_not_scroll(page) -> None:
    """A background reload re-renders the lists: the focused chip keeps focus, no scroll."""
    await _tab(page, "Moving")
    chip = page.get_by_role("button", name="Snowfall", exact=True)
    await chip.focus()
    # the user scrolled on to other cards below this one
    await page.evaluate(
        """() => {
            document.body.append(Object.assign(document.createElement("div"),
                { style: "height: 3000px" }));
            window.scrollTo(0, document.body.scrollHeight);
        }"""
    )
    before = await page.evaluate("window.scrollY")
    await _set_state(page, {"memory": {"used_pct": 18.2, "stranded": 0}})
    await expect(page.get_by_text("Memory on the lights: 18% used")).to_be_attached()
    await expect(chip).to_be_focused()
    assert await page.evaluate("window.scrollY") == before


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

    # Ruling 19: names match ignoring case
    await _set_state(page, {"active": {"type": "look", "ref": "red AND silver"}})
    applied = page.get_by_role("button", name="Red and silver", exact=True)
    await expect(applied).to_have_attribute("aria-current", "true")
    await expect(looks.nth(1)).to_contain_text("on now")
    candy = page.get_by_role("button", name="Candy stripes", exact=True)
    await expect(candy).not_to_have_attribute("aria-current", "true")


async def test_look_apply_busy(page) -> None:
    """A look shows Applying... while it uploads; another click sends nothing."""
    await _tab(page, "Looks")
    look = page.get_by_role("button", name="Candy stripes", exact=True)
    await _hold(page, "look/apply")
    await look.evaluate("b => { b.click(); b.click(); }")
    await expect(look).to_contain_text("Applying...")
    await expect(look).to_have_attribute("aria-disabled", "true")
    assert len(await _calls(page, "look/apply")) == 1
    await _release(page)
    await expect(look).to_contain_text("moving")
    await expect(look).to_have_attribute("aria-disabled", "false")
    assert len(await _calls(page, "look/apply")) == 1


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

    # updating overwrites the saved look: it asks first
    await page.evaluate(
        "window.confirm = (question) => { window.__asked = question; return false; }"
    )
    await page.get_by_role("button", name="Options for Warm glow").click()
    await page.get_by_role("button", name="Update from what's on now").click()
    assert await page.evaluate("window.__asked") == (
        'Replace the look "Warm glow" with what\'s on now?'
    )
    assert not await _calls(page, "look/update")  # not confirmed
    await page.evaluate("window.confirm = () => true")
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
    corners = page.get_by_role("button", name="Delete group Corners")
    await expect(corners).to_have_count(0)
    await expect(page.get_by_role("button", name="Delete group Top")).to_be_visible()
    groups = page.get_by_label("Select a saved group")
    await expect(groups).not_to_contain_text("Corners")
    # its group is gone
    await expect(runs.locator("option:checked")).to_have_text("All LEDs")


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


async def test_editor_loader_failure_is_caught(page) -> None:
    """A failed load of HA's form leaves no unhandled rejection (the fixture fails on one)."""
    await page.evaluate(
        """async () => {
            customElements.define("hui-button-card", class extends HTMLElement {
                static getConfigElement() { return Promise.reject(new Error("no chunk")); }
            });
            customElements.get("twinkly-canvas-card").getConfigElement();
            await new Promise((resolve) => setTimeout(resolve, 100));
        }"""
    )


@pytest.mark.parametrize(
    ("width", "query"), [(1280, ""), (390, "?dark"), (360, ""), (360, "?dark")]
)
async def test_screenshots(page, width: int, query: str) -> None:
    await page.set_viewport_size({"width": width, "height": 900})
    await page.goto(HARNESS + query)
    # a built-in plays: its mark and Show painting show too
    await _set_state(page, BUILTIN_1)
    await _select_odd_38_80(page)
    RESULTS.mkdir(exist_ok=True)
    theme = "-dark" if query else ""
    for tab in ("Colours", "Moving", "Looks"):
        await _tab(page, tab)
        if tab == "Moving":
            await page.get_by_role("button", name="Chase", exact=True).click()
            await page.get_by_role("button", name="Red", exact=True).click()
            await page.get_by_role("button", name="Silver", exact=True).click()
        if tab == "Looks":
            await page.get_by_role("button", name="Options for Red and silver").click()
        # no sideways scroll
        assert await page.evaluate("document.documentElement.scrollWidth") <= width
        path = RESULTS / f"{tab.lower()}-{width}{theme}.png"
        await page.screenshot(path=path, full_page=True)
