using Microsoft.AspNetCore.Http;
using Xunit;

public sealed class FileBrowserTests : IDisposable
{
    private readonly DirectoryInfo workspace = Directory.CreateTempSubdirectory("maplarge-tests-");
    private readonly string root;
    private readonly FileBrowser browser;

    public FileBrowserTests()
    {
        root = workspace.CreateSubdirectory("files").FullName;
        browser = new FileBrowser(workspace.FullName, "files");
        Directory.CreateDirectory(Local("Docs/Nested"));
        File.WriteAllText(Local("Docs/Brief.txt"), "brief");
        File.WriteAllText(Local("Docs/Nested/brief notes.txt"), "notes");
        File.WriteAllText(Local("Outside scope.txt"), "outside");
    }

    [Fact]
    public void RootMustExist()
    {
        Assert.Throws<InvalidOperationException>(() => new FileBrowser(workspace.FullName, "missing"));
    }

    [Fact]
    public void BrowseReturnsImmediateChildrenWithSeparateFileAndFolderTotals()
    {
        var view = browser.List("Docs", null, default, folderSizes: true);
        Assert.Equal("Docs", view.Path);
        Assert.Collection(view.Entries,
            folder =>
            {
                Assert.Equal("Docs/Nested", folder.Path);
                Assert.Equal("folder", folder.Kind);
                Assert.Equal(5L, folder.SizeBytes);
            },
            file =>
            {
                Assert.Equal("Docs/Brief.txt", file.Path);
                Assert.Equal("file", file.Kind);
                Assert.Equal(5L, file.SizeBytes);
                Assert.Equal(DateTimeKind.Utc, file.ModifiedUtc.Kind);
            });
        Assert.Equal(new ViewTotals(1, 1, 5), view.Totals);
        Assert.Null(browser.List("Docs", null, default).Entries[0].SizeBytes);
    }

    [Fact]
    public void SearchIsRecursiveScopedAndCaseInsensitive()
    {
        File.WriteAllText(Local("Brief outside.txt"), "ignored");
        var view = browser.List("Docs", "bRiEf", default);
        Assert.Equal(["Docs/Brief.txt", "Docs/Nested/brief notes.txt"], view.Entries.Select(entry => entry.Path));
        Assert.Equal(new ViewTotals(2, 0, 10), view.Totals);
        Assert.Empty(browser.List("Docs", "[brief]", default).Entries);
    }

    [Fact]
    public void BrowseHonorsCancellation()
    {
        Assert.ThrowsAny<OperationCanceledException>(() =>
            browser.List("Docs", null, new CancellationToken(true)));
    }

    [Theory]
    [InlineData("../Outside scope.txt")]
    [InlineData("Docs/../Outside scope.txt")]
    [InlineData("/Docs/Brief.txt")]
    [InlineData("Docs//Brief.txt")]
    [InlineData("Docs/./Brief.txt")]
    [InlineData("Docs\\Brief.txt")]
    [InlineData("C:/file")]
    [InlineData("Docs/.UPLOAD-stage")]
    public void InvalidRelativePathIsRejected(string path)
    {
        Assert.Equal(400, Assert.Throws<BrowserException>(() => browser.OpenDownload(path)).Status);
    }

    [Fact]
    public async Task DownloadStreamsBytesAndRejectsDirectories()
    {
        await using var stream = browser.OpenDownload("Docs/Brief.txt");
        using var output = new MemoryStream();
        await stream.CopyToAsync(output);
        Assert.Equal("brief"u8.ToArray(), output.ToArray());
        Assert.Equal(404, Assert.Throws<BrowserException>(() => browser.OpenDownload("Docs")).Status);
        Assert.Equal(404, Assert.Throws<BrowserException>(() => browser.List("Docs/Brief.txt", null, default)).Status);
    }

