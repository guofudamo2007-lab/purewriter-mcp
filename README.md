# Pure Writer MCP

让 AI 的分散修改，准确落回 Pure Writer 2。

A Windows MCP server for reading and editing Pure Writer 2 manuscripts, with revision checks, exact text patches, backups, native history, and undo. 中文说明见下文；[English quick start](README.en.md).

适合小说的文风、对白、叙述和多章零散修改：助手先读上下文，再把多处修改一次提交，保留未改部分的缩进和换行。也支持新建章节、续写、整章更新、分卷和 TXT 导出。

这是独立的社区项目，与 Pure Writer 官方无隶属关系。适配使用 Windows 客户端的本地 SQLite 数据库，**不是官方实时编辑接口**。目前推荐 Python 3.14，使用 MCP Python SDK 2.3.0，通过 stdio 接入支持 MCP 的助手。

## 安装

在 Windows PowerShell 中，进入下载或克隆得到的项目目录：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

数据库目录须同时包含 `Room.db` 和 `History.db`。默认查找当前用户的 `Documents/PureWriter/App`；如果文档文件夹被重定向到 OneDrive 或其他位置，请显式设置路径。

### 选择 AI 客户端

现有 Codex / Antigravity 接入已扩展为 13 种配置方案：Claude Desktop、Claude Code、Cursor / Cursor CLI、Gemini CLI、VS Code / Copilot、Copilot CLI、Cline、Roo Code、OpenCode、Windsurf 和 Devin Desktop。完整路径、格式及接入命令见 [客户端适配指南](CLIENTS.md)。

配置助手默认只预览，且默认仅处理 Codex：

```powershell
.\.venv\Scripts\python.exe configure_clients.py --client codex
.\.venv\Scripts\python.exe configure_clients.py --client codex --apply
```

`--client antigravity` 仅处理 Antigravity，`--client both` 处理两者。脚本仅更新 `purewriter` 条目，在原配置旁保存备份并保留其他设置。

按需要指定路径和默认书籍，同一组参数先预览，再加 `--apply`：

```powershell
.\.venv\Scripts\python.exe configure_clients.py --client codex --data-dir 'C:\Path\To\PureWriter\App' --book '我的小说'
```

重载 MCP 或重启助手后，调用 `pw_get_status`。当前版本应为 `2.1.0`，共 21 个工具。书名可以不预设：先用 `pw_list_books` 找到书籍 ID，再在工具参数中明确指定 `book`。

### 通用配置与环境变量

可用 `--list-clients` 查看适配器，`--print-config` 输出所选客户端格式的片段；`--project-dir` 指定项目，`--config-path` 指定实际配置文件，`--client` 可重复选择多个客户端。脚本保留原有权限设置和 JSONC 的其他条目及注释。

将 [JSON 配置示例](examples/mcp-config.json) 中的绝对路径改为自己的路径，放进客户端的 MCP 配置。`command` 必须指向已安装依赖的 Python，`args` 指向本项目的 `purewriter_mcp_server.py`。

| 环境变量 | 默认值 / 用途 |
| --- | --- |
| `PUREWRITER_DATA_DIR` | 当前用户 `Documents/PureWriter/App` |
| `PUREWRITER_EXE_PATH` | 当前用户 `AppData/Local/Pure Writer 2/Pure Writer 2.exe` |
| `DEFAULT_BOOK_NAME` | 空；每次明确传入书籍名称或 ID |
| `NOVEL_TXT_PATH` | 空；仅供 TXT 比较，不自动同步 |
| `PUREWRITER_EXPORT_DIR` | 当前用户 `Documents/PureWriterExports` |

## 使用

各类 agent 的参数约定、失败重试和完整改稿规则见 [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md)。

可以这样请求助手：

> 读取《我的小说》第一章，把这几处直接说明情绪的句子改成动作和对白，保留人物设定、事实和因果。先检查差异，再通过 Pure Writer MCP 落实，给我修改摘要。

> 本地稿有更新。对齐书籍和章节，核对正文差异后更新到 Pure Writer；遇到同名章节用 ID 区分，新增或拆章时先说明结构变化。

正式提交前，请在 Pure Writer 中保存并退出。读取和预览可在客户端运行时进行，但未保存的编辑可能尚未出现在数据库中。提交过程中也应保持客户端关闭；服务不会强关或自动重启应用。

推荐工具流程：

