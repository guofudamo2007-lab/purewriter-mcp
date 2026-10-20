# Pure Writer MCP

A local Windows MCP server for Pure Writer 2, built for scattered edits across paragraphs and chapters. Read context, preview exact text replacements, apply them with revision checks, and undo a recorded operation when needed.

This is an independent community project, not an official Pure Writer integration. It accesses the desktop application's local SQLite schema. Client refresh and cloud synchronization are separate from database verification.

## Quick start

Use Python 3.14 on Windows. From the repository directory:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe configure_clients.py --client codex
.\.venv\Scripts\python.exe configure_clients.py --client codex --apply
```

The helper now provides 13 client adapters: Codex, Antigravity, Claude Desktop, Claude Code, Cursor / Cursor CLI, Gemini CLI, VS Code / Copilot, Copilot CLI, Cline, Roo Code, OpenCode, Windsurf, and Devin Desktop. See [CLIENTS.md](CLIENTS.md) for paths, official references and commands.

Use `--list-clients` to discover adapters, `--print-config` for a client-specific snippet, `--project-dir` for a project configuration, or `--config-path` for a custom file. Repeat `--client` to select multiple clients; both still means Antigravity + Codex only. The helper previews by default, backs up files before applying, preserves unrelated settings and JSONC comments, and retains existing approval and disabled settings. These are Windows-local stdio adapters; the automated checks cover configuration and SDK transport, not every application's UI.

The default data directory is the current user's `Documents/PureWriter/App`. It must contain `Room.db` and `History.db`. Override it with `--data-dir` or `PUREWRITER_DATA_DIR`, especially when Documents is redirected. An optional `--book` / `DEFAULT_BOOK_NAME` sets the default book. Without one, supply an explicit `book` name or ID to each book-specific tool.

Reload the MCP connection and call `pw_get_status`. The server exposes 21 tools, version 2.1.0. MCP messages and tool guidance are primarily in Chinese.

## Editing workflow

See [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) for client-independent tool conventions and retry behavior.

1. List books and inspect the chapter outline.
2. Read chapters and keep their returned `revision` values.
3. Preview `pw_batch_patch` with `expected_revision` and exact `old_text` / `new_text` pairs.
4. Save and close Pure Writer before committing. The server never force-closes it.
5. Commit with `dry_run=false` and a unique `request_id`, then read back the result.
6. Keep the returned `operation_id` for `pw_undo_operation`.

All patches in a chapter match its original text, not previous replacements. Ambiguous or missing anchors, overlapping edits, stale revisions, and cross-book IDs fail without partially applying the batch. Reuse the request ID only when retrying the identical operation after an unknown response.

## Data and recovery

Each mutation creates a CRC-checked backup at `<data directory>/Backups/MCP_Backups/<operation_id>/databases.zip`. Native chapter history and a separate operation journal are updated in the transaction. Undo refuses to overwrite subsequent changes. WAL and unsupported journal modes block writes.

The service does not upload manuscript data itself. Your MCP/AI client receives returned text; its own data policy applies. Backups and the operation journal contain private manuscript text and must not be committed to Git.

TXT comparison checks normalized content containment only; it is not automatic bidirectional synchronization. Export defaults to `Documents/PureWriterExports` (override with `PUREWRITER_EXPORT_DIR`), with hash protection before overwriting an existing file.

See the [Chinese README](README.md) for the complete tool list, examples and limitations. Tests use synthetic temporary databases only. The full stdio test requires Windows and `PW_RUN_PROTOCOL_TEST=1`.

Released under the [MIT License](LICENSE). Pure Writer itself and users' manuscripts are not included in this license.
