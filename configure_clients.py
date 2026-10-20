"""Preview/apply only the purewriter MCP entry; preserve unrelated settings."""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent


def plan_configs(home, *, client='codex', python=None, data_dir=None,
                 exe_path=None, book='', novel_txt=''):
    home = Path(home)
    python = Path(python) if python else ROOT / '.venv/Scripts/python.exe'
    if not python.is_file():
        raise RuntimeError('请先创建 .venv 并安装 requirements.txt，或用 --python 指定解释器。')
    env = dict(PYTHONIOENCODING='utf-8',
               PUREWRITER_DATA_DIR=str(Path(data_dir) if data_dir else home/'Documents/PureWriter/App'),
               PUREWRITER_EXE_PATH=str(Path(exe_path) if exe_path else home/'AppData/Local/Pure Writer 2/Pure Writer 2.exe'))
    if book: env['DEFAULT_BOOK_NAME'] = book
    if novel_txt: env['NOVEL_TXT_PATH'] = str(Path(novel_txt).resolve())
    settings = dict(command=str(python.resolve()), args=['-B','-X','utf8',str(ROOT/'purewriter_mcp_server.py')], env=env)
    plans = []
    if client in ('antigravity','both'):
        path = home/'.gemini/config/mcp_config.json'
        old = path.read_text(encoding='utf-8-sig') if path.exists() else '{}'
        obj = json.loads(old)
        updated = copy.deepcopy(obj)
        updated.setdefault('mcpServers',{})['purewriter'] = settings
        plans.append((path,old,json.dumps(updated,ensure_ascii=False,indent=2)+'\n'))
    if client in ('codex','both'):
        path = home/'.codex/config.toml'
        old = path.read_text(encoding='utf-8-sig') if path.exists() else ''
        obj = tomllib.loads(old)
        remaining = re.sub(r'(?ms)^\[mcp_servers\.purewriter(?:\.[^\]]+)?\][ \t]*\r?\n.*?(?=^\[|\Z)','',old)
        quote = lambda value: json.dumps(value,ensure_ascii=False)
        lines = ['[mcp_servers.purewriter]', 'command = '+quote(settings['command']),
                 'args = '+quote(settings['args']), 'startup_timeout_sec = 30',
                 'tool_timeout_sec = 120', '[mcp_servers.purewriter.env]']
        lines += [key+' = '+quote(value) for key,value in settings['env'].items()]
        new = remaining.rstrip()+'\n\n'+'\n'.join(lines)+'\n'
        expected = copy.deepcopy(obj)
        expected.setdefault('mcp_servers',{})['purewriter'] = dict(settings,startup_timeout_sec=30,tool_timeout_sec=120)
        if tomllib.loads(new) != expected:
            raise RuntimeError('TOML 无法只更新 purewriter，未写任何配置。')
        plans.append((path,old,new))
    return plans,settings


def apply_plans(plans):
    for path,old,new in plans:
        current = path.read_text(encoding='utf-8-sig') if path.exists() else ('{}' if path.suffix=='.json' else '')
        if current != old: raise RuntimeError('配置已变化，请重新预览。')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    changed = []
    for path,old,new in plans:
        if old == new: continue
        path.parent.mkdir(parents=True,exist_ok=True)
        backup = None
        if path.exists():
            backup = path.with_name(path.name+'.before-purewriter-'+stamp+'.bak')
            shutil.copy2(path,backup)
        fd,tmp = tempfile.mkstemp(dir=path.parent,prefix='.purewriter-config-')
        try:
            with os.fdopen(fd,'w',encoding='utf-8',newline='') as file:
                file.write(new); file.flush(); os.fsync(file.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        if path.read_text(encoding='utf-8') != new: raise RuntimeError('配置读回不一致。')
        changed.append(dict(path=str(path),backup=str(backup) if backup else None))
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client',choices=['codex','antigravity','both'],default='codex')
    parser.add_argument('--python',help='已安装依赖的 Python 解释器')
    parser.add_argument('--data-dir',help='包含 Room.db 与 History.db 的目录')
    parser.add_argument('--exe-path',help='Pure Writer 2.exe 的完整路径')
    parser.add_argument('--book',default='',help='默认书籍名称或 ID；省略时每次调用显式指定 book')
    parser.add_argument('--novel-txt',default='',help='可选的本地 TXT，仅作为比较默认值')
    parser.add_argument('--apply',action='store_true',help='实际更新配置；默认仅预览')
    args = parser.parse_args()
    plans,settings = plan_configs(Path.home(),client=args.client,python=args.python,data_dir=args.data_dir,
                                 exe_path=args.exe_path,book=args.book,novel_txt=args.novel_txt)
    if not args.apply:
        result = dict(mode='preview',files=[str(p[0]) for p in plans],purewriter=settings)
    else:
        result = dict(ok=True,changed=apply_plans(plans),purewriter=settings)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
