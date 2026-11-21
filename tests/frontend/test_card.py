"""Browser tests of the canvas card against a fake hass (fake_hass.js).

Headless Chromium loads harness.html from disk: no web server, no network. The tests run
with the rest of the suite (.venv/bin/pytest, or .venv/bin/pytest tests/frontend) and skip
where Playwright is not installed. test_screenshots saves PNGs under test-results/.

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
        "type => window.__calls.filter(c => c.type === type)", f"twinkly_canvas/{command}"
    )


async def _last(page, command: str) -> dict:
    await page.wait_for_function(
        "type => window.__calls.some(c => c.type === type)", arg=f"twinkly_canvas/{command}"
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
        await cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

    await touch("touchStart", x1)
    for step in range(1, 13):
        await touch("touchMove", x1 + (x2 - x1) * step / 12)
    await touch("touchEnd")
    await expect(_selected(page)).to_have_count(7)  # LEDs 3-9


async def test_group_select(page) -> None:
    await page.get_by_label("Select a saved group").select_option("Top")  # "301-400 odd"
    await expect(page.get_by_text("Colour for 50 LEDs")).to_be_visible()
    await expect(page.locator(".rl").first).to_have_text("301")  # the page of its first LED
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

    await page.get_by_label("Custom colour").fill("#123456")
    assert (await _last(page, "paint"))["color"] == [0x12, 0x34, 0x56, 0]
    await expect(_dot(page, 39)).to_have_css("background-color", "rgb(18, 52, 86)")  # at once


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
    await expect(_dot(page, 1)).to_have_css("background-color", "rgb(255, 0, 0)")  # reloaded


async def test_card_api(page) -> None:
    assert await page.evaluate(
        "customElements.get('twinkly-canvas-card').getStubConfig(window.hass)"
    ) == {"entity": "light.test_tree"}
    assert await page.evaluate("document.querySelector('twinkly-canvas-card').getCardSize()") == 8
    assert await page.evaluate("window.customCards.some(c => c.type === 'twinkly-canvas-card')")


@pytest.mark.parametrize(("width", "query"), [(1280, ""), (390, "?dark")])
async def test_screenshots(page, width: int, query: str) -> None:
    await page.set_viewport_size({"width": width, "height": 900})
    await page.goto(HARNESS + query)
    await _select_odd_38_80(page)
    assert await page.evaluate("document.documentElement.scrollWidth") <= width  # no sideways
    RESULTS.mkdir(exist_ok=True)
    await page.screenshot(path=RESULTS / f"colours-{width}.png", full_page=True)