1. 用 `pw_list_books` / `pw_get_book_outline` 确定书籍和章节 ID。
2. 用 `pw_read_chapter` / `pw_read_chapters` 读取上下文和 `revision`。
3. 用 `pw_batch_patch(dry_run=true)` 预览。同章的所有修改放在同一个 `edits` 数组中。
4. 校验通过后，使用相同修改参数、`dry_run=false` 和唯一 `request_id` 提交。
5. 保存返回的 `operation_id`，再次读取正文核对；需要撤销时用 `pw_undo_operation`。

预览示例（替换占位 ID、版本和原文）：

```json
{
  "book": "书籍ID",
  "changes": [{
    "chapter_id": "章节ID",
    "expected_revision": "读取章节返回的revision",
    "edits": [
      {"old_text": "　　门口很冷。", "new_text": "　　他把手缩进袖口。"},
      {"old_text": "　　他笑了。", "new_text": "　　他扯了一下嘴角。"}
    ]
  }],
  "label": "第一章：动作与对白调整",
  "dry_run": true,
  "request_id": "为本轮生成一个UUID"
}
```

每条 `old_text` 都匹配修改前的正文，不能依赖上一条刚生成的文字。原文缺失、匹配多处、范围重叠、跨书籍 ID 或版本冲突都会拒绝整轮提交。需要全局替换时才显式使用 `replace_all=true`。单次最多 100 章、每章 500 处修改。

网络或进程响应丢失后，重试相同操作应复用 `request_id`；不同内容使用新 ID。`replayed=true` 表示返回历史操作收据，并不表示当前稿件仍停留在那个版本。出现版本冲突必须重读核对，不能只替换版本号强行写入。

## 工具一览

| 用途 | 工具 |
| --- | --- |
| 状态与阅读 | `pw_get_status`、`pw_list_books`、`pw_get_book_outline`、`pw_read_chapter`、`pw_read_chapters`、`pw_search` |
| 分散修改 | `pw_batch_patch`、`pw_patch_chapter` |
| 章节与分卷 | `pw_create_chapter`、`pw_update_chapter`、`pw_append_chapter`、`pw_create_category`、`pw_delete_chapter` |
| 历史与回退 | `pw_list_history`、`pw_restore_history`、`pw_list_operations`、`pw_get_operation`、`pw_undo_operation` |
| TXT 与客户端 | `pw_diff_with_txt`、`pw_export_book_txt`、`pw_open_app` |

正文写入默认仅预览。现有章节修改要求 `expected_revision`，实际数据库提交要求 `request_id`。删除使用软删除。

`pw_diff_with_txt` 只检查忽略空白后的正文包含关系，**不能判断哪份更新，也不能发现 TXT 独有章节**。本地稿更新需要助手检查章节映射和实际差异，没有自动双向同步。TXT 导出默认另存，覆盖现有文件须提供其当前 SHA-256。

## 备份与恢复

- 每轮提交前，通过 SQLite backup API 创建 `Room.db` 和 `History.db` 快照，并检查压缩包 CRC。
- 备份位于 `<数据目录>/Backups/MCP_Backups/<operation_id>/databases.zip`。
- 原生时光机保存修改前后内容；本服务的操作日志位于 `<数据目录>/MCP/operations.db`。
- 跨进程文件锁串行化 MCP 写入；SQLite 写锁内再次校验版本，事务同时提交正文、历史与操作记录，并核对写入结果。
- 仅支持 rollback journal 模式。遇到 WAL / MEMORY / OFF 会拒绝写入，不修改应用的 journal 设置。
- `pw_undo_operation` 只回退对应操作涉及的记录，发现后续编辑就停止；不会用整库覆盖的方式撤销一次润色。
- 服务不会修改原生 `Buffer.txt` 或标签缓存。客户端刷新、移动端和云同步需独立确认；数据库读回成功不能替代这些检查。

备份和日志含有完整私有稿件，请像原稿一样保管。MCP 服务本身没有网络上传代码，但接入的 AI 客户端会收到工具返回的正文，应按所选客户端的数据处理规则使用。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v

# 启用真实 stdio 子进程测试，仅写临时合成数据库。
# 需 Windows 进程查询权限，并先退出 Pure Writer。
$env:PW_RUN_PROTOCOL_TEST='1'
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
```

测试全部使用程序生成的示例数据。Windows CI 运行完整测试，包括多章原子写入、冲突拒绝、重试幂等、历史回退、备份和配置保留。贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)。许可证适用于本仓库代码和文档，不包含 Pure Writer 软件本身，也不涵盖使用者的小说或其他稿件。
