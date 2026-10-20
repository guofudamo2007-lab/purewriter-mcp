"""Preview/apply Pure Writer MCP adapters without changing other servers or approvals."""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib

from config_formats import parse_jsonc, toml_snippet, update_jsonc, update_toml

ROOT = Path(__file__).resolve().parent
CLIENTS = {
    'codex': ('Codex', 'mcp_servers', '.codex/config.toml'),
    'antigravity': ('Antigravity', 'mcpServers', '.gemini/config/mcp_config.json'),
    'claude-desktop': ('Claude Desktop', 'mcpServers', 'APPDATA/Claude/claude_desktop_config.json'),
    'claude-code': ('Claude Code', 'mcpServers', '--project-dir/.mcp.json'),
    'cursor': ('Cursor / Cursor CLI', 'mcpServers', '.cursor/mcp.json'),
    'gemini-cli': ('Gemini CLI', 'mcpServers', '.gemini/settings.json'),
    'vscode': ('VS Code / GitHub Copilot', 'servers', '--project-dir/.vscode/mcp.json'),
    'copilot-cli': ('GitHub Copilot CLI', 'mcpServers', '.copilot/mcp-config.json'),
    'cline': ('Cline CLI / IDE (custom path)', 'mcpServers', '.cline/data/settings/cline_mcp_settings.json'),
    'roo-code': ('Roo Code', 'mcpServers', '--project-dir/.roo/mcp.json'),
    'opencode': ('OpenCode', 'mcp', '.config/opencode/opencode.json'),
    'windsurf': ('Windsurf (explicit config path)', 'mcpServers', '--config-path'),
    'devin': ('Devin Desktop / Cascade', 'mcpServers', 'APPDATA/devin/mcp_config.json'),
}
PROJECT_PATHS = {
    'antigravity': '.agents/mcp_config.json',
    'claude-code': '.mcp.json', 'cursor': '.cursor/mcp.json',
    'gemini-cli': '.gemini/settings.json', 'vscode': '.vscode/mcp.json',
    'copilot-cli': '.mcp.json', 'roo-code': '.roo/mcp.json', 'opencode': 'opencode.json',
}
OWNED_ENV = {'PYTHONIOENCODING', 'PUREWRITER_DATA_DIR', 'PUREWRITER_EXE_PATH',
             'DEFAULT_BOOK_NAME', 'NOVEL_TXT_PATH'}


def selected_clients(client):
    selected = []
    for name in [client] if isinstance(client, str) else client:
        for item in ('antigravity', 'codex') if name == 'both' else (name,):
            if item not in CLIENTS:
                raise ValueError('未知客户端：' + item)
            if item not in selected:
                selected.append(item)
    if not selected:
        raise ValueError('请指定至少一个客户端。')
    return selected


def build_settings(home, *, python=None, data_dir=None, exe_path=None, book='', novel_txt=''):
    home = Path(home).resolve()
    python = Path(python) if python else ROOT / '.venv/Scripts/python.exe'
    if not python.is_file():
        raise ValueError('请先创建 .venv 并安装 requirements.txt，或用 --python 指定解释器。')
    env = dict(PYTHONIOENCODING='utf-8',
               PUREWRITER_DATA_DIR=str(Path(data_dir).resolve() if data_dir else home/'Documents/PureWriter/App'),
               PUREWRITER_EXE_PATH=str(Path(exe_path).resolve() if exe_path else home/'AppData/Local/Pure Writer 2/Pure Writer 2.exe'))
    if book:
        env['DEFAULT_BOOK_NAME'] = book
    if novel_txt:
        env['NOVEL_TXT_PATH'] = str(Path(novel_txt).resolve())
    return dict(command=str(python.resolve()), args=['-B', '-X', 'utf8', str(ROOT/'purewriter_mcp_server.py')], env=env)


def client_entry(client, settings):
    entry = copy.deepcopy(settings)
    if client == 'codex':
        entry.update(startup_timeout_sec=30, tool_timeout_sec=120)
    elif client == 'opencode':
        entry = dict(type='local', command=[settings['command'], *settings['args']],
                     environment=copy.deepcopy(settings['env']), timeout=30000)
    elif client in ('claude-code', 'vscode', 'copilot-cli'):
        entry['type'] = 'stdio'
    elif client == 'gemini-cli':
        entry['timeout'] = 120000
    return entry


