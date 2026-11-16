"""Focused browser cases against isolated Kestrel servers and disposable fixtures."""
from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlencode

from PIL import Image, ImageChops
from playwright.sync_api import expect, sync_playwright
import pytest

import http_test

PROJECT_PATH = "Documents/Project Stardust"
NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
BASELINES = Path(__file__).with_name("visual-baselines")
ENGINES = ["chromium", "firefox", "webkit"]
VIEWPORTS = [(1200, 630), (820, 700), (390, 780), (320, 640)]


@pytest.fixture(scope="session")
def runtime():
    http_test.FileBrowserTests.setUpClass()
    with sync_playwright() as playwright:
        yield playwright


@pytest.fixture
def server(runtime):
    fixture = http_test.FileBrowserTests()
    try:
        fixture.setUp(include_symlinks=False)
        shutil.copytree(http_test.PROJECT / "sample-data", fixture.root, dirs_exist_ok=True)
        for item in fixture.root.rglob("*"):
            if not item.is_symlink():
                os.utime(item, (NOW.timestamp(), NOW.timestamp()))
        yield fixture
    finally:
        fixture.doCleanups()


@pytest.fixture(scope="session")
def browsers(runtime):
    opened = {}
    yield opened
    for browser in opened.values():
        browser.close()


@pytest.fixture
def open_browser(runtime, browsers, server):
    contexts = []

    def open_page(engine="chromium", viewport=VIEWPORTS[0]):
        if engine not in browsers:
            browsers[engine] = getattr(runtime, engine).launch()
        width, height = viewport
        context = browsers[engine].new_context(
            viewport={"width": width, "height": height}, has_touch=width <= 820,
            timezone_id="UTC", locale="en-US", reduced_motion="reduce",
            accept_downloads=True,
        )
        contexts.append(context)
        page = context.new_page()
        page.set_default_timeout(6000)
        page.clock.set_fixed_time(NOW)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        return page, errors

    yield open_page
    for context in contexts:
        context.close()


def ready(page):
    expect(page.locator("#results")).to_have_attribute("aria-busy", "false")


def open_view(page, server, path=PROJECT_PATH, **params):
    page.goto(f"http://127.0.0.1:{server.port}/?" + urlencode({"path": path, **params}))
    ready(page)


def search(page, text):
    page.get_by_role("searchbox").fill(text)
    page.get_by_role("searchbox").press("Enter")
    ready(page)


def menu(page, filename):
    page.get_by_role("button", name="Actions for " + filename, exact=True).click()
    panel = page.locator("#item-menu")
    expect(panel).to_be_visible()
    return panel


def test_toolbar_caret_and_file_icon_dimensions(open_browser, server):
    page, errors = open_browser()
    open_view(page, server)
    for name in ("type", "date", "upload"):
        control = page.locator(f"#{name}-menu summary")
        caret = control.locator(".caret")
        expect(caret).to_have_css("width", "7.5px")
        expect(caret).to_have_css("height", "3.75px")
        expect(caret).to_have_css("background-color", "rgb(100, 113, 135)")
        for dimension in ("width", "height"):
            expect(control.locator(".caret-box")).to_have_css(dimension, "18px")
    icons = [
        ('#tree img[src$="/files.svg"]', "20px"),
        ('#tree img[src$="/folder.svg"]', "20px"),
        ('#entries img[src$="/folder.svg"]', "24px"),
        ('#entries img[src$="/document.svg"]', "20px"),
    ]
    for selector, size in icons:
        icon = page.locator(selector).first
        expect(icon).to_have_css("width", size)
        expect(icon).to_have_css("height", size)
        assert icon.evaluate("(img) => img.complete && img.naturalWidth > 0")
    assert not errors


def expect_popover_in_view(page, popover):
    page.wait_for_function("""popover => {
        const { left, right, width } = popover.getBoundingClientRect();
        return width > 0 && left >= -1 && right <= document.documentElement.clientWidth + 1;
    }""", arg=popover.element_handle())


