"""Chrome integration flow. Set SCREENSHOTS to capture the walkthrough."""
import os
from pathlib import Path
import shutil
import time
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from playwright.sync_api import expect, sync_playwright
from http_test import FileBrowserTests, PROJECT

DEST = os.environ.get("SCREENSHOTS")


class DashboardTests(FileBrowserTests):
    def setUp(self):
        super().setUp()
        shutil.copytree(PROJECT / "sample-data", self.root, dirs_exist_ok=True)
        now = time.time()
        for entry in self.root.rglob("Project Brief.pdf"):
            os.utime(entry, (now, now))
        for name in ["Docs", "link.txt", "loop"]:
            path = self.root / name
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path)
        self.base = f"http://127.0.0.1:{self.port}"
        runtime = sync_playwright().start()
        self.addCleanup(runtime.stop)
        browser = runtime.chromium.launch(executable_path=os.environ.get("CHROME_BIN", "/usr/bin/google-chrome"))
        self.addCleanup(browser.close)
        self.browser = browser

    def ready(self, page):
        expect(page.locator("#results")).to_have_attribute("aria-busy", "false")
        self.assertEqual(page.evaluate("document.documentElement.scrollWidth"), page.viewport_size["width"])

    def capture(self, page, name):
        if DEST:
            Path(DEST).mkdir(parents=True, exist_ok=True)
            page.evaluate("""async () => {
                await Promise.all([...document.images]
                    .filter(image => image.getClientRects().length)
                    .map(image => image.decode()));
            }""")
            page.screenshot(path=Path(DEST) / (name + ".png"), full_page=True, animations="disabled")

    def open(self, page, **query):
        page.goto(self.base + "/?" + urlencode(query))
        self.ready(page)

    def menu(self, page, name):
        page.get_by_role("button", name="Actions for " + name, exact=True).click()
        return page.locator("#item-menu")

    def search(self, page, term):
        page.get_by_role("searchbox").fill(term)
        page.get_by_role("searchbox").press("Enter")
        self.ready(page)

    def test_dashboard(self):
        for label, width, height in [("desktop", 1200, 630), ("tablet", 820, 700), ("mobile", 390, 780)]:
            with self.subTest(viewport=label):
                ctx = self.browser.new_context(viewport={"width": width, "height": height},
                    permissions=["clipboard-read", "clipboard-write"], accept_downloads=True,
                    has_touch=label != "desktop", is_mobile=label == "mobile")
                try:
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    self.run_flow(page, ctx, label)
                    self.assertEqual(errors, [])
                finally:
                    ctx.close()

    def test_narrow_phone_popovers(self):
        page = self.browser.new_page(viewport={"width": 320, "height": 640},
                                     is_mobile=True, has_touch=True)
        self.addCleanup(page.close)
        self.open(page, path="Documents/Project Stardust")
        for name in ["type-menu", "date-menu", "upload-menu"]:
            panel = page.locator("#" + name)
            panel.locator("summary").tap()
            self.ready(page)
            box = panel.locator(".popover").bounding_box()
            self.assertGreaterEqual(box["x"], 0)
            self.assertLessEqual(box["x"] + box["width"], 320)
            panel.locator("summary").tap()

    def test_popup_and_notification_visual_states(self):
        page = self.browser.new_page(viewport={"width": 1200, "height": 630},
                                     reduced_motion="reduce")
        self.addCleanup(page.close)
        self.open(page, path="Documents/Project Stardust", q="brief")
        note = page.locator("#results-note")
        expect(note).to_have_css("display", "flex")
        left, right = (note.locator("span").nth(i).bounding_box() for i in range(2))
        self.assertAlmostEqual(left["y"], right["y"], delta=1)
        self.assertGreater(right["x"], left["x"] + left["width"])
        expect(page.locator("mark")).to_have_css("background-color", "rgb(255, 236, 167)")

        types = page.locator("#type-menu")
        modified = page.locator("#date-menu")
        types.locator("summary").click()
        expect(types.locator(".popover")).to_be_visible()
        modified.locator("summary").click()
        expect(types.locator(".popover")).to_be_hidden()
        expect(modified.locator(".popover")).to_be_visible()
        page.keyboard.press("Escape")
        expect(modified.locator(".popover")).to_be_hidden()
        expect(modified.locator("summary")).to_be_focused()

        menu = self.menu(page, "Project Brief.pdf")
        download = menu.get_by_role("button", name="Download")
        download.hover()
        expect(download).to_have_css("background-color", "rgb(238, 242, 249)")
        expect(download).to_have_css("outline-style", "none")
        page.locator("#query").click()
        expect(menu).to_be_hidden()

        def abort_download(route):
            expect(page.locator("#notice")).to_contain_text("Download started")
            expect(page.locator("#notice .spin")).to_be_visible()
            route.abort()

        page.route("**/api/download?*", abort_download)
        self.menu(page, "Project Brief.pdf").get_by_role("button", name="Download").click()
        expect(page.locator("#notice")).to_contain_text("Download failed")
        expect(page.locator("#notice .spin")).to_have_count(0)
        page.get_by_role("button", name="Dismiss notification").click()
        expect(page.locator("#notice")).to_be_hidden()

    def test_combined_filters_validation_and_reset(self):
        page = self.browser.new_page(viewport={"width": 1200, "height": 630})
        self.addCleanup(page.close)
        self.open(page, path="Documents/Project Stardust")
        page.locator("#type-menu summary").click()
        page.get_by_role("checkbox", name="PDFs").check()
        page.get_by_role("checkbox", name="Spreadsheets").check()
        self.ready(page)
        expect(page.locator("#counts")).to_have_text("0 folders · 2 files")
        page.locator("#date-menu summary").click()
        page.get_by_role("button", name="Custom date range").click()
        page.locator("#from-date").fill("2020-02-02")
        page.locator("#to-date").fill("2020-02-01")
        page.get_by_role("button", name="Apply dates").click()
        expect(page.locator("#notice")).to_contain_text("Choose an end date")
        expect(page.locator("#dates")).to_be_visible()
        page.locator("#to-date").fill("2020-02-03")
        page.get_by_role("button", name="Apply dates").click()
        self.ready(page)
        expect(page.locator("#counts")).to_have_text("0 folders · 0 files")
        expect(page.locator("#total")).to_have_text("0 B total")
        expect(page.locator("#empty")).to_contain_text("No matches")
        page.locator("#date-menu summary").click()
        page.get_by_role("button", name="Any time").click()
        self.ready(page)
        expect(page.locator("#counts")).to_have_text("0 folders · 2 files")
        self.search(page, "brief")
        expect(page.locator("mark")).to_have_text("Brief")
        page.get_by_role("searchbox").fill("")
        page.get_by_role("button", name="Search", exact=True).click()
        self.ready(page)
        expect(page.locator("#results-note")).to_be_hidden()
        expect(page.locator("#counts")).to_have_text("0 folders · 2 files")

    def test_upload_partial_success_and_dialog_validation(self):
        page = self.browser.new_page(viewport={"width": 820, "height": 700})
        self.addCleanup(page.close)
        self.open(page, path="Documents/Project Stardust")
        folder = self.root / "Documents/Project Stardust"
        original = (folder / "Meeting Notes.txt").read_bytes()
        page.locator("#file-input").set_input_files([
            {"name": "Meeting Notes.txt", "mimeType": "text/plain", "buffer": b"do not overwrite"},
            {"name": "New upload.txt", "mimeType": "text/plain", "buffer": b"new"},
        ])
        expect(page.locator("#notice")).to_contain_text("1 uploaded, 1 failed")
        expect(page.get_by_role("button", name="Actions for New upload.txt")).to_be_visible()
        self.assertEqual((folder / "Meeting Notes.txt").read_bytes(), original)
        self.assertEqual((folder / "New upload.txt").read_bytes(), b"new")

        menu = self.menu(page, "New upload.txt")
        menu.get_by_role("button", name="Rename").click()
        dialog = page.locator("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("textbox")).to_be_focused()
        dialog.get_by_role("textbox").fill("bad/name")
        dialog.get_by_role("button", name="Save").click()
        expect(dialog.get_by_role("alert")).to_contain_text("without directory separators")
        dialog.get_by_role("textbox").fill("valid.txt")
        expect(dialog.get_by_role("alert")).to_be_hidden()
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        self.assertFalse((folder / "valid.txt").exists())
        self.assertTrue((folder / "New upload.txt").is_file())

    def run_flow(self, page, ctx, label):
        project = "Documents/Project Stardust"
        shot = lambda name: self.capture(page, f"{label}-{name}")
        self.open(page, path="Documents")
        expect(page.locator("#tree [aria-current=page]")).to_have_text("Documents")
        expect(page.locator("#counts")).to_have_text("3 folders · 0 files")
        shot("documents")
        folder = page.locator("#entries").get_by_role("link", name="Project Stardust")
        if label == "desktop":
            folder.hover()
            shot("folder-hover")
        if label != "desktop":
            folder.tap()
        else:
            folder.click()
        self.ready(page)
        expect(page.locator("#tree [aria-current=page]")).to_have_text("Project Stardust")
        expect(page.locator("#breadcrumbs [aria-current=page]")).to_have_text("Project Stardust")
        shot("project")
        self.search(page, "bRiEf")
        expect(page.locator("mark")).to_have_text("Brief")
        expect(page.locator("#results-note span").first).to_have_text('Results for "brief"')
        expect(page.locator("#results-note span").last).to_have_text("1 item found")
        shot("search")
        page.reload()
        self.ready(page)
        expect(page.locator("mark")).to_have_text("Brief")
        page.go_back()
        self.ready(page)
        expect(page.get_by_role("searchbox")).to_have_value("")
        page.go_forward()
        self.ready(page)
        expect(page.locator("mark")).to_have_text("Brief")
        if label == "desktop":
            trigger = page.get_by_role("button", name="Actions for Project Brief.pdf")
            trigger.hover()
            expect(trigger).to_have_css("background-color", "rgb(238, 242, 249)")
        menu = self.menu(page, "Project Brief.pdf")
        expect(menu.locator("button img")).to_have_count(6)
        self.assertTrue(menu.locator("button img").evaluate_all(
            "async images => { await Promise.all(images.map(image => image.decode())); "
            "return images.every(image => image.naturalWidth > 0); }"))
        shot("actions")
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()
        expect(page.get_by_role("button", name="Actions for Project Brief.pdf")).to_be_focused()

        def pause_download(route):
            response = route.fetch()
            expect(page.locator("#notice .spin")).to_be_visible()
            expect(page.locator("#notice .spin")).to_have_css("width", "17px")
            shot("download-started")
            route.fulfill(response=response)

        page.route("**/api/download?*", pause_download)
        with page.expect_download() as event:
            self.menu(page, "Project Brief.pdf").get_by_role("button", name="Download").click()
        download = event.value
        self.assertEqual(download.suggested_filename, "Project Brief.pdf")
        self.assertEqual(Path(download.path()).read_bytes(), (self.root / project / "Project Brief.pdf").read_bytes())
        expect(page.locator("#notice")).to_contain_text("Download complete")
        shot("download-complete")
        page.get_by_role("button", name="Dismiss notification").click()
        expect(page.locator("#notice")).to_be_hidden()
        page.unroute("**/api/download?*")
        copy = page.get_by_role("button", name="Copy folder link")
        if label == "desktop":
            copy.hover()
            expect(copy.locator(".tooltip")).to_be_visible()
            shot("copy-tooltip")
        copy.click()
        expect(page.locator("#notice")).to_contain_text("Link copied")
        folder_url = page.evaluate("navigator.clipboard.readText()")
        self.assertIn("Project+Stardust", folder_url)
        page.goto(folder_url)
        self.ready(page)
        self.menu(page, "Project Brief.pdf").get_by_role("button", name="Copy link").click()
        expect(page.locator("#notice")).to_contain_text("Link copied")
        page.goto(page.evaluate("navigator.clipboard.readText()"))
        self.ready(page)
        expect(page.locator(".entry.selected")).to_contain_text("Project Brief.pdf")
        shot("file-link")

        page.locator("#type-menu summary").click()
        shot("types")
        page.get_by_role("checkbox", name="PDFs").check()
        self.ready(page)
        expect(page.locator("#entries .entry")).to_have_count(1)
        page.reload()
        self.ready(page)
        expect(page.locator("#entries .entry")).to_have_count(1)
        page.locator("#date-menu summary").click()
        shot("modified")
        page.get_by_role("button", name="Last 7 days").click()
        self.ready(page)
        expect(page.locator("#entries .entry")).to_have_count(1)
        page.locator("#date-menu summary").click()
        page.get_by_role("button", name="Custom date range").click()
        page.locator("#from-date").fill("2000-01-01")
        page.locator("#to-date").fill("2000-01-02")
        shot("custom-date")
        page.get_by_role("button", name="Apply dates").click()
        self.ready(page)
        expect(page.locator("#empty")).to_contain_text("No matches")
        shot("empty")
        self.open(page, path=project)
        page.get_by_role("button", name="Grid view").click()
        self.ready(page)
        expect(page.locator("#entries")).to_have_class("grid")
        shot("grid")
        page.get_by_role("button", name="List view").click()
        self.ready(page)
        page.get_by_role("button", name="Name ↑").click()
        self.ready(page)
        expect(page.locator("#entries .entry").first).to_contain_text("Timeline.csv")
        shot("sort")

        self.open(page, path=project)
        page.get_by_role("button", name="New", exact=False).click()
        dialog = page.locator("dialog")
        name = "QA " + label
        dialog.get_by_role("textbox").fill(name)
        shot("new-folder")
        dialog.get_by_role("button", name="Save").click()
        self.ready(page)
        page.locator("#entries").get_by_role("link", name=name, exact=True).click()
        self.ready(page)
        expect(page.locator("#empty")).to_have_text("This folder is empty.")
        page.locator("#upload-menu summary").click()
        shot("upload")
        with page.expect_file_chooser() as choose:
            page.get_by_role("button", name="File upload").click()
        payload = {"name": "Review [draft].txt", "mimeType": "text/plain", "buffer": b"reviewed"}
        choose.value.set_files(payload)
        expect(page.locator("#notice")).to_contain_text("Upload complete")
        self.ready(page)
        shot("uploaded")
        page.locator("#file-input").set_input_files(payload)
        expect(page.locator("#notice")).to_contain_text("1 failed")
        self.search(page, "[draft]")
        expect(page.locator("mark")).to_have_text("[draft]")
        self.open(page, path=project + "/" + name)
        with TemporaryDirectory() as staging:
            folder = Path(staging) / "Review Set"
            (folder / "Nested").mkdir(parents=True)
            (folder / "Nested" / "Evidence.txt").write_text("test evidence")
            page.locator("#upload-menu summary").click()
            with page.expect_file_chooser() as choose:
                page.get_by_role("button", name="Folder upload").click()
            choose.value.set_files(folder)
            expect(page.locator("#notice")).to_contain_text("Upload complete")
            self.ready(page)
            expect(page.locator("#entries").get_by_role("link", name="Review Set")).to_be_visible()
        self.search(page, "Evidence")
        expect(page.locator("mark")).to_have_text("Evidence")
        shot("nested-upload")
        self.open(page, path=project + "/" + name)

        self.menu(page, payload["name"]).get_by_role("button", name="Rename").click()
        dialog.get_by_role("textbox").fill("Final.txt")
        shot("rename")
        dialog.get_by_role("button", name="Save").click()
        expect(page.get_by_role("button", name="Actions for Final.txt")).to_be_visible()
        self.menu(page, "Final.txt").get_by_role("button", name="Make a copy").click()
        dialog.get_by_role("textbox").fill("Final.txt")
        dialog.get_by_role("button", name="Save").click()
        expect(dialog.get_by_role("alert")).to_contain_text("already exists")
        shot("conflict")
        dialog.get_by_role("textbox").fill("Copy.txt")
        shot("copy")
        dialog.get_by_role("button", name="Save").click()
        expect(page.get_by_role("button", name="Actions for Copy.txt")).to_be_visible()
        self.menu(page, "Copy.txt").get_by_role("button", name="Move to…").click()
        dialog.get_by_role("textbox").fill(project)
        shot("move")
        dialog.get_by_role("button", name="Save").click()
        expect(page.get_by_role("button", name="Actions for Copy.txt")).to_have_count(0)
        self.assertEqual((self.root / project / "Copy.txt").read_bytes(), b"reviewed")
        (self.root / project / "Copy.txt").unlink()
        self.open(page, path=project)
        self.menu(page, name).get_by_role("button", name="Delete").click()
        shot("delete")
        dialog.get_by_role("button", name="Cancel").click()
        expect(page.locator("#entries").get_by_role("link", name=name, exact=True)).to_be_visible()
        self.menu(page, name).get_by_role("button", name="Delete").click()
        dialog.get_by_role("button", name="Delete", exact=True).click()
        expect(page.locator("#entries").get_by_role("link", name=name, exact=True)).to_have_count(0)
        self.assertFalse((self.root / project / name).exists())
        self.open(page, path="Missing directory")
        expect(page.locator("#empty")).to_be_visible()
        shot("missing")


if __name__ == "__main__":
    import unittest
    suite = unittest.TestSuite(DashboardTests(name) for name in
                               ("test_dashboard", "test_narrow_phone_popovers",
                                "test_popup_and_notification_visual_states",
                                "test_combined_filters_validation_and_reset",
                                "test_upload_partial_success_and_dialog_validation"))
    raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
