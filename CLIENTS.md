# AI agent / MCP 客户端接入

本项目在 Windows 本机运行，通过 stdio 接入 AI 客户端。适配器负责配置格式与启动参数；小说操作仍由同一套版本校验、备份和补丁引擎处理。当前提供 **13 种配置方案**。

## 选择客户端

`~` 表示 Windows 用户目录。以下路径依据所链接的官方说明核对于 2026-10-20；客户端自定义目录或安装方式可能不同，可用 `--config-path` 指向其设置界面实际打开的文件。

| `--client` | 客户端 | 默认配置 / 项目配置 | 格式 |
| --- | --- | --- | --- |
| `codex` | Codex | `~/.codex/config.toml`，尊重 `CODEX_HOME` | TOML `mcp_servers` |
| `antigravity` | [Antigravity](https://antigravity.google/docs/mcp) | `~/.gemini/config/mcp_config.json`；项目 `.agents/mcp_config.json` | `mcpServers` |
| `claude-desktop` | [Claude Desktop](https://modelcontextprotocol.io/docs/develop/connect-local-servers) | `%APPDATA%/Claude/claude_desktop_config.json` | `mcpServers` |
| `claude-code` | [Claude Code](https://code.claude.com/docs/en/mcp) | 必须指定项目：`.mcp.json` | `mcpServers` + `type: stdio` |
| `cursor` | [Cursor](https://docs.cursor.com/context/model-context-protocol) / [Cursor CLI](https://prod.cursor.com/docs/cli/mcp) | `~/.cursor/mcp.json`；项目 `.cursor/mcp.json` | `mcpServers` |
| `gemini-cli` | [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/) | `~/.gemini/settings.json`；项目 `.gemini/settings.json` | `mcpServers` |
| `vscode` | [VS Code / GitHub Copilot](https://code.visualstudio.com/docs/agents/reference/mcp-configuration) | 必须指定项目：`.vscode/mcp.json` | `servers` + `type: stdio` |
| `copilot-cli` | [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers) | `~/.copilot/mcp-config.json`；项目 `.mcp.json`；尊重 `COPILOT_HOME` | `mcpServers` + `type: stdio` |
| `cline` | [Cline](https://docs.cline.bot/mcp/mcp-overview) | CLI：`~/.cline/data/settings/cline_mcp_settings.json`；尊重 `CLINE_MCP_SETTINGS_PATH`；IDE 用 `--config-path` | `mcpServers` |
| `roo-code` | [Roo Code](https://roocodeinc.github.io/Roo-Code/features/mcp/using-mcp-in-roo/) | 必须指定项目：`.roo/mcp.json` | `mcpServers` |
| `opencode` | [OpenCode](https://opencode.ai/docs/mcp-servers/) | `~/.config/opencode/opencode.json`；项目 `opencode.json`；尊重 `OPENCODE_CONFIG` | `mcp` + `type: local` + 数组 `command` + `environment` |
| `windsurf` | Windsurf | 从 MCP 设置打开实际文件，必须传 `--config-path` | `mcpServers` |
| `devin` | [Devin Desktop / Cascade](https://docs.devin.ai/desktop/cascade/mcp) | `%APPDATA%/devin/mcp_config.json` | `mcpServers` |

Windsurf 的官方 MCP 文档当前跳转到 Devin Desktop，所述默认目录已变化，因此不猜测旧安装的配置位置。保留独立的 `windsurf` 配置片段和自定义文件适配。

Claude Desktop 某些安装方式使用不同目录，以 Settings → Developer → Edit Config 打开的文件为准。Cline IDE 中用 MCP Servers → Configure → Configure MCP Servers 找到文件。Roo 全局配置、VS Code 用户配置及其他客户端自定义配置也可通过 `--config-path` 更新。

OpenCode 自动识别默认位置已有的 `.jsonc`；如果 `.json` 和 `.jsonc` 同时存在，需要明确指定文件。Claude Code 与 Copilot CLI 的项目 `.mcp.json` 格式相容，同时指定时只更新一次。

## 常用命令

先按 [安装说明](README.md#安装) 建立 Python 虚拟环境。在仓库目录执行：

```powershell
# 查看支持列表；不读取客户端配置
.\.venv\Scripts\python.exe configure_clients.py --list-clients

# 全局配置：默认仅预览，加入 --apply 才写入
.\.venv\Scripts\python.exe configure_clients.py --client claude-desktop
.\.venv\Scripts\python.exe configure_clients.py --client claude-desktop --apply

# 项目配置：传入已有的小说工作目录
.\.venv\Scripts\python.exe configure_clients.py --client claude-code --project-dir 'C:\Writing\MyNovel' --apply
.\.venv\Scripts\python.exe configure_clients.py --client vscode --project-dir 'C:\Writing\MyNovel' --apply

# 可一次选择多个使用全局配置的客户端
.\.venv\Scripts\python.exe configure_clients.py --client cursor --client gemini-cli --client opencode --apply

# 指向客户端设置界面实际打开的文件
.\.venv\Scripts\python.exe configure_clients.py --client cline --config-path 'C:\Path\To\cline_mcp_settings.json' --apply

# 只输出目标客户端格式的配置片段，不读取或改动配置
.\.venv\Scripts\python.exe configure_clients.py --client opencode --print-config
```

所有方案支持 `--python`、`--data-dir`、`--exe-path`、`--book` 和 `--novel-txt`。启动路径全部为绝对路径，参数按数组传递，支持中文和空格路径，不依赖 agent 当前工作目录。解释器必须已安装本项目依赖。

默认仍只处理 Codex。`--client both` 继续表示 Antigravity + Codex，不表示全部客户端。`--config-path` 只能配合单个客户端，且不能与 `--project-dir` 同时使用。项目配置只会写入明确指定的项目，不自动修改任何 agent 规则文件。

## 配置保留与升级

- 仅更新 `purewriter` 的启动配置；保留其他服务器和客户端选项。
- JSON/JSONC 的其他条目、注释、换行和 BOM 保持原样；`purewriter` 对象内部可重新排版。Codex TOML 保留其他设置，遇到无法可靠编辑的布局会停止，提供手动片段入口。
- 保留已有 `disabled` / `enabled`、工具允许列表、自动批准、信任和超时设置，不自动扩大权限。已禁用的服务不会因更新而重新启用。
- 保留用户额外环境变量；本工具管理的路径会更新。省略 `--book` / `--novel-txt` 会清除原条目中对应的默认值。
- 写入前保存带时间戳的原配置备份，预检变化并逐文件原子替换、读回校验。多文件应用不是一个跨文件事务；遇到中途 I/O 错误，已完成的文件和备份仍会保留。
- 配置语法错误、重复键、类型错误或同名远程服务冲突会停止，不静默覆盖。预览不打印原配置中的其他环境变量或凭据。

## 连接后怎么验证

1. 在客户端重载 MCP 或重启。项目首次连接可能需要按客户端流程确认项目可信。
2. 查看 `purewriter` 已连接，工具列表应有 21 项；工具名可能被客户端加前缀。
3. 调用 `pw_get_status`，应返回版本 `2.1.0`。检查数据路径、应用运行状态和写入条件。
4. 调用 `pw_list_books`，明确书籍和章节 ID，再用 `pw_read_chapter` 读回正文与 revision。
5. 先做一次授权范围内的 `dry_run=true` 预览，再按 [agent 改稿指引](AGENT_WORKFLOW.md) 提交。

自动化覆盖 13 个适配器的配置格式、保留与重复执行，并通过 MCP SDK 启动生成配置中的两种命令结构，验证中文路径、不同工作目录、工具发现、读取以及文本/结构化返回一致性。原有真实 stdio 用例继续验证预览、提交、冲突和撤销。**这不等于已逐一安装、登录并人工验收上述客户端 UI。**

服务目前仅支持 Windows 本地数据库。云端 agent、浏览器网页聊天，以及运行在 WSL/容器/远程机器上的进程，不能直接把这份 Windows 本地 stdio 配置当作远程连接使用。客户端未连接时，先核对其实际执行环境和配置日志。

## English quick reference

Use `--client` with an ID from the table. Configuration is previewed by default; `--apply` performs the update with a backup. Pass an existing `--project-dir` for Claude Code, VS Code, or Roo Code, or use `--config-path` for a specific file. Cline defaults to its CLI configuration; choose the file opened by the IDE for its extension. Windsurf requires an explicit path because current official documentation points to Devin Desktop.

`--print-config` emits a client-specific snippet without reading or writing user configuration. JSONC comments outside the edited server are preserved. Existing approval, trust, disabled and timeout settings remain in place. Extra environment variables remain; omitted book/TXT defaults are removed. No real client configuration is changed by the test suite.

These are Windows-local stdio adapters, with configuration and SDK protocol verification rather than individual UI certification. After reloading the client, call `pw_get_status`, list books, and read a chapter. Follow [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) for guarded manuscript edits.