def config_snippet(client, settings):
    entry = client_entry(client, settings)
    if client == 'codex':
        return toml_snippet(entry)
    return json.dumps({CLIENTS[client][1]: {'purewriter': entry}}, ensure_ascii=False, indent=2) + '\n'


def config_path(home, client, *, project_dir=None, environ=None):
    env = environ or {}
    home = Path(home).resolve()
    if project_dir:
        if client not in PROJECT_PATHS:
            raise ValueError(client + ' 不支持 --project-dir，请用 --config-path 指定配置。')
        project = Path(project_dir).resolve()
        if not project.is_dir():
            raise ValueError('--project-dir 必须是已存在的项目目录。')
        path = project / PROJECT_PATHS[client]
    elif client in ('claude-code', 'vscode', 'roo-code'):
        raise ValueError(client + ' 需要 --project-dir 或 --config-path；生成片段可用 --print-config。')
    elif client == 'windsurf':
        raise ValueError('Windsurf 请从 MCP 设置打开实际配置文件，用 --config-path 指定；或用 --print-config。')
    elif client in ('claude-desktop', 'devin'):
        appdata = Path(env.get('APPDATA', home/'AppData/Roaming'))
        path = appdata / ('Claude/claude_desktop_config.json' if client == 'claude-desktop' else 'devin/mcp_config.json')
    elif client == 'codex':
        path = Path(env.get('CODEX_HOME', home/'.codex')) / 'config.toml'
    elif client == 'copilot-cli':
        path = Path(env.get('COPILOT_HOME', home/'.copilot')) / 'mcp-config.json'
    elif client == 'cline':
        path = Path(env.get('CLINE_MCP_SETTINGS_PATH', home/CLIENTS[client][2]))
    elif client == 'opencode' and env.get('OPENCODE_CONFIG'):
        return Path(env['OPENCODE_CONFIG']).resolve()
    else:
        path = home / CLIENTS[client][2]
    if client == 'opencode':
        jsonc = path.with_suffix('.jsonc')
        if path.exists() and jsonc.exists():
            raise ValueError('同时发现 OpenCode JSON 和 JSONC，请用 --config-path 明确选择。')
        if jsonc.exists():
            path = jsonc
    return path.resolve()


def _merge_entry(existing, desired, client):
    if not isinstance(existing, dict):
        raise ValueError('purewriter 配置必须是对象。')
    if any(key in existing for key in ('url', 'serverUrl', 'httpUrl')) or existing.get('type') in ('http', 'sse', 'remote', 'streamable-http'):
        raise ValueError('已有 purewriter 是远程服务器，请先手动改名或合并 --print-config 输出。')
    result = copy.deepcopy(existing)
    env_key = 'environment' if client == 'opencode' else 'env'
    environment = result.get(env_key, {})
    if not isinstance(environment, dict):
        raise ValueError('purewriter 环境变量必须是对象。')
    environment = {key: value for key, value in environment.items() if key not in OWNED_ENV}
    environment.update(desired[env_key])
    for key, value in desired.items():
        if key in ('command', 'args', 'env', 'environment', 'type'):
            result[key] = value
        else:
            result.setdefault(key, value)
    result[env_key] = environment
    return result


def _read(path):
    # Keep BOM and CRLF intact for narrow edits and exact change detection.
    if not path.exists():
        return None
    with path.open(encoding='utf-8', newline='') as file:
        return file.read()


