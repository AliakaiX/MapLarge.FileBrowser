using Microsoft.AspNetCore.Http.Features;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Server.Kestrel.Core;

const long maxUploadBytes = 10 * 1024 * 1024;
var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls(builder.Configuration["urls"] ?? "http://127.0.0.1:5080");
builder.Services.Configure<KestrelServerOptions>(options =>
    options.Limits.MaxRequestBodySize = maxUploadBytes + 64 * 1024);
builder.Services.Configure<FormOptions>(options =>
    options.MultipartBodyLengthLimit = maxUploadBytes + 64 * 1024);
builder.Services.AddProblemDetails();
var origins = builder.Configuration.GetSection("Browser:AllowedOrigins").Get<string[]>() ?? [];
builder.Services.AddCors(options => options.AddDefaultPolicy(policy =>
{
    policy.WithOrigins(origins).WithMethods("GET", "POST").AllowAnyHeader()
        .WithExposedHeaders("Content-Disposition");
}));

var files = new FileBrowser(builder.Environment.ContentRootPath,
    builder.Configuration["Browser:Root"] ?? "sample-data");
var app = builder.Build();
app.UseExceptionHandler();
app.UseStatusCodePages();
app.UseCors();
app.UseDefaultFiles();
app.UseStaticFiles();
app.Use(async (context, next) =>
{
    var request = context.Request;
    if (HttpMethods.IsPost(request.Method) &&
        request.Headers.TryGetValue("Origin", out var origin) &&
        !origins.Contains(origin.ToString(), StringComparer.OrdinalIgnoreCase) &&
        !string.Equals(origin, $"{request.Scheme}://{request.Host}", StringComparison.OrdinalIgnoreCase))
    {
        await Results.Problem(statusCode: 403, title: "This browser origin cannot modify files.")
            .ExecuteAsync(context);
        return;
    }
    await next(context);
});
app.Use(async (context, next) =>
{
    try
    {
        await next(context);
    }
    catch (BrowserException exception) when (!context.Response.HasStarted)
    {
        await Results.Problem(statusCode: exception.Status, title: exception.Message)
            .ExecuteAsync(context);
    }
    catch (IOException exception) when (!context.Response.HasStarted &&
        exception is FileNotFoundException or DirectoryNotFoundException)
    {
        await Results.Problem(statusCode: 404, title: "The entry no longer exists.")
            .ExecuteAsync(context);
    }
    catch (UnauthorizedAccessException) when (!context.Response.HasStarted)
    {
        await Results.Problem(statusCode: 403, title: "The filesystem denied access.")
            .ExecuteAsync(context);
    }
});

// A deployed service would attach RequireAuthorization() and enforce per-root access here.
var api = app.MapGroup("/api");
api.MapGet("/browse", (string? path, bool? folderSizes, CancellationToken ct) =>
    files.List(path, null, ct, folderSizes == true));
api.MapGet("/search", (string? path, string? q, CancellationToken ct) =>
    string.IsNullOrWhiteSpace(q) || q.Length > 200
        ? Results.Problem(statusCode: 400, title: "Provide a search term of 1 to 200 characters.")
        : Results.Ok(files.List(path, q.Trim(), ct)));
api.MapGet("/download", (string path) =>
    Results.File(files.OpenDownload(path), "application/octet-stream",
        fileDownloadName: Path.GetFileName(path), enableRangeProcessing: true));
api.MapPost("/upload", async (string? path, [FromForm] IFormFile file, CancellationToken ct) =>
{
    if (file.Length > maxUploadBytes)
        return Results.Problem(statusCode: 413, title: "Files are limited to 10 MiB.");
    var entry = await files.Upload(path, file, ct);
    var location = $"/api/download?path={Uri.EscapeDataString(entry.Path)}";
    return Results.Created(location, entry);
}).DisableAntiforgery(); // Anonymous loopback demo. Revisit CSRF protection if adding cookie authentication.
api.MapPost("/folders", (FolderRequest request) =>
    Results.Ok(files.CreateFolder(request.Path, request.Name)));
api.MapPost("/actions", (ActionRequest request, CancellationToken ct) =>
{
    files.Apply(request, ct);
    return Results.NoContent();
});
app.Run();

internal sealed record FolderRequest(string? Path, string Name);
internal sealed record ActionRequest(string Path, string Operation, string? Destination);