def test_toolbar_popovers_fit_with_wider_text_and_after_resize(open_browser, server):
    page, errors = open_browser(viewport=(390, 640))
    open_view(page, server)
    page.add_style_tag(content=".toolbar summary { font-size: 18px; }")
    date = page.locator("#dates")
    page.locator("#date-menu summary").click()
    page.get_by_role("button", name="Custom date range").click()
    expect(page.locator("#from-date")).to_be_focused()
    page.set_viewport_size({"width": 320, "height": 640})
    expect_popover_in_view(page, date)
    page.keyboard.press("Escape")
    page.locator("#upload-menu summary").click()
    expect_popover_in_view(page, page.locator("#upload-menu .popover"))
    assert page.evaluate("document.documentElement.scrollWidth") == 320
    assert not errors


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["desktop", "tablet", "phone", "small-phone"])
def test_responsive_layout_navigation_and_menus(engine, viewport, open_browser, server):
    page, errors = open_browser(engine, viewport)
    open_view(page, server, "Documents")
    page.locator("#entries").get_by_role("link", name="Project Stardust").click()
    ready(page)
    expect(page.locator("#breadcrumbs [aria-current]")).to_have_text("Project Stardust")
    expect(page.locator("#tree [aria-current]")).to_have_text("Project Stardust")
    expect(page.locator("#query")).to_be_visible()
    for selector in ("#type-menu", "#date-menu", "#upload-menu"):
        page.locator(selector + " summary").click()
        popover = page.locator(selector + " .popover")
        expect(popover).to_be_visible()
        expect_popover_in_view(page, popover)
        page.keyboard.press("Escape")
        expect(popover).to_be_hidden()
    page.locator("#date-menu summary").click()
    page.get_by_role("button", name="Custom date range").click()
    expect_popover_in_view(page, page.locator("#dates"))
    page.keyboard.press("Escape")
    panel = menu(page, "Project Brief.pdf")
    box = panel.bounding_box()
    assert box["x"] >= 0 and box["y"] >= 0
    assert box["x"] + box["width"] <= viewport[0] + 1
    assert box["y"] + box["height"] <= viewport[1] + 1
    assert page.evaluate("document.documentElement.scrollWidth") == viewport[0]
    assert not errors


@pytest.mark.parametrize("engine", ENGINES)
def test_search_query_scope_highlight_and_history(engine, open_browser, server):
    page, errors = open_browser(engine)
    folder = server.root / PROJECT_PATH
    (folder / "Research/Brief [v2] brief.txt").write_text("nested")
    (server.root / "Documents/Brief outside.txt").write_text("outside")
    open_view(page, server)
    search(page, "  bRiEf  ")
    expect(page.locator("#results-note span").first).to_have_text('Results for "brief"')
    expect(page.locator("#results-note span").last).to_have_text("2 items found")
    expect(page.locator("#entries mark")).to_have_count(3)
    expect(page.locator(".name-label mark")).to_have_count(3)
    expect(page.locator(".name-label").filter(has_text="Project Brief.pdf")).to_have_text("Project Brief.pdf")
    expect(page.locator("#entries")).not_to_contain_text("Brief outside.txt")
    search(page, "[v2]")
    expect(page.locator("mark")).to_have_text("[v2]")
    page.reload()
    ready(page)
    expect(page.locator("mark")).to_have_text("[v2]")
    page.go_back()
    ready(page)
    expect(page.locator("#results-note span").last).to_have_text("2 items found")
    page.go_forward()
    ready(page)
    expect(page.locator("mark")).to_have_text("[v2]")
    assert not errors


@pytest.mark.parametrize(("label", "matching"), [
    ("Folders", "Category folder"), ("Documents", "Example.docx"),
    ("Spreadsheets", "Example.xlsx"), ("Presentations", "Example.pptx"),
    ("Images", "Example.PNG"), ("Videos", "Example.mp4"), ("Forms", "Example.xsn"),
    ("PDFs", "Example.pdf"), ("Archives", "Example.zip"),
])
def test_each_file_type_filters_the_expected_entries(label, matching, open_browser, server):
    folder = server.root / "Type fixtures"
    folder.mkdir()
    (folder / "Category folder").mkdir()
    for extension in ("docx", "xlsx", "pptx", "PNG", "mp4", "xsn", "pdf", "zip", "unknown"):
        (folder / f"Example.{extension}").touch()
    page, _ = open_browser()
    open_view(page, server, folder.name)
    page.locator("#type-menu summary").click()
    check = page.get_by_role("checkbox", name=label, exact=True)
    check.check()
    ready(page)
    expect(page.locator("#entries .name")).to_have_text([matching])
    page.reload()
    ready(page)
    expect(page.locator("#entries .name")).to_have_text([matching])
    page.locator("#type-menu summary").click()
    expect(check).to_be_checked()
    check.uncheck()
    ready(page)
    expect(page.locator("#entries .entry")).to_have_count(10)