def plan_configs(home, *, client='codex', python=None, data_dir=None,
                 exe_path=None, book='', novel_txt='', project_dir=None,
                 config_path_override=None, environ=None):
    clients = selected_clients(client)
    if config_path_override and (len(clients) != 1 or project_dir):
        raise ValueError('--config-path 只能用于单个客户端，且不能同时使用 --project-dir。')
    settings = build_settings(home, python=python, data_dir=data_dir, exe_path=exe_path, book=book, novel_txt=novel_txt)
    plans = {}
    for name in clients:
        path = Path(config_path_override).resolve() if config_path_override else config_path(home, name, project_dir=project_dir, environ=environ)
        old = _read(path)
        text = old if old is not None else ('' if name == 'codex' else '{}\n')
        obj = tomllib.loads(text.lstrip('\ufeff')) if name == 'codex' else parse_jsonc(text)[0]
        servers = obj.get(CLIENTS[name][1], {})
        if not isinstance(servers, dict):
            raise ValueError('MCP 服务器列表必须是对象。')
        entry = _merge_entry(servers.get('purewriter', {}), client_entry(name, settings), name)
        new = update_toml(text, entry) if name == 'codex' else update_jsonc(text, CLIENTS[name][1], entry)
        plan = (path, old, new)
        if path in plans and plans[path] != plan:
            raise ValueError('多个客户端将不同格式写入同一路径，已停止。')
        plans[path] = plan
    return list(plans.values()), settings


def apply_plans(plans):
    for path, old, new in plans:
        if _read(path) != old:
            raise RuntimeError('配置已变化，请重新预览。')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    changed = []
    for path, old, new in plans:
        if old == new:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if _read(path) != old:
            raise RuntimeError('配置已变化，请重新预览。')
        if path.exists():
            backup = path.with_name(path.name + '.before-purewriter-' + stamp + '.bak')
            shutil.copy2(path, backup)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.purewriter-config-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as file:
                file.write(new)
                file.flush()
                os.fsync(file.fileno())
            if _read(path) != old:
                raise RuntimeError('配置已变化，请重新预览。')
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        if _read(path) != new:
            raise RuntimeError('配置读回不一致。')
        changed.append(dict(path=str(path), backup=str(backup) if backup else None))
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', choices=[*CLIENTS, 'both'], action='append', help='可重复指定；默认 codex；both 保留旧版两客户端行为')
    parser.add_argument('--list-clients', action='store_true', help='列出适配器，不读取或修改客户端配置')
    parser.add_argument('--project-dir', help='指定项目配置所在的目录')
    parser.add_argument('--config-path', help='显式配置文件位置，仅限一个客户端')
    parser.add_argument('--python', help='已安装依赖的 Python 解释器')
    parser.add_argument('--data-dir', help='包含 Room.db 与 History.db 的目录')
    parser.add_argument('--exe-path', help='Pure Writer 2.exe 的完整路径')
    parser.add_argument('--book', default='', help='默认书籍名称或 ID；省略时每次调用显式指定 book')
    parser.add_argument('--novel-txt', default='', help='可选的本地 TXT，仅作为比较默认值')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='实际更新配置；默认仅预览')
    mode.add_argument('--print-config', action='store_true', help='输出单个客户端的配置片段；不读取或写入配置')
    args = parser.parse_args()
    try:
        if args.list_clients:
            print(json.dumps({key: dict(name=value[0], root_key=value[1], default_path=value[2]) for key, value in CLIENTS.items()}, ensure_ascii=False, indent=2))
            return
        clients = selected_clients(args.client or ['codex'])
        options = dict(python=args.python, data_dir=args.data_dir, exe_path=args.exe_path, book=args.book, novel_txt=args.novel_txt)
        if args.print_config:
            if len(clients) != 1:
                raise ValueError('--print-config 只支持单个客户端。')
            print(config_snippet(clients[0], build_settings(Path.home(), **options)), end='')
            return
        plans, settings = plan_configs(Path.home(), client=clients, project_dir=args.project_dir,
                                       config_path_override=args.config_path, environ=os.environ, **options)
        # Never print existing configs: they may contain credentials.
        result = dict(mode='apply' if args.apply else 'preview', clients=clients,
                      files=[str(p[0]) for p in plans], purewriter=settings)
        if args.apply:
            result.update(ok=True, changed=apply_plans(plans))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError) as error:
        if isinstance(error, (json.JSONDecodeError, tomllib.TOMLDecodeError)):
            parser.exit(2, '配置语法无效，未写入。请检查文件或手动合并 --print-config 输出。\n')
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
