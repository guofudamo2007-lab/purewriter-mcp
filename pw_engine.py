"""Transactional, revision-checked editing of Pure Writer's local SQLite stores.

No MCP dependency; the same engine is used by the protocol and integration tests.
Native schema is never migrated. Read calls cannot create files or change PRAGMAs.
"""
from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable


class Conflict(ValueError):
    """An edit is unsafe or its precondition no longer holds."""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def revision(row):
    return hashlib.sha256(encoded(dict(row)).encode('utf-8')).hexdigest()


def now_ms():
    return time.time_ns() // 1_000_000


def patch_text(content: str, edits: list[dict]) -> str:
    """Match every anchor against the original. Never normalize untouched text."""
    if not edits or len(edits) > 500:
        raise Conflict('每章须有 1–500 处修改。')
    spans = []
    for index, edit in enumerate(edits):
        old, new = edit.get('old_text'), edit.get('new_text')
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise Conflict(f'修改 {index + 1}: old_text 必须非空，new_text 必须是文本。')
        positions = []
        start = 0
        while True:
            found = content.find(old, start)
            if found < 0: break
            positions.append(found)
            start = found + 1  # Also detect self-overlapping anchors.
        if not positions:
            raise Conflict(f'修改 {index + 1}: 原文未精确匹配。请读回原文，保留空格和换行。')
        if len(positions) > 1 and not edit.get('replace_all', False):
            raise Conflict(f'修改 {index + 1}: 匹配 {len(positions)} 处；请增加上下文，或明确 replace_all。')
        spans.extend((pos, pos + len(old), new, index) for pos in positions)
    spans.sort(key=lambda s: (s[0], s[1]))
    for left, right in zip(spans, spans[1:]):
        if left[1] > right[0]:
            raise Conflict(f'修改 {left[3] + 1} 与 {right[3] + 1} 范围重叠，请合并。')
    for start, end, replacement, _ in reversed(spans):
        content = content[:start] + replacement + content[end:]
    return content


def updated_article(old, **values):
    result = dict(old)
    result.update(values)
    stamp = max(now_ms(), old['updateTime'] + 1)
    result['updateTime'] = stamp
    if 'content' in values:
        body = values['content']
        # Display count is advisory; native client may use a different counting rule.
        result['count'] = sum(not c.isspace() for c in body)
        summary = ' '.join(line.strip() for line in body.splitlines() if line.strip())
        result['summary'] = summary[:154] + ('…' if len(summary) > 154 else '')
    for field, timestamp in [('title','titleUpdateTime'),('categoryId','categoryIdUpdateTime'),
                             ('rank','rankUpdateTime'),('deleted','deletedTime')]:
        if field in values: result[timestamp] = stamp
    if any(k in values for k in ('categoryId','rank','orderKey','deleted')):
        result['structureUpdateTime'] = stamp
    return result


_local_lock = threading.RLock()