@pytest.mark.parametrize(("label", "matching"), [
    ("Today", ["Today.txt"]),
    ("Last 7 days", ["Day 7.txt", "Today.txt"]),
    ("Last 30 days", ["Day 30.txt", "Day 7.txt", "Day 8.txt", "Today.txt"]),
    ("This year (2026)", ["Day 30.txt", "Day 31.txt", "Day 7.txt", "Day 8.txt", "Future.txt", "Today.txt"]),
    ("Last year (2025)", ["Last year.txt"]),
    ("Any time", ["Day 30.txt", "Day 31.txt", "Day 7.txt", "Day 8.txt", "Future.txt",
                  "Last year.txt", "Older.txt", "Today.txt"]),
])
def test_modified_presets_and_calendar_boundaries(label, matching, open_browser, server):
    folder = server.root / "Date fixtures"
    folder.mkdir()
    for name, stamp in {
        "Today": "2026-11-16T00:00:00", "Day 7": "2026-11-10T00:00:00",
        "Day 8": "2026-11-09T23:59:59", "Day 30": "2026-10-18T00:00:00",
        "Day 31": "2026-10-17T23:59:59", "Future": "2026-11-16T12:00:01",
        "Last year": "2025-12-31T23:59:59", "Older": "2024-12-31T23:59:59",
    }.items():
        path = folder / f"{name}.txt"
        path.touch()
        seconds = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc).timestamp()
        os.utime(path, (seconds, seconds))
    page, _ = open_browser()
    open_view(page, server, folder.name, date="today")
    page.locator("#date-menu summary").click()
    page.get_by_role("button", name=label, exact=True).click()
    ready(page)
    expect(page.locator("#entries .name")).to_have_text(matching)


@pytest.mark.parametrize(("lower", "upper", "matching"), [
    ("2026-11-15", "2026-11-15", ["Middle.txt"]),
    ("2026-11-15", "", ["Middle.txt", "New.txt"]),
    ("", "2026-11-15", ["Middle.txt", "Old.txt"]),
])
def test_custom_date_inclusive_and_open_bounds(lower, upper, matching, open_browser, server):
    folder = server.root / "Date fixtures"
    folder.mkdir()
    for name, day in [("Old", 14), ("Middle", 15), ("New", 16)]:
        path = folder / f"{name}.txt"
        path.touch()
        stamp = datetime(2026, 11, day, 23, 59, tzinfo=timezone.utc).timestamp()
        os.utime(path, (stamp, stamp))
    page, _ = open_browser()
    open_view(page, server, folder.name)
    page.locator("#date-menu summary").click()
    page.get_by_role("button", name="Custom date range").click()
    page.locator("#from-date").fill(lower)
    page.locator("#to-date").fill(upper)
    page.get_by_role("button", name="Apply dates").click()
    ready(page)
    expect(page.locator("#entries .name")).to_have_text(matching)


@pytest.mark.parametrize(("column", "ascending"), [
    ("Name", ["Alpha.txt", "Bravo.pdf", "Charlie.csv"]),
    ("Modified", ["Bravo.pdf", "Charlie.csv", "Alpha.txt"]),
    ("Type", ["Charlie.csv", "Bravo.pdf", "Alpha.txt"]),
    ("Size", ["Charlie.csv", "Alpha.txt", "Bravo.pdf"]),
])
def test_each_sort_column_both_directions_and_reload(column, ascending, open_browser, server):
    folder = server.root / "Sort fixtures"
    folder.mkdir()
    for name, size, seconds in [("Alpha.txt", 2, 3), ("Bravo.pdf", 3, 1), ("Charlie.csv", 1, 2)]:
        path = folder / name
        path.write_bytes(b"x" * size)
        os.utime(path, (NOW.timestamp() + seconds, NOW.timestamp() + seconds))
    page, _ = open_browser()
    open_view(page, server, folder.name, sort="size" if column == "Name" else "name")
    page.locator("#headings").get_by_role("button", name=column).click()
    ready(page)
    expect(page.locator("#entries .name")).to_have_text(ascending)
    page.locator("#headings").get_by_role("button", name=column).click()
    ready(page)
    expect(page.locator("#entries .name")).to_have_text(list(reversed(ascending)))
    page.reload()
    ready(page)
    expect(page.locator("#entries .name")).to_have_text(list(reversed(ascending)))


