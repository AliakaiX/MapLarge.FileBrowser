internal sealed class FileBrowser
{
    private const string PendingPrefix = ".upload-";
    private readonly string root;
    private readonly object mutationLock = new();
    private readonly HashSet<string> activeUploads = new(
        OperatingSystem.IsWindows() ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal);

    public FileBrowser(string contentRoot, string configuredRoot)
    {
        root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(configuredRoot, contentRoot));
        var directory = new DirectoryInfo(root);
        if (!directory.Exists)
            throw new InvalidOperationException("Browser:Root must point to an existing directory.");

        for (DirectoryInfo? ancestor = directory; ancestor is not null; ancestor = ancestor.Parent)
        {
            if (IsLink(ancestor.Attributes))
                throw new InvalidOperationException("Browser:Root and its ancestors must be regular directories.");
        }
    }

    public BrowserView List(string? path, string? query, CancellationToken ct, bool folderSizes = false)
    {
        var directory = Resolve(path, directory: true);
        var entries = new List<BrowserEntry>();
        var pending = new Stack<string>();
        pending.Push(directory);

        while (pending.TryPop(out var current))
        {
            foreach (var item in new DirectoryInfo(current).EnumerateFileSystemInfos())
            {
                ct.ThrowIfCancellationRequested();
                var attributes = item.Attributes;
                if (IsLink(attributes) || !IsValidSegment(item.Name))
                    continue;

                bool isDirectory = attributes.HasFlag(FileAttributes.Directory);
                if (query is null || item.Name.Contains(query, StringComparison.OrdinalIgnoreCase))
                {
                    var entry = Describe(item, isDirectory);
                    if (folderSizes && isDirectory)
                        entry = entry with { SizeBytes = GetFolderBytes(item.FullName, ct) };
                    entries.Add(entry);
                }
                if (query is not null && isDirectory)
                    pending.Push(item.FullName);
            }
        }

        var ordered = entries.OrderBy(entry => entry.Kind == "file")
            .ThenBy(entry => entry.Path, StringComparer.OrdinalIgnoreCase)
            .ThenBy(entry => entry.Path, StringComparer.Ordinal)
            .ToArray();
        var fileCount = ordered.Count(entry => entry.Kind == "file");
        return new BrowserView(path ?? "", ordered,
            new ViewTotals(fileCount, ordered.Length - fileCount,
                ordered.Where(entry => entry.Kind == "file").Sum(entry => entry.SizeBytes ?? 0)));
    }

    private static long GetFolderBytes(string path, CancellationToken ct)
    {
        var pending = new Stack<DirectoryInfo>();
        pending.Push(new DirectoryInfo(path));
        long bytes = 0;
        while (pending.TryPop(out var folder))
        {
            foreach (var item in folder.EnumerateFileSystemInfos())
            {
                ct.ThrowIfCancellationRequested();
                if (IsLink(item.Attributes) || !IsValidSegment(item.Name))
                    continue;
                if (item is DirectoryInfo child)
                    pending.Push(child);
                else if (item is FileInfo file)
                    bytes += file.Length;
            }
        }
        return bytes;
    }

    public FileStream OpenDownload(string path) =>
        new(Resolve(path, directory: false), FileMode.Open, FileAccess.Read, FileShare.Read,
            bufferSize: 64 * 1024, FileOptions.Asynchronous | FileOptions.SequentialScan);

    public async Task<BrowserEntry> Upload(string? path, IFormFile file, CancellationToken ct)
    {
        if (!IsValidSegment(file.FileName))
            throw new BrowserException(400, "Provide a valid filename without directory components.");

        string target;
        string pending;
        FileStream output;
        lock (mutationLock)
        {
            var directory = Resolve(path, directory: true);
            target = Path.Combine(directory, file.FileName);
            EnsureAvailable(target);
            pending = Path.Combine(directory, PendingPrefix + Guid.NewGuid().ToString("N"));
            output = new FileStream(pending, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                bufferSize: 64 * 1024, FileOptions.Asynchronous);
            activeUploads.Add(pending);
        }
        try
        {
            await using (output)
                await file.CopyToAsync(output, ct);

            lock (mutationLock)
            {
                ct.ThrowIfCancellationRequested();
                if (Path.Exists(target))
                    throw new BrowserException(409, "An entry with that filename already exists.");

                File.Move(pending, target, overwrite: false);
                return Describe(new FileInfo(target), isDirectory: false);
            }
        }
        finally
        {
            lock (mutationLock)
            {
                activeUploads.Remove(pending);
                File.Delete(pending);
            }
        }
    }

    public BrowserEntry CreateFolder(string? path, string name)
    {
        if (name is null || !IsValidSegment(name))
            throw new BrowserException(400, "Provide a valid folder name.");

        lock (mutationLock)
        {
            var target = Path.Combine(Resolve(path, directory: true), name);
            EnsureAvailable(target);
            return Describe(Directory.CreateDirectory(target), isDirectory: true);
        }
    }

    public void Apply(ActionRequest request, CancellationToken ct)
    {
        if (string.IsNullOrEmpty(request.Path))
            throw new BrowserException(400, "Select an entry below the root.");

        lock (mutationLock)
        {
            var source = Resolve(request.Path, directory: null);
            bool isDirectory = Directory.Exists(source);
            var comparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            if (isDirectory && request.Operation is "move" or "delete" &&
                activeUploads.Any(path => path.StartsWith(source + Path.DirectorySeparatorChar, comparison)))
                throw new BrowserException(409, "Wait for uploads to finish before changing this folder.");

            if (request.Operation == "delete")
            {
                if (isDirectory)
                    Directory.Delete(source, recursive: true);
                else
                    File.Delete(source);
                return;
            }

            if (request.Operation is not ("move" or "copy") ||
                string.IsNullOrEmpty(request.Destination))
                throw new BrowserException(400, "Provide a supported action and destination path.");

            var segments = request.Destination.Split('/');
            if (segments.Any(segment => !IsValidSegment(segment)))
                throw new BrowserException(400, "Provide a valid destination path.");
            var parent = Resolve(string.Join('/', segments[..^1]), directory: true);
            var target = Path.Combine(parent, segments[^1]);
            if (isDirectory && target.StartsWith(source + Path.DirectorySeparatorChar, comparison))
                throw new BrowserException(400, "A folder cannot be placed inside itself.");

            EnsureAvailable(target);
            if (request.Operation == "move")
            {
                if (isDirectory)
                    Directory.Move(source, target);
                else
                    File.Move(source, target);
                return;
            }

            var staging = Path.Combine(parent, PendingPrefix + Guid.NewGuid().ToString("N"));
            try
            {
                if (isDirectory)
                {
                    CopyDirectory(source, staging, ct);
                    Directory.Move(staging, target);
                }
                else
                {
                    File.Copy(source, staging);
                    ct.ThrowIfCancellationRequested();
                    File.Move(staging, target);
                }
            }
            finally
            {
                if (Directory.Exists(staging))
                    Directory.Delete(staging, recursive: true);
                else
                    File.Delete(staging);
            }
        }
    }

    private static void CopyDirectory(string source, string target, CancellationToken ct)
    {
        var pending = new Stack<(string Source, string Target)>();
        pending.Push((source, target));
        while (pending.TryPop(out var pair))
        {
            ct.ThrowIfCancellationRequested();
            Directory.CreateDirectory(pair.Target);
            foreach (var item in new DirectoryInfo(pair.Source).EnumerateFileSystemInfos())
            {
                ct.ThrowIfCancellationRequested();
                if (IsLink(item.Attributes) || !IsValidSegment(item.Name))
                    continue;

                var destination = Path.Combine(pair.Target, item.Name);
                if (item is DirectoryInfo)
                    pending.Push((item.FullName, destination));
                else
                    File.Copy(item.FullName, destination);
            }
        }
    }

    private static void EnsureAvailable(string target)
    {
        if (Path.Exists(target))
            throw new BrowserException(409, "An entry already exists at the destination.");
    }

    private string Resolve(string? path, bool? directory)
    {
        string[] segments = string.IsNullOrEmpty(path) ? [] : path.Split('/');
        if (segments.Any(segment => !IsValidSegment(segment)))
            throw new BrowserException(400, "Use a relative path with slash-separated names.");

        var current = root;
        for (var i = 0; i < segments.Length; i++)
        {
            current = Path.Combine(current, segments[i]);
            var attributes = File.GetAttributes(current);
            if (IsLink(attributes))
                throw new BrowserException(404, "Entry not found.");

            bool? expectDirectory = i < segments.Length - 1 ? true : directory;
            if (expectDirectory is bool expected && attributes.HasFlag(FileAttributes.Directory) != expected)
                throw new BrowserException(404, "Entry not found.");
        }

        if (segments.Length == 0 && directory != true)
            throw new BrowserException(404, "File not found.");
        return current;
    }

    private BrowserEntry Describe(FileSystemInfo item, bool isDirectory) =>
        new(item.Name, Path.GetRelativePath(root, item.FullName).Replace(Path.DirectorySeparatorChar, '/'),
            isDirectory ? "folder" : "file", isDirectory ? null : ((FileInfo)item).Length, item.LastWriteTimeUtc);

    private static bool IsLink(FileAttributes attributes) =>
        attributes.HasFlag(FileAttributes.ReparsePoint);

    private static bool IsValidSegment(string segment) =>
        !string.IsNullOrWhiteSpace(segment) && segment is not "." and not ".." &&
        !segment.EndsWith('.') && !segment.EndsWith(' ') &&
        !segment.StartsWith(PendingPrefix, StringComparison.OrdinalIgnoreCase) &&
        segment.IndexOfAny(['/', '\\', ':', '<', '>', '"', '|', '?', '*']) < 0 &&
        !segment.Any(char.IsControl);
}

internal sealed class BrowserException(int status, string message) : Exception(message)
{
    public int Status { get; } = status;
}

internal sealed record BrowserEntry(string Name, string Path, string Kind, long? SizeBytes,
    DateTime ModifiedUtc);
internal sealed record BrowserView(string Path, BrowserEntry[] Entries, ViewTotals Totals);
internal sealed record ViewTotals(int FileCount, int FolderCount, long FileBytes);
