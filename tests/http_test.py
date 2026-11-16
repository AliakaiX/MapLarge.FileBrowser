"""Run against a real Kestrel process with a fresh filesystem for each case."""
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from urllib.parse import urlencode

PROJECT = Path(__file__).resolve().parents[1]
DLL = PROJECT / "bin/Release/net10.0/MapLarge.FileBrowser.dll"
ORIGIN = "http://localhost:5173"


class FileBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["dotnet", "build", "-c", "Release", str(PROJECT)], check=True)

    def setUp(self, *, include_symlinks=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "root"
        (self.root / "Docs").mkdir(parents=True)
        (self.root / "Docs/hello.txt").write_bytes(b"hello")
        (self.root / "README.txt").write_bytes(b"demo")
        (self.root.parent / "outside.txt").write_bytes(b"outside")
        if include_symlinks:
            (self.root / "link.txt").symlink_to(self.root.parent / "outside.txt")
            (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        self.server = subprocess.Popen(
            ["dotnet", str(DLL), "--urls", "http://127.0.0.1:0"],
            cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "Browser__Root": str(self.root),
                 "Browser__AllowedOrigins__0": ORIGIN,
                 "Logging__LogLevel__Microsoft.AspNetCore": "Warning"},
        )
        self.addCleanup(self.stop_server)
        startup_log = []
        for line in self.server.stdout:
            startup_log.append(line)
            match = re.search(r"Now listening on: http://127\.0\.0\.1:(\d+)", line)
            if match:
                self.port = int(match[1])
                self.assertNotEqual(self.port, 5080, "Kestrel ignored the ephemeral test URL")
                break
        else:
            self.fail("Kestrel exited before listening:\n" + "".join(startup_log))

    def stop_server(self):
        self.server.terminate()
        try:
            self.server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.communicate()

    def request(self, endpoint, params=None, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        url = "/api/" + endpoint
        if params:
            url += "?" + urlencode(params)
        try:
            connection.request(method, url, body, headers or {})
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def upload(self, name, content, path="Docs", origin=None):
        boundary = "maplarge-test"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"'
            f"\r\nContent-Type: application/octet-stream\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if origin:
            headers["Origin"] = origin
        return self.request("upload", {"path": path}, "POST", body, headers)

    def view(self, endpoint, params=None):
        status, _, body = self.request(endpoint, params)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_browse_and_totals(self):
        root = self.view("browse")
        self.assertEqual([entry["path"] for entry in root["entries"]], ["Docs", "README.txt"])
        self.assertEqual(root["totals"], {"fileCount": 1, "folderCount": 1, "fileBytes": 4})
        docs = self.view("browse", {"path": "Docs"})
        self.assertEqual(docs["entries"][0]["path"], "Docs/hello.txt")
        self.assertEqual(docs["totals"]["fileBytes"], 5)

    def test_search_scope_matches_and_empty(self):
        self.assertEqual(self.view("search", {"q": ".TXT"})["totals"]["fileBytes"], 9)
        self.assertEqual(self.view("search", {"q": "DOC"})["totals"]["folderCount"], 1)
        self.assertEqual(self.view("search", {"path": "Docs", "q": "read"})["entries"], [])
        self.assertEqual(self.request("search", {"q": " "})[0], 400)

    def test_download_range_and_attachment(self):
        status, headers, body = self.request("download", {"path": "Docs/hello.txt"},
                                             headers={"Range": "bytes=1-3"})
        self.assertEqual((status, body), (206, b"ell"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(headers["Content-Range"], "bytes 1-3/5")

    def test_upload_roundtrip_and_conflicts(self):
        status, headers, body = self.upload("résumé & draft.txt", b"\x00\xff")
        self.assertEqual(status, 201, body)
        path = json.loads(body)["path"]
        self.assertIn("%26", headers["Location"])
        self.assertEqual(self.request("download", {"path": path})[2], b"\x00\xff")
        self.assertEqual(self.upload("résumé & draft.txt", b"overwrite")[0], 409)
        self.assertEqual(self.request("download", {"path": path})[2], b"\x00\xff")
        self.assertEqual(self.upload("empty.txt", b"")[0], 201)

    def test_concurrent_upload(self):
        with concurrent.futures.ThreadPoolExecutor(4) as executor:
            statuses = list(executor.map(lambda _: self.upload("race.txt", b"payload")[0], range(8)))
        self.assertEqual(sorted(statuses), [201] + [409] * 7)
        self.assertEqual(list((self.root / "Docs").glob(".upload-*")), [])

    def test_invalid_paths_and_links(self):
        for path in ("../outside.txt", "/outside.txt", "Docs//hello.txt", "Docs/./hello.txt",
                     "Docs\\hello.txt", "C:/outside.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.request("download", {"path": path})[0], 400)
        self.assertEqual(self.request("download", {"path": "link.txt"})[0], 404)
        self.assertEqual(self.request("browse", {"path": "loop"})[0], 404)
        self.assertEqual(self.request("browse", {"path": "missing"})[0], 404)
        self.assertEqual(self.request("browse", {"path": "README.txt"})[0], 404)
        self.assertEqual(self.upload("../outside.txt", b"changed")[0], 400)
        self.assertEqual(self.upload("link.txt", b"changed", "")[0], 409)
        self.assertEqual((self.root.parent / "outside.txt").read_bytes(), b"outside")

    def test_upload_limits_and_missing_file(self):
        self.assertEqual(self.upload("max.bin", b"a" * (10 * 1024 * 1024))[0], 201)
        self.assertEqual(self.upload("over.bin", b"a" * (10 * 1024 * 1024 + 1))[0], 413)
        self.assertFalse((self.root / "Docs/over.bin").exists())
        self.assertEqual(self.request("upload", method="POST", body=b"")[0], 400)

    def test_browser_upload_origin(self):
        self.assertEqual(self.upload("denied.txt", b"bytes", origin="http://unlisted.example")[0], 403)
        self.assertFalse((self.root / "Docs/denied.txt").exists())
        self.assertEqual(self.upload("allowed.txt", b"bytes", origin=ORIGIN)[0], 201)

    def test_folder_sizes_are_opt_in(self):
        self.assertIsNone(self.view("browse")["entries"][0]["sizeBytes"])
        view = self.view("browse", {"folderSizes": "true"})
        self.assertEqual(view["entries"][0]["sizeBytes"], 5)
        self.assertEqual(view["totals"]["fileBytes"], 4)

    def test_folder_actions_and_validation(self):
        def post(route, **body):
            return self.request(route, method="POST", body=json.dumps(body),
                                headers={"Content-Type": "application/json"})[0]

        self.assertEqual(post("folders", path="Docs", name="Nested"), 200)
        self.assertEqual(post("folders", path="Docs", name="Nested"), 409)
        self.assertEqual(post("folders", path="Docs", name="../outside"), 400)
        self.assertEqual(post("actions", path="", operation="delete"), 400)
        self.assertEqual(post("actions", path="loop", operation="delete"), 404)
        self.assertEqual(post("actions", path="Docs", operation="move",
                              destination="Docs/Nested/Docs"), 400)
        self.assertEqual(post("actions", path="Docs", operation="copy",
                              destination="Docs/Cyclic"), 400)
        (self.root / "Docs/link").symlink_to(self.root.parent / "outside.txt")
        self.assertEqual(post("actions", path="Docs", operation="copy", destination="Copy"), 204)
        self.assertEqual((self.root / "Copy/hello.txt").read_bytes(), b"hello")
        self.assertTrue((self.root / "Copy/Nested").is_dir())
        self.assertFalse((self.root / "Copy/link").is_symlink())
        self.assertEqual(post("actions", path="Copy", operation="move", destination="New"), 204)
        self.assertEqual(post("actions", path="New", operation="delete"), 204)
        self.assertFalse((self.root / "New").exists())
        self.assertTrue((self.root / "Docs").is_dir())

    def test_cors_preflight(self):
        headers = {"Origin": ORIGIN, "Access-Control-Request-Method": "POST"}
        status, response_headers, _ = self.request("upload", method="OPTIONS", headers=headers)
        self.assertEqual(status, 204)
        self.assertEqual(response_headers["Access-Control-Allow-Origin"], ORIGIN)
        headers["Origin"] = "http://unlisted.example"
        _, response_headers, _ = self.request("upload", method="OPTIONS", headers=headers)
        self.assertIsNone(response_headers["Access-Control-Allow-Origin"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
