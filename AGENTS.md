# MapLarge File Browser

## Start here

This is a local ASP.NET Core demo for browsing, searching, and managing files. It targets .NET 10 and serves a plain HTML, CSS, and JavaScript frontend from the same process as the API. There is no Node.js build step.

Open `MapLarge.FileBrowser.sln` in Visual Studio or Rider. Run the `MapLarge.FileBrowser` profile, or run `dotnet run --project MapLarge.FileBrowser.csproj` from the repository root. The dashboard serves at `http://127.0.0.1:5080/`. See [README.md](README.md) for IDE prerequisites and browser test setup.

## Project map

| Path | Responsibility |
| --- | --- |
| `Program.cs` | Kestrel binding, configuration, static files, middleware, and the `/api` routes. |
| `FileBrowser.cs` | Filesystem service for path validation, browse and search, metadata, downloads, uploads, and mutations. |
| `wwwroot/index.html`, `wwwroot/styles.css`, `wwwroot/app.js` | Dashboard markup, responsive styles, and browser behavior. Icons live in `wwwroot/icons/`. |
| `Properties/launchSettings.json` | Shared local Kestrel run and debug profile for both IDEs. |
| `sample-data/` | Writable demo fixtures. Browser tests copy them into an isolated temporary directory. |
| `documentation/api.md` | API contract, including routes, response shapes, and configuration. |
| `tests/MapLarge.FileBrowser.Tests/` | xUnit filesystem service tests using temporary directories. |
| `tests/test_browser_matrix.py` | Playwright behavior tests and visual comparisons against `tests/visual-baselines/`. Browser dependencies are pinned in `tests/requirements-browser.txt`. |
| `tests/http_test.py`, `tests/browser_test.py` | Standalone HTTP integration tests and a separate Chrome walkthrough. The Playwright matrix also reuses the Kestrel fixture from `http_test.py`. |
| `.github/workflows/ci.yml` | GitHub Actions matrix for Windows, Ubuntu, and macOS. |

## Working on the app

- Use a disposable copy of `sample-data/` when testing write operations manually. Set `Browser__Root` to an existing directory or pass `--Browser:Root` as an app argument. The configured directory and its ancestors must be regular directories.
- API paths are relative to the configured root and use forward slashes. Use the existing path validation and symlink checks for filesystem operations. Preserve conflict handling and staging cleanup when changing uploads or copies.
- Keep the default listener on loopback. The demo API permits file operations without authentication.
- Update `documentation/api.md` when a route, payload, or response changes. Changes to fixture names or layouts can affect browser assertions.

## Verify changes

For every feature, addition, bug fix, or other change, review the affected test coverage. Add or update tests in the same PR whenever existing tests do not cover the new or changed behavior. Use xUnit for filesystem and service logic, Playwright for browser-facing behavior, and both when the change spans those layers.

- Run `dotnet test MapLarge.FileBrowser.sln -c Release` from the repository root. xUnit covers browse, search, path validation, file operations, conflicts, cancellation, and concurrent uploads.
- After installing the Playwright browsers, run `python -m pytest tests/test_browser_matrix.py -k "not visual_baseline"` from the repository root in the test virtual environment. Use this for dashboard, static asset, and browser-facing API changes.
- For API changes, run `python tests/http_test.py` on a system that permits symlink creation. The suite covers HTTP status codes and headers, ranges, CORS and origin checks, and filesystem behavior through Kestrel.
- GitHub Actions runs xUnit and Playwright behavior tests on each operating system. The screenshot comparisons use Linux baselines and are run separately in a matching rendering environment.
