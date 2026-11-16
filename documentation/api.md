# File browser API contract

| Method and route | Input | Success |
| --- | --- | --- |
| `GET /api/browse` | Optional query parameters `path` and `folderSizes=true` | 200 with immediate children and totals |
| `GET /api/search` | Optional `path` and required `q` | 200 with recursive name matches and totals |
| `GET /api/download` | Required query parameter `path` for a file | 200 with attachment bytes or 206 for a satisfiable range |
| `POST /api/upload` | Optional destination folder `path` in the query string and one multipart field named `file` | 201 with the new entry and a download URL in `Location` |
| `POST /api/folders` | JSON with parent `path` and folder `name` | 200 with the new folder entry |
| `POST /api/actions` | JSON with entry `path`, `operation` (`move`, `copy`, or `delete`), and `destination` for move/copy | 204 |


An omitted or empty folder path selects the root. Entry paths are relative to that root and use slash separators. Use `URLSearchParams` to encode path and query values. For deep links, a frontend can store `path` and `q` in its own query string and forward them to the matching endpoint. Clear `q` to return to browse. Build navigation links from each entry's returned `path`.

```json
{
  "path": "Docs",
  "entries": [
    {
      "name": "hello.txt",
      "path": "Docs/hello.txt",
      "kind": "file",
      "sizeBytes": 5,
      "modifiedUtc": "2026-11-16T15:00:00Z"
    }
  ],
  "totals": { "fileCount": 1, "folderCount": 0, "fileBytes": 5 }
}
```

Search trims the term and matches file and folder names using case-insensitive substring comparison. Results put folders first and sort by relative path. Totals apply to the entries in the response. Folder entries have a null `sizeBytes` by default. The dashboard requests recursive folder sizes in browse mode with `folderSizes=true` and includes them in its visible total. The API's `totals.fileBytes` always sums visible files only. Search walks the selected subtree on demand and materializes all matches. It leaves folder sizes null so the dashboard's search totals count each matched file once. This MVP assumes small demo trees.

Uploads accept up to 10 MiB per file. A same-directory temporary file keeps incomplete uploads out of browse and search. The API returns 409 for an existing target. For move and copy, supply the complete destination path including the new entry name. Rename is a move within the same folder. The destination parent must already exist. Delete removes folders recursively. Copy skips symbolic links and temporary files.

Error status codes include 400 for invalid input, 403 for refused access or upload origin, 404 for missing or incompatible entries, 409 for upload conflicts, and 413 for size limits. When available, the error body is ASP.NET Core Problem Details JSON. The browser can fall back to the status when the body is empty.

```sh
curl --get http://127.0.0.1:5080/api/search --data-urlencode 'q=welcome'
curl -F 'file=@/tmp/demo.txt' http://127.0.0.1:5080/api/upload
curl --get http://127.0.0.1:5080/api/download --data-urlencode 'path=demo.txt' -o demo.txt
```

Cross-origin frontend development requires configuring the exact frontend origin. Use browser `FormData` for uploads and let the browser supply the multipart boundary.