@pytest.mark.parametrize("engine", ENGINES)
def test_native_download_round_trip_and_retry_after_failure(engine, open_browser, server):
    page, errors = open_browser(engine)
    open_view(page, server)
    pattern = "**/api/download?*"
    page.route(pattern, lambda route: route.abort())
    menu(page, "Project Brief.pdf").get_by_role("button", name="Download").click()
    expect(page.locator("#notice")).to_contain_text("Download failed")
    page.get_by_role("button", name="Dismiss notification").click()
    expect(page.locator("#notice")).to_be_hidden()
    page.unroute(pattern)
    with page.expect_download() as transfer:
        menu(page, "Project Brief.pdf").get_by_role("button", name="Download").click()
    # Playwright's Linux WebKit port sanitizes spaces in blob download names.
    expected_name = "Project Brief.pdf"
    if engine == "webkit" and sys.platform.startswith("linux"):
        expected_name = "Project_Brief.pdf"
    assert transfer.value.suggested_filename == expected_name
    assert transfer.value.path().read_bytes() == (server.root / PROJECT_PATH / "Project Brief.pdf").read_bytes()
    expect(page.locator("#notice")).to_contain_text("Download complete")
    assert not errors


@pytest.mark.parametrize("engine", ENGINES)
def test_file_upload_and_mutation_round_trip(engine, open_browser, server):
    page, errors = open_browser(engine)
    open_view(page, server)
    page.get_by_role("button", name="New", exact=True).click()
    dialog = page.locator("dialog")
    dialog.get_by_role("textbox").fill("QA folder")
    dialog.get_by_role("button", name="Save").click()
    page.locator("#entries").get_by_role("link", name="QA folder").click()
    ready(page)
    expect(page.locator("#empty")).to_have_text("This folder is empty.")
    page.locator("#file-input").set_input_files(
        {"name": "Proof.txt", "mimeType": "text/plain", "buffer": b"proof"})
    expect(page.locator("#notice")).to_contain_text("Upload complete")
    menu(page, "Proof.txt").get_by_role("button", name="Make a copy").click()
    dialog.get_by_role("button", name="Save").click()
    duplicate = "Copy of Proof.txt"
    menu(page, duplicate).get_by_role("button", name="Rename").click()
    dialog.get_by_role("textbox").fill("Renamed.txt")
    dialog.get_by_role("button", name="Save").click()
    menu(page, "Renamed.txt").get_by_role("button", name="Move to…").click()
    dialog.get_by_role("textbox").fill(PROJECT_PATH)
    dialog.get_by_role("button", name="Save").click()
    expect(page.get_by_role("button", name="Actions for Renamed.txt")).to_have_count(0)
    assert (server.root / PROJECT_PATH / "Renamed.txt").read_bytes() == b"proof"
    menu(page, "Proof.txt").get_by_role("button", name="Delete").click()
    dialog.get_by_role("button", name="Cancel").click()
    menu(page, "Proof.txt").get_by_role("button", name="Delete").click()
    dialog.get_by_role("button", name="Delete", exact=True).click()
    expect(page.locator("#empty")).to_have_text("This folder is empty.")
    assert not errors


@pytest.mark.parametrize("kind", ["blocked", "unavailable"])
def test_clipboard_failures_show_dismissible_feedback(kind, open_browser, server):
    page, errors = open_browser()
    script = (
        "Object.defineProperty(navigator, 'clipboard', {value: {writeText: async () => "
        "{throw new DOMException('Permission denied', 'NotAllowedError')}}})"
        if kind == "blocked" else
        "Object.defineProperty(navigator, 'clipboard', {value: undefined})"
    )
    page.add_init_script(script)
    open_view(page, server)
    page.get_by_role("button", name="Copy folder link").click()
    expect(page.locator("#notice")).to_contain_text("Could not copy the link")
    page.get_by_role("button", name="Dismiss notification").click()
    expect(page.locator("#notice")).to_be_hidden()
    assert not errors


def test_failed_browse_can_recover_through_parent_link(open_browser, server):
    page, errors = open_browser()
    open_view(page, server, "Documents/Missing")
    expect(page.locator("#empty")).to_contain_text("The entry no longer exists")
    page.locator("#breadcrumbs").get_by_role("link", name="Documents").click()
    ready(page)
    expect(page.locator("#entries").get_by_role("link", name="Project Stardust")).to_be_visible()
    assert not errors


