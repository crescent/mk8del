"""Language switching against the real launcher; installer UI uses isolated API fixtures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps/launcher/web"

def open_build(page):
    # Blur the field like a real click would, let its autosave (and refresh) finish, then open the step: action() drops clicks while busy.
    page.evaluate("document.activeElement?.blur()")
    while page.evaluate("busy"):
        page.wait_for_timeout(50)
    page.evaluate("openStep('step-build', true)")


class LanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def start_launcher(self, folder):
        state_file = folder / "launcher.json"
        if state_file.exists():
            state_file.unlink()
        process = subprocess.Popen(
            [os.environ.get("MK8_LAUNCHER_EXE", str(ROOT / "target/release/Launcher.exe")),
             "--headless", "--data-dir", str(folder)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.addCleanup(lambda: process.poll() is None and process.terminate())
        deadline = time.monotonic() + 15
        while not state_file.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail("Launcher did not start")
            time.sleep(0.05)
        return process, json.loads(state_file.read_text())["url"]

    def test_language_survives_new_server_and_preserves_user_data(self):
        with tempfile.TemporaryDirectory(prefix="mk8-language-ui-") as temporary:
            folder = Path(temporary)
            process, url = self.start_launcher(folder)
            page = self.browser.new_page(locale="en-US", viewport={"width": 1365, "height": 1024})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(url)
                expect(page.get_by_role("heading", name="Installation", exact=True)).to_be_visible()
                page.get_by_role("button", name="Profile", exact=True).click()
                page.get_by_label("Display name", exact=True).fill("Instalar")
                page.get_by_role("button", name="Save name", exact=True).click()
                expect(page.locator("#notice")).to_have_text("Name saved on this PC.")
                page.get_by_role("button", name="Multiplayer", exact=True).click()
                page.locator("#invite-input").fill("mk8://literal-private-value")
                page.get_by_label("Language", exact=True).select_option("pt-BR")
                expect(page.locator("html")).to_have_attribute("lang", "pt-BR")
                page.get_by_role("button", name="Perfil", exact=True).click()
                expect(page.get_by_label("Nome de exibição", exact=True)).to_have_value("Instalar")
                page.get_by_role("button", name="Multijogador", exact=True).click()
                expect(page.locator("#invite-input")).to_have_value("mk8://literal-private-value")
                expect(page.locator("#notice")).to_have_text("Nome salvo neste computador.")
                page.get_by_label("Idioma", exact=True).select_option("en")
                expect(page.locator("#notice")).to_have_text("Name saved on this PC.")
                page.get_by_role("button", name="Install", exact=True).click()
                open_build(page)
                page.get_by_role("button", name="Start build", exact=True).click()
                expect(page.locator("#notice")).to_contain_text("NPDM unavailable:")
                token = page.locator('meta[name="mk8-token"]').get_attribute("content")
                response = page.request.post(url + "/api/language", headers={"X-MK8-Token": token}, data=json.dumps({"language": "fr"}))
                self.assertEqual(response.status, 400)
                self.assertEqual(json.loads((folder / "language.json").read_text()), "en")
                self.assertEqual(json.loads((folder / "username.json").read_text()), "Instalar")
                process.terminate()
                process.wait(timeout=10)
                process, url = self.start_launcher(folder)
                page.goto(url)
                expect(page.locator("html")).to_have_attribute("lang", "en")
                expect(page.get_by_role("heading", name="Installation", exact=True)).to_be_visible()
                page.get_by_role("button", name="Profile", exact=True).click()
                expect(page.get_by_label("Display name", exact=True)).to_have_value("Instalar")
                page.get_by_label("Language", exact=True).select_option("pt-BR")
                page.reload()
                expect(page.get_by_role("heading", name="Instalação", exact=True)).to_be_visible()
                page.get_by_label("Idioma", exact=True).select_option("en")
                expect(page.locator("html")).to_have_attribute("lang", "en")
                token = page.locator('meta[name="mk8-token"]').get_attribute("content")
                status = page.request.get(url + "/api/status", headers={"X-MK8-Token": token}).json()["data"]
                status["build"].update(state="running", phase="Compilando para o seu PC", percent=42, started_ms=int(time.time() * 1000) - 60000)
                page.route("**/api/status", lambda route: route.fulfill(json={"ok": True, "data": status}))
                expect(page.locator("#build-phase")).to_have_text("Compiling for your PC")
                expect(page.locator("#build-summary")).to_have_text("Compiling for your PC · 42%")
                page.get_by_label("Language", exact=True).select_option("pt-BR")
                expect(page.locator("#build-phase")).to_have_text("Compilando para o seu PC")
                page.get_by_label("Idioma", exact=True).select_option("en")
                expect(page.locator("#build-phase")).to_have_text("Compiling for your PC")
                page.unroute("**/api/status")
                expect(page.locator("#build-live")).to_be_hidden()
                page.set_viewport_size({"width": 390, "height": 844})
                self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                screenshots = ROOT / "local/screenshots"
                screenshots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshots / "launcher-english-mobile.png"), full_page=True)
                page.set_viewport_size({"width": 1365, "height": 1024})
                page.screenshot(path=str(screenshots / "launcher-english.png"), full_page=True)
                self.assertEqual(errors, [])
            finally:
                page.close()
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)

    def test_installer_switches_language_and_keeps_paths(self):
        page = self.browser.new_page(locale="pt-BR", viewport={"width": 880, "height": 620})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        requests = []

        def route(request):
            path = request.request.url.split("mk8.test", 1)[1]
            if path.startswith("/api/"):
                body = request.request.post_data_json if request.request.method == "POST" else {}
                requests.append((path, body))
                if path == "/api/state":
                    data = {"version": "0.1.5", "app": "C:\\Apps\\MK8-online", "data": "D:\\Games\\MK8-online", "desktop": True, "update": False, "needed_gib": 30}
                elif path == "/api/space":
                    data = {"free_gib": 21.5, "hard_disk": True}
                elif path == "/api/install":
                    request.fulfill(json={"ok": False, "error": "A instalação já está em andamento."}, status=400)
                    return
                else:
                    data = {}
                request.fulfill(json={"ok": True, "data": data})
            else:
                file = WEB / ("setup.html" if path == "/" else path[1:])
                mime = {".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".css": "text/css"}[file.suffix]
                request.fulfill(body=file.read_text(encoding="utf-8").replace("__TOKEN__", "test").replace("__LANGUAGE__", ""), content_type=mime + "; charset=utf-8")

        try:
            page.route("http://mk8.test/**", route)
            page.goto("http://mk8.test/")
            expect(page.get_by_role("heading", name="Bem-vindo ao MK8-online")).to_be_visible()
            page.get_by_label("Idioma", exact=True).select_option("en")
            expect(page.get_by_role("heading", name="Welcome to MK8-online")).to_be_visible()
            page.get_by_role("button", name="Get started").click()
            page.locator("#data").fill("E:\\Instalar\\MK8-online")
            expect(page.locator("#space")).to_contain_text("Only 21.5 GB free")
            expect(page.locator("#space")).to_contain_text("hard drive")
            page.get_by_role("button", name="Install", exact=True).click()
            expect(page.locator("#paths-error")).to_have_text("Installation is already in progress.")
            self.assertEqual(next(body["language"] for path, body in requests if path == "/api/install"), "en")
            page.get_by_label("Language", exact=True).select_option("pt-BR")
            expect(page.locator("#space")).to_contain_text("Só 21,5 GB")
            expect(page.locator("#paths-error")).to_have_text("A instalação já está em andamento.")
            expect(page.locator("#data")).to_have_value("E:\\Instalar\\MK8-online")
            page.get_by_label("Idioma", exact=True).select_option("en")
            expect(page.locator("html")).to_have_attribute("lang", "en")
            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
            screenshots = ROOT / "local/screenshots"
            screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshots / "installer-english.png"), full_page=True)
            self.assertEqual(errors, [])
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