    [Theory]
    [InlineData("")]
    [InlineData(" ")]
    [InlineData("sub/folder")]
    [InlineData("bad.")]
    [InlineData("bad ")]
    [InlineData(".upload-hidden")]
    public void InvalidFolderNameLeavesDestinationUntouched(string name)
    {
        Assert.Equal(400, Assert.Throws<BrowserException>(() => browser.CreateFolder("Docs", name)).Status);
        Assert.Equal(2, Directory.GetFileSystemEntries(Local("Docs")).Length);
    }

    [Fact]
    public void CreateFolderReportsRelativeMetadataAndConflicts()
    {
        var result = browser.CreateFolder("Docs", "Création & QA");
        Assert.Equal("Docs/Création & QA", result.Path);
        Assert.True(Directory.Exists(Local(result.Path)));
        Assert.Equal(409, Assert.Throws<BrowserException>(() => browser.CreateFolder("Docs", result.Name)).Status);
        Assert.Equal(409, Assert.Throws<BrowserException>(() => browser.CreateFolder("Docs", "Brief.txt")).Status);
    }

    [Fact]
    public async Task UploadPreservesBinaryContentAndRejectsOverwrite()
    {
        byte[] payload = [0, 255, 13, 10];
        using var input = new MemoryStream(payload);
        var entry = await browser.Upload("Docs", new FormFile(input, 0, payload.Length, "file", "Résumé.txt"), default);
        Assert.Equal("Docs/Résumé.txt", entry.Path);
        Assert.Equal(payload.Length, entry.SizeBytes);
        var conflict = await Assert.ThrowsAsync<BrowserException>(() =>
            browser.Upload("Docs", new FormFile(Stream.Null, 0, 0, "file", entry.Name), default));
        Assert.Equal(409, conflict.Status);
        Assert.Equal(payload, await File.ReadAllBytesAsync(Local(entry.Path)));
        Assert.Empty(Directory.GetFiles(Local("Docs"), ".upload-*"));
    }

    [Fact]
    public async Task ActiveUploadIsHiddenAndPreventsMovingOrDeletingItsAncestor()
    {
        var started = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var file = new ControlledFormFile(async (destination, ct) =>
        {
            await destination.WriteAsync(new byte[] { 42 }, ct);
            started.SetResult();
            await release.Task.WaitAsync(ct);
        });
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var upload = browser.Upload("Docs/Nested", file, timeout.Token);
        try
        {
            await started.Task.WaitAsync(timeout.Token);
            Assert.Single(browser.List("Docs/Nested", null, default).Entries);
            Assert.Equal(409, Assert.Throws<BrowserException>(() => Apply("Docs", "move", "Moved")).Status);
            Assert.Equal(409, Assert.Throws<BrowserException>(() => Apply("Docs", "delete")).Status);
        }
        finally
        {
            release.TrySetResult();
            await upload;
        }
        Assert.Equal(new byte[] { 42 }, File.ReadAllBytes(Local("Docs/Nested/controlled.bin")));
        Assert.Empty(Directory.GetFiles(Local("Docs/Nested"), ".upload-*"));
    }

    [Fact]
    public async Task CompetingUploadsCommitExactlyOnePayload()
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var first = browser.Upload("Docs", WaitingFile(1), timeout.Token);
        var second = browser.Upload("Docs", WaitingFile(2), timeout.Token);
        release.SetResult();

        var outcomes = await Task.WhenAll(Outcome(first, 1), Outcome(second, 2));
        var winner = Assert.Single(outcomes.OfType<byte>());
        Assert.Equal(new[] { winner }, File.ReadAllBytes(Local("Docs/controlled.bin")));
        Assert.Empty(Directory.GetFiles(Local("Docs"), ".upload-*"));

        ControlledFormFile WaitingFile(byte payload) => new(async (destination, ct) =>
        {
            await release.Task.WaitAsync(ct);
            await destination.WriteAsync(new[] { payload }, ct);
        });