def test_interrupted_request_does_not_replace_newer_results(open_browser, server):
    page, errors = open_browser()
    open_view(page, server)
    delayed = []
    page.route("**/api/search?*", lambda route:
               delayed.append(route) if "q=brief" in route.request.url else route.continue_())
    page.get_by_role("searchbox").fill("brief")
    page.get_by_role("searchbox").press("Enter")
    expect(page.locator("#results")).to_have_attribute("aria-busy", "true")
    search(page, "Timeline")
    assert len(delayed) == 1
    expect(page.locator("mark")).to_have_text("Timeline")
    expect(page.locator("#results-note span").first).to_have_text('Results for "timeline"')
    assert not errors


@pytest.mark.parametrize(("size", "feedback"), [(0, "Upload complete"), (10 * 1024 * 1024 + 1, "1 failed")])
def test_upload_size_edges_and_visible_outcome(size, feedback, open_browser, server):
    page, errors = open_browser()
    open_view(page, server)
    page.locator("#file-input").set_input_files(
        {"name": "Size test.bin", "mimeType": "application/octet-stream", "buffer": b"x" * size})
    expect(page.locator("#notice")).to_contain_text(feedback)
    assert (server.root / PROJECT_PATH / "Size test.bin").exists() == (size == 0)
    assert not errors


@pytest.mark.parametrize("viewport", VIEWPORTS[:3], ids=["desktop", "tablet", "phone"])
@pytest.mark.parametrize("scene", ["browse", "search", "actions", "grid", "upload-menu", "delete-dialog"])
def test_chromium_visual_baseline(scene, viewport, open_browser, server, tmp_path):
    page, _ = open_browser(viewport=viewport)
    open_view(page, server, q="brief" if scene in ("search", "actions") else "",
              view="grid" if scene == "grid" else "")
    if scene == "actions":
        menu(page, "Project Brief.pdf")
    if scene == "upload-menu":
        page.locator("#upload-menu summary").click()
    if scene == "delete-dialog":
        menu(page, "Project Brief.pdf").get_by_role("button", name="Delete").click()
    page.evaluate("""async () => {
        await Promise.all([...document.images]
            .filter(image => image.getClientRects().length)
            .map(image => image.decode()));
    }""")
    screenshot = page.screenshot(animations="disabled", caret="hide")
    name = f"{scene}-{viewport[0]}.png"
    expected = BASELINES / name
    if os.environ.get("UPDATE_VISUAL_BASELINES") == "1":
        BASELINES.mkdir(exist_ok=True)
        expected.write_bytes(screenshot)
        return
    assert expected.is_file(), f"Missing baseline {expected}"
    actual = Image.open(BytesIO(screenshot)).convert("RGB")
    baseline = Image.open(expected).convert("RGB")
    assert actual.size == baseline.size
    difference = ImageChops.difference(actual, baseline)
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(red, ImageChops.lighter(green, blue))
    mask = maximum.point(lambda pixel: 255 if pixel > 24 else 0)
    changed_fraction = mask.histogram()[255] / (actual.width * actual.height)
    if changed_fraction > 0.001:
        (tmp_path / name).write_bytes(screenshot)
        difference.save(tmp_path / ("diff-" + name))
        pytest.fail(f"{changed_fraction:.2%} of pixels changed; actual and diff saved to {tmp_path}")


@pytest.mark.parametrize("engine", ENGINES)
def test_directory_upload_reconstructs_nested_paths(engine, open_browser, server, tmp_path):
    incoming = tmp_path / "Review bundle & files"
    (incoming / "Nested").mkdir(parents=True)
    (incoming / "Read me.txt").write_text("first")
    (incoming / "Nested/Notes [draft].txt").write_text("second")
    page, errors = open_browser(engine, VIEWPORTS[2])
    open_view(page, server)
    page.locator("#upload-menu summary").click()
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Folder upload").click()
    chooser.value.set_files(incoming)
    expect(page.locator("#notice")).to_contain_text("Upload complete")
    expect(page.locator("#notice")).to_contain_text("2 files")
    search(page, "[draft]")
    expect(page.locator("mark")).to_have_text("[draft]")
    assert (server.root / PROJECT_PATH / incoming.name / "Nested/Notes [draft].txt").read_text() == "second"
    assert not errors