@contextlib.contextmanager
def writer_lock(path):
    """OS lock releases on crashes and serializes different MCP client processes."""
    with _local_lock:
        with open(path, 'a+b') as f:
            if f.tell() == 0:
                f.write(b'0'); f.flush()
            f.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise Conflict('另一个助手正在写入，请稍后使用相同 request_id 重试。') from error
            try:
                yield
            finally:
                f.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class Store:
    def __init__(self, data_dir: Path, app_running: Callable[[], bool] | None = None):
        self.root = Path(data_dir).resolve()
        self.app_running = app_running or (lambda: False)

    @property
    def journal(self):
        return self.root / 'MCP' / 'operations.db'

    def read(self, name='Room.db'):
        path = self.root / name
        if not path.is_file(): raise FileNotFoundError(f'数据库不存在: {path}')
        c = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        return c

    def book(self, c, name):
        if not name: raise Conflict('必须指定书籍，禁止无范围写入。')
        rows = [dict(r) for r in c.execute('SELECT * FROM Folder WHERE deleted=0')]
        exact = [r for r in rows if r['id'] == name or r['name'].strip('\ufeff\u200b ') == name]
        matches = exact or [r for r in rows if name in r['name']]
        if len(matches) != 1: raise Conflict('书籍不存在或不唯一，请使用精确书籍 ID。')
        if matches[0]['id'] == 'PW_Trash': raise Conflict('废纸篓不能作为写入书籍。')
        return matches[0]

    def article(self, c, name, book_id, include_deleted=False):
        exact = c.execute('SELECT * FROM Article WHERE id=?', (name,)).fetchone()
        if exact:
            if exact['folderId'] != book_id: raise Conflict('章节 ID 不属于指定书籍。')
            if exact['deleted'] and not include_deleted: raise Conflict('章节已删除。')
            return dict(exact)
        rows = [dict(r) for r in c.execute('SELECT * FROM Article WHERE folderId=? AND deleted=0', (book_id,))]
        matches = [r for r in rows if r['title'].strip('\ufeff\u200b ') == name]
        if not matches: matches = [r for r in rows if name and name in r['title']]
        if len(matches) != 1: raise Conflict('章节不存在或不唯一，请使用 pw_read_chapter 返回的章节 ID。')
        return matches[0]

    @staticmethod
    def check_revision(row, expected):
        if not expected or revision(row) != expected:
            raise Conflict(f'《{row["title"]}》版本不符或缺少 expected_revision；请重新读取并核对修改。')

    def batch(self, changes, *, book, dry_run=True, request_id='', label='散落修改'):
        if not changes or len(changes) > 100: raise Conflict('每轮须有 1–100 个章节。')
        payload = dict(changes=changes, book=book, label=label)
        def plan(c):
            bid = self.book(c, book)['id']
            rows, seen = [], set()
            for change in changes:
                old = self.article(c, change['chapter_id'], bid)
                if old['id'] in seen: raise Conflict('同一章的所有修改请放在同一个 edits 列表。')
                seen.add(old['id'])
                self.check_revision(old, change.get('expected_revision'))
                body = patch_text(old['content'], change['edits'])
                if body != old['content']:
                    rows.append(dict(table='Article', before=old, after=updated_article(old, content=body)))
            return rows
        return self.mutate('batch_patch', payload, plan, dry_run=dry_run, request_id=request_id, label=label)

    def _lookup(self, request_id):
        if not self.journal.exists(): return None
        c = sqlite3.connect(self.journal.as_uri()+'?mode=ro', uri=True)
        try:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='operations'").fetchone(): return None
            row = c.execute('SELECT fingerprint,result FROM operations WHERE request_id=?', (request_id,)).fetchone()
            return (row[0], json.loads(row[1])) if row else None
        finally: c.close()

    @staticmethod
    def _replay(existing, fingerprint):
        if existing[0] != fingerprint: raise Conflict('request_id 已被不同操作使用，请为新修改使用新 ID。')
        return dict(existing[1], replayed=True)

    def mutate(self, action, payload, planner, *, dry_run=True, request_id='', label=''):
        fingerprint = revision(dict(action=action, payload=payload))
        if not dry_run:
            if not request_id or len(request_id) > 200:
                raise Conflict('提交须提供非空 request_id（最长 200 字符）；同一次操作重试请使用相同 ID。')
            existing = self._lookup(request_id)
            if existing: return self._replay(existing, fingerprint)
        with contextlib.closing(self.read()) as c:
            plan = planner(c)
        preview = self.describe(plan)
        if dry_run: return dict(ok=True, dry_run=True, action=action, label=label, **preview)
        if self.app_running():
            raise Conflict('Pure Writer 正在运行。请保存并退出后提交；本服务不会强制关闭程序。')
        if not plan: return dict(ok=True, action='no_change', dry_run=False, **preview)
        for name in ('Room.db', 'History.db'):
            with contextlib.closing(self.read(name)) as c:
                if c.execute('PRAGMA journal_mode').fetchone()[0] not in ('delete','persist','truncate'):
                    raise Conflict('数据库不是 rollback journal 模式，不能保证跨库原子提交；已停止，未改模式。')
        # The only new schema is in our own sidecar, never in a native database.
        self.journal.parent.mkdir(exist_ok=True)
        with writer_lock(self.journal.parent/'writer.lock'):
            existing = self._lookup(request_id)
            if existing: return self._replay(existing, fingerprint)
            if self.app_running(): raise Conflict('Pure Writer 已启动，写入已停止。')
            return self._commit(action, fingerprint, request_id, label, planner)

    @staticmethod
    def describe(plan):
        chapters = []
        for item in plan:
            if item['table'] != 'Article': continue
            old, new = item['before'], item['after']
            body_before = old['content'] if old else ''
            body_after = new['content']
            diff = ''.join(difflib.unified_diff(body_before.splitlines(keepends=True),
                         body_after.splitlines(keepends=True), fromfile='修改前', tofile='修改后'))
            chapters.append(dict(id=new['id'], title=new['title'], before_revision=revision(old) if old else None,
                after_revision=revision(new), old_chars=len(body_before), new_chars=len(body_after),
                changed_fields=[k for k in new if not old or new[k] != old[k]],
                diff=diff[:16000], diff_truncated=len(diff)>16000))
        return dict(chapter_count=len(chapters), chapters=chapters, row_count=len(plan))

    def _commit(self, action, fingerprint, request_id, label, planner):
        op_id = uuid.uuid4().hex
        c = sqlite3.connect((self.root/'Room.db').as_uri()+'?mode=rw', uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        committed = False
        try:
            c.execute('ATTACH DATABASE ? AS hist', ((self.root/'History.db').as_uri()+'?mode=rw',))
            c.execute('ATTACH DATABASE ? AS audit', (str(self.journal),))
            for schema in ('main','hist','audit'):
                if c.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0] not in ('delete','persist','truncate'):
                    raise Conflict('检测到不支持的 journal 模式，未执行写入。')
                c.execute(f'PRAGMA {schema}.synchronous=FULL')
            c.execute('''CREATE TABLE IF NOT EXISTS audit.operations(
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                created_at INTEGER NOT NULL, action TEXT NOT NULL, label TEXT NOT NULL,
                changes TEXT NOT NULL, result TEXT NOT NULL)''')
            c.commit()
            c.execute('BEGIN IMMEDIATE')
            plan = planner(c)  # Re-resolve structure and revisions under the write locks.
            preview = self.describe(plan)
            # Reserve write locks in all attached databases before checking or backing up.
            for item in plan:
                self._check_row(c, item)
            if self.app_running(): raise Conflict('Pure Writer 在提交前启动，已取消本轮写入。')
            backup_dir = self.root/'Backups'/'MCP_Backups'/op_id
            backup_dir.mkdir(parents=True)
            archive_path = backup_dir/'databases.zip'
            with zipfile.ZipFile(archive_path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
                for name in ('Room.db','History.db'):
                    with contextlib.closing(self.read(name)) as source, contextlib.closing(sqlite3.connect(':memory:')) as dest:
                        source.backup(dest)
                        archive.writestr(name,dest.serialize())
            with open(archive_path,'r+b') as backup_file:
                backup_file.flush()
                os.fsync(backup_file.fileno())
            with zipfile.ZipFile(archive_path) as archive:
                if archive.testzip() is not None: raise Conflict('备份完整性检查失败，未修改原稿。')
            for item in plan:
                table, before, after = item['table'], item['before'], item['after']
                if table == 'Article' and before: self._history(c, before)
                self._write_row(c, table, before, after)
                if table == 'Article': self._history(c, after)
            result = dict(ok=True, action=action, operation_id=op_id, request_id=request_id,
                label=label, dry_run=False, replayed=False, verified=True,
                backup_dir=str(backup_dir), **preview)
            # Verify real stored values, including trigger effects, before committing any DB.
            for item in plan:
                table, after = item['table'], item['after']
                row = c.execute(f'SELECT * FROM {table} WHERE id=?', (after['id'],)).fetchone()
                if not row or dict(row) != after: raise Conflict('写入读回不一致，已撤销整轮修改。')
            if self.app_running(): raise Conflict('Pure Writer 在提交过程中启动，整轮修改已撤销。')
            c.execute('INSERT INTO audit.operations VALUES(?,?,?,?,?,?,?,?)',
                (op_id,request_id,fingerprint,now_ms(),action,label,encoded(plan),encoded(result)))
            c.commit()
            committed = True
        finally:
            if not committed: c.rollback()
            c.close()
        return result

    @staticmethod
    def _check_row(c, item):
        table, before, after = item['table'], item['before'], item['after']
        if table not in ('Article','Category'): raise Conflict('不支持的修改表。')
        row = c.execute(f'SELECT * FROM {table} WHERE id=?', (after['id'],)).fetchone()
        if before is None:
            if row: raise Conflict('新建 ID 已存在。')
        elif row is None or dict(row) != before:
            raise Conflict('预览后原稿发生变化，本轮未写入；请重新读取。')

    @staticmethod
    def _write_row(c, table, before, after):
        # Column names come solely from validated native schema / internal planners.
        columns = [r[1] for r in c.execute(f'PRAGMA table_info({table})')]
        if set(after) != set(columns): raise Conflict('数据库结构与当前适配不符，未执行写入。')
        if before is None:
            c.execute(f'INSERT INTO {table} ('+','.join('"'+k+'"' for k in columns)+') VALUES ('+
                      ','.join('?' for _ in columns)+')', [after[k] for k in columns])
        else:
            keys = [k for k in columns if k != 'id']
            c.execute(f'UPDATE {table} SET '+','.join('"'+k+'"=?' for k in keys)+' WHERE id=?',
                      [after[k] for k in keys]+[after['id']])

    @staticmethod
    def _history(c, article):
        columns = [r[1] for r in c.execute('PRAGMA hist.table_info(History)')]
        last_time = c.execute('SELECT COALESCE(MAX(createTime),0) FROM hist.History').fetchone()[0]
        values = {'id': -(uuid.uuid4().int % (2**62-1)+1), 'createTime': max(now_ms(),last_time+1)}
        values.update({'article_'+k:v for k,v in article.items() if 'article_'+k in columns})
        if not {'article_id','article_title','article_content'}.issubset(values):
            raise Conflict('History.db 结构不兼容，未执行写入。')
        keys = list(values)
        c.execute('INSERT INTO hist.History ('+','.join('"'+k+'"' for k in keys)+') VALUES ('+
                  ','.join('?' for _ in keys)+')', [values[k] for k in keys])

    def operations(self, limit=20):
        if not self.journal.exists(): return []
        with contextlib.closing(sqlite3.connect(self.journal.as_uri()+'?mode=ro', uri=True)) as c:
            c.row_factory = sqlite3.Row
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='operations'").fetchone(): return []
            return [dict(r) for r in c.execute('SELECT id,request_id,created_at,action,label FROM operations ORDER BY created_at DESC,rowid DESC LIMIT ?',
                                               (max(1,min(limit,100)),))]

    def operation(self, operation_id):
        if not self.journal.exists(): raise Conflict('没有本服务的修改记录。')
        with contextlib.closing(sqlite3.connect(self.journal.as_uri()+'?mode=ro', uri=True)) as c:
            row = c.execute('SELECT changes,result FROM operations WHERE id=?',(operation_id,)).fetchone()
            if not row: raise Conflict('找不到 operation_id。')
            return json.loads(row[0]), json.loads(row[1])

    def undo(self, operation_id, *, dry_run=True, request_id=''):
        original, _ = self.operation(operation_id)
        def plan(c):
            result = []
            for item in reversed(original):
                table, before, after = item['table'], item['before'], item['after']
                row = c.execute(f'SELECT * FROM {table} WHERE id=?',(after['id'],)).fetchone()
                if not row or dict(row) != after:
                    raise Conflict('本轮修改后已有其他编辑，不能直接整轮回退。请读回后局部修改。')
                current = dict(row)
                if before is None:
                    if table == 'Category' and c.execute(
                        'SELECT 1 FROM Article WHERE categoryId=? AND deleted=0 LIMIT 1', (after['id'],)
                    ).fetchone():
                        raise Conflict('新分卷中已有后续章节，不能回退分卷创建。')
                    restored = dict(current, deleted=1, deletedTime=now_ms(), updateTime=now_ms())
                    if 'structureUpdateTime' in restored: restored['structureUpdateTime'] = now_ms()
                else:
                    restored = dict(before)
                    stamp = max(now_ms(), current.get('updateTime',0)+1)
                    restored['updateTime'] = stamp
                    for field in ('titleUpdateTime','rankUpdateTime','categoryIdUpdateTime',
                                  'structureUpdateTime','deletedTime'):
                        if field in restored and before.get(field) != after.get(field): restored[field] = stamp
                result.append(dict(table=table,before=current,after=restored))
            return result
        return self.mutate('undo',dict(operation_id=operation_id),plan,dry_run=dry_run,
                           request_id=request_id,label='回退 '+operation_id)
