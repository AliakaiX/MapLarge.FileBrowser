# MapLarge File Browser

MapLarge File Browser is a local demo for browsing, searching, and managing files in a sample directory. ASP.NET Core serves the web interface and API from one process.

## Run locally

Install the [.NET 10 SDK](https://dotnet.microsoft.com/en-us/download/dotnet/10.0) for your operating system. Open the project folder in a terminal. It contains `MapLarge.FileBrowser.csproj`.

| Platform | Terminal | Command |
| --- | --- | --- |
| Windows | PowerShell | `dotnet run` |
| Linux | Terminal | `dotnet run` |
| macOS | Terminal | `dotnet run` |

Open [the demo](http://127.0.0.1:5080/) in your browser. Open **Documents → Project Stardust** and search for **brief** to try scoped search. Press Ctrl+C in the terminal to stop the app.

The demo uses the bundled `sample-data` directory. Changes made in the browser persist there. To use a disposable copy, copy the directory and start the app with `dotnet run -- --Browser:Root "path-to-copy"`.

Keep the demo on loopback and use disposable files. The API permits file operations without signing in. See the [API documentation](documentation/api.md) for endpoints and configuration.

## Run in Visual Studio or Rider

Open [MapLarge.FileBrowser.sln](MapLarge.FileBrowser.sln), which includes the web app and xUnit test project. Both IDEs use the shared [launch profile](Properties/launchSettings.json) to start Kestrel and open the dashboard at `http://127.0.0.1:5080/`.

- **Visual Studio on Windows.** Use an up-to-date Visual Studio 2026 installation with the **ASP.NET and web development** workload and the .NET 10 SDK. Set **MapLarge.FileBrowser** as the startup project if needed, select the **MapLarge.FileBrowser** launch profile, and press **F5**. Run xUnit from **Test > Test Explorer**. [Visual Studio and SDK compatibility](https://learn.microsoft.com/en-us/dotnet/core/porting/versioning-sdk-msbuild-vs)
- **Rider on Windows, Linux, or macOS.** Use Rider 2025.3 or later with the .NET 10 SDK. Select **MapLarge.FileBrowser** in the run configuration selector and choose **Run** or **Debug**. If the profile is missing, right-click `Properties/launchSettings.json` and choose **Generate Configurations**. Run xUnit from the **Unit Tests** tool window. [Rider launch profiles](https://www.jetbrains.com/help/rider/Running_LaunchSettings.html) and [Rider SDK support](https://support.jetbrains.com/hc/en-us/articles/40138743685522--NET-SDK-support-in-JetBrains-Rider)

## GitHub Actions

The [cross-platform workflow](.github/workflows/ci.yml) runs on pushes, pull requests, and manual dispatch. Each row runs both suites against the same source.

| GitHub runner | xUnit | Playwright |
| --- | --- | --- |
| Windows (`windows-latest`) | Filesystem service suite | Browser behavior suite |
| Ubuntu (`ubuntu-latest`) | Filesystem service suite | Browser behavior suite with Linux browser dependencies |
| macOS (`macos-latest`) | Filesystem service suite | Browser behavior suite |

- **xUnit** builds the .NET app and exercises browse and search, path validation, file and folder operations, downloads and uploads, overwrite conflicts, concurrent uploads, cancellation, and cleanup of temporary files.
- **Playwright** starts the app on a temporary Kestrel port for each test. Core flows cover Chromium, Firefox, and WebKit. The suite checks responsive navigation, scoped search, filters and sorting, URL and history state, keyboard interaction, uploads and downloads, file mutations, retries, and error feedback.

## Run the tests

Run xUnit from the project folder.

```sh
dotnet test MapLarge.FileBrowser.sln -c Release
```

For Playwright, install Python 3.12 and create a virtual environment. On Windows, use PowerShell.

```powershell
py -3.12 -m venv .test-env
.\.test-env\Scripts\python.exe -m pip install -r tests/requirements-browser.txt
.\.test-env\Scripts\python.exe -m playwright install chromium firefox webkit
.\.test-env\Scripts\python.exe -m pytest tests/test_browser_matrix.py -k 'not visual_baseline'
```

On Linux or macOS, use the following commands. On Linux, add `--with-deps` after `playwright install` to install system dependencies.

```sh
python3.12 -m venv .test-env
.test-env/bin/python -m pip install -r tests/requirements-browser.txt
.test-env/bin/python -m playwright install chromium firefox webkit
.test-env/bin/python -m pytest tests/test_browser_matrix.py -k 'not visual_baseline'
```

Two additional checks run separately from GitHub Actions. For Chromium screenshot comparisons, use a Linux environment with fonts matching the bundled baselines and select `-k visual_baseline`. For HTTP integration tests, run `python tests/http_test.py` on a system that permits creating symbolic links.