def test_upload_failed_request_can_be_retried(open_browser, server):
    page, errors = open_browser()
    open_view(page, server)
    payload = {"name": "Retry.txt", "mimeType": "text/plain", "buffer": b"retry"}
    page.route("**/api/upload?*", lambda route: route.abort(), times=1)
    page.locator("#file-input").set_input_files(payload)
    expect(page.locator("#notice")).to_contain_text("0 uploaded, 1 failed")
    assert not (server.root / PROJECT_PATH / "Retry.txt").exists()
    page.locator("#file-input").set_input_files(payload)
    expect(page.locator("#notice")).to_contain_text("Upload complete")
    expect(page.locator("#entries .name")).to_contain_text(["Retry.txt"])
    assert not errors


def test_folder_copy_and_recursive_delete_keep_the_original(open_browser, server):
    page, errors = open_browser()
    open_view(page, server)
    panel = menu(page, "Research")
    expect(panel.get_by_role("button", name="Download")).to_have_count(0)
    panel.get_by_role("button", name="Make a copy").click()
    dialog = page.locator("dialog")
    dialog.get_by_role("button", name="Save").click()
    copy = "Copy of Research"
    expect(page.locator("#entries").get_by_role("link", name=copy)).to_be_visible()
    assert (server.root / PROJECT_PATH / copy / "Observations.txt").is_file()
    menu(page, copy).get_by_role("button", name="Delete").click()
    expect(dialog).to_contain_text("and its contents?")
    dialog.get_by_role("button", name="Delete", exact=True).click()
    expect(page.locator("#entries").get_by_role("link", name=copy)).to_have_count(0)
    assert (server.root / PROJECT_PATH / "Research/Observations.txt").is_file()
    assert not errors


def test_keyboard_opens_and_dismisses_item_menu(open_browser, server):
    page, errors = open_browser()
    open_view(page, server)
    trigger = page.get_by_role("button", name="Actions for Project Brief.pdf")
    trigger.focus()
    page.keyboard.press("Enter")
    expect(trigger).to_have_attribute("aria-expanded", "true")
    first = page.locator("#item-menu").get_by_role("button", name="Download")
    expect(first).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#item-menu").get_by_role("button", name="Copy link")).to_be_focused()
    page.keyboard.press("Escape")
    expect(trigger).to_have_attribute("aria-expanded", "false")
    expect(trigger).to_be_focused()
    assert not errors


@pytest.mark.parametrize("engine", ENGINES)
def test_copied_folder_and_file_urls_restore_unicode_paths(engine, open_browser, server):
    folder = server.root / PROJECT_PATH / "Résumé & plans"
    folder.mkdir()
    filename = "brief #1.txt"
    (folder / filename).write_text("evidence")
    page, errors = open_browser(engine)
    page.add_init_script("""Object.defineProperty(navigator, 'clipboard', {
      value: {writeText: async value => {window.lastCopied = value;}}
    })""")
    project = PROJECT_PATH + "/" + folder.name
    open_view(page, server, project, q="brief")
    page.get_by_role("button", name="Copy folder link").click()
    expect(page.locator("#notice")).to_contain_text("Link copied")
    page.goto(page.evaluate("window.lastCopied"))
    ready(page)
    expect(page.locator("#breadcrumbs [aria-current]")).to_have_text(folder.name)
    expect(page.get_by_role("searchbox")).to_have_value("")
    menu(page, filename).get_by_role("button", name="Copy link").click()
    expect(page.locator("#notice")).to_contain_text("Link copied")
    page.goto(page.evaluate("window.lastCopied"))
    ready(page)
    expect(page.locator(".entry.selected .name")).to_have_text(filename)
    assert not errors


@pytest.mark.parametrize(("operation", "value", "error"), [
    ("New", "Research", "already exists"),
    ("New", "bad/name", "valid folder name"),
    ("Move to…", "Missing folder", "no longer exists"),
])
def test_mutation_errors_leave_dialog_open_and_original_unchanged(
    operation, value, error, open_browser, server,
):
    page, errors = open_browser()
    open_view(page, server)
    if operation == "New":
        page.get_by_role("button", name=operation, exact=True).click()
    else:
        menu(page, "Project Brief.pdf").get_by_role("button", name=operation).click()
    dialog = page.locator("dialog")
    dialog.get_by_role("textbox").fill(value)
    dialog.get_by_role("button", name="Save").click()
    expect(dialog.get_by_role("alert")).to_contain_text(error)
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    assert (server.root / PROJECT_PATH / "Project Brief.pdf").is_file()
    assert not errors