        static async Task<byte?> Outcome(Task<BrowserEntry> upload, byte payload)
        {
            try
            {
                await upload;
                return payload;
            }
            catch (BrowserException exception)
            {
                Assert.Equal(409, exception.Status);
                return null;
            }
        }
    }

    [Fact]
    public void StagingEntriesAreExcludedFromBrowseSearchAndFolderSizes()
    {
        Directory.CreateDirectory(Local("Docs/.upload-copy"));
        File.WriteAllText(Local("Docs/.upload-copy/brief.txt"), "staged copy");
        File.WriteAllText(Local("Docs/.upload-file"), "staged upload");
        var docs = browser.List(null, null, default, folderSizes: true).Entries[0];
        Assert.Equal(10L, docs.SizeBytes);
        Assert.Equal(2, browser.List("Docs", "brief", default).Entries.Length);
        Assert.Equal(2, browser.List("Docs", null, default).Entries.Length);
    }

    [Fact]
    public async Task CanceledOrFailedUploadRemovesPartialFile()
    {
        using var cancellation = new CancellationTokenSource();
        var canceled = new ControlledFormFile(async (destination, ct) =>
        {
            await destination.WriteAsync(new byte[] { 42 }, ct);
            cancellation.Cancel();
        });
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => browser.Upload("Docs", canceled, cancellation.Token));
        var failed = new ControlledFormFile(async (destination, ct) =>
        {
            await destination.WriteAsync(new byte[] { 42 }, ct);
            throw new IOException("Simulated write failure");
        });
        await Assert.ThrowsAsync<IOException>(() => browser.Upload("Docs", failed, default));
        Assert.False(File.Exists(Local("Docs/controlled.bin")));
        Assert.Empty(Directory.GetFiles(Local("Docs"), ".upload-*"));
    }

    [Fact]
    public void CopyMoveAndDeletePreserveExpectedTree()
    {
        Apply("Docs", "copy", "Copy");
        Apply("Copy/Brief.txt", "move", "Copy/Renamed.txt");
        Assert.False(File.Exists(Local("Copy/Brief.txt")));
        Assert.Equal("brief", File.ReadAllText(Local("Copy/Renamed.txt")));
        Assert.Equal("notes", File.ReadAllText(Local("Copy/Nested/brief notes.txt")));
        Apply("Copy", "delete");
        Assert.False(Directory.Exists(Local("Copy")));
        Assert.True(File.Exists(Local("Docs/Brief.txt")));
    }

    [Theory]
    [InlineData("", "delete", null, 400)]
    [InlineData("Docs", "unknown", null, 400)]
    [InlineData("Docs", "copy", null, 400)]
    [InlineData("Docs", "copy", "Docs/Nested/Copy", 400)]
    [InlineData("Docs", "move", "Docs/Nested/Moved", 400)]
    [InlineData("Docs", "move", "../outside", 400)]
    [InlineData("Docs", "copy", "Outside scope.txt", 409)]
    public void InvalidMutationPreservesSource(string path, string operation, string? destination, int status)
    {
        Assert.Equal(status, Assert.Throws<BrowserException>(() => Apply(path, operation, destination)).Status);
        Assert.Equal("brief", File.ReadAllText(Local("Docs/Brief.txt")));
    }

    [Fact]
    public void CanceledCopyLeavesNoStagingOrDestination()
    {
        foreach (var source in new[] { "Docs", "Docs/Brief.txt" })
        {
            Assert.ThrowsAny<OperationCanceledException>(() =>
                browser.Apply(new ActionRequest(source, "copy", "Canceled"), new CancellationToken(true)));
            Assert.False(Path.Exists(Local("Canceled")));
            Assert.Empty(Directory.GetFileSystemEntries(root, ".upload-*"));
        }
    }

    private string Local(string path) => Path.Combine(root, path);
    private void Apply(string path, string operation, string? destination = null) =>
        browser.Apply(new ActionRequest(path, operation, destination), default);

    public void Dispose() => workspace.Delete(recursive: true);

    private sealed class ControlledFormFile(Func<Stream, CancellationToken, Task> copy)
        : FormFile(Stream.Null, 0, 1, "file", "controlled.bin"), IFormFile
    {
        Task IFormFile.CopyToAsync(Stream target, CancellationToken cancellationToken) =>
            copy(target, cancellationToken);
    }
}
