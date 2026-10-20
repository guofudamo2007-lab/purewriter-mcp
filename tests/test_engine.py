import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from pw_engine import Store, Conflict, revision, patch_text, writer_lock


@contextmanager
def db(path):
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            yield conn


ARTICLE_SCHEMA = '''CREATE TABLE Article (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
 summary TEXT, count INTEGER, extension TEXT NOT NULL, preview INTEGER NOT NULL,
 preview1 INTEGER NOT NULL, updateTime INTEGER NOT NULL, createTime INTEGER NOT NULL,
 folderId TEXT NOT NULL, categoryId TEXT, editorId INTEGER NOT NULL, rank INTEGER NOT NULL,
 titleUpdateTime INTEGER NOT NULL, rankUpdateTime INTEGER NOT NULL,
 folderIdUpdateTime INTEGER NOT NULL, categoryIdUpdateTime INTEGER NOT NULL,
 extensionUpdateTime INTEGER NOT NULL, deleted INTEGER NOT NULL, deletedTime INTEGER NOT NULL,
 autoChapter INTEGER NOT NULL, autoChapterUpdateTime INTEGER NOT NULL,
 orderKey TEXT, structureUpdateTime INTEGER NOT NULL DEFAULT 0)'''


def fixture(root):
    c = sqlite3.connect(root / 'Room.db')
    c.execute(ARTICLE_SCHEMA)
    c.execute('CREATE TABLE Folder(id TEXT PRIMARY KEY, name TEXT, deleted INTEGER, rank INTEGER)')
    c.executemany('INSERT INTO Folder VALUES(?,?,0,0)', [('book', '测试小说'), ('other', '另一本书')])
    c.execute('''CREATE TABLE Category(id TEXT PRIMARY KEY, folderId TEXT NOT NULL, name TEXT NOT NULL,
        createdTime INTEGER NOT NULL, collapsed INTEGER NOT NULL, rank INTEGER NOT NULL, description TEXT,
        rankUpdateTime INTEGER NOT NULL, folderIdUpdateTime INTEGER NOT NULL, updateTime INTEGER NOT NULL,
        deleted INTEGER NOT NULL, deletedTime INTEGER NOT NULL, orderKey TEXT, structureUpdateTime INTEGER NOT NULL)''')
    c.execute("INSERT INTO Category VALUES('volume','book','第一卷',1,0,10000,'',1,1,1,0,0,'000000000001000000',1)")
    cols = [r[1] for r in c.execute('PRAGMA table_info(Article)')]
    for num, (aid, title, body, folder) in enumerate([
        ('a', '第一章', '　　门口很冷。\r\n\r\n　　他笑了。\r\n原样保留。', 'book'),
        ('b', '第二章', '重复。重复。\n收尾。', 'book'),
        ('x', '第一章', '别的书，不能改。', 'other')],1):
        row = dict.fromkeys(cols, 0)
        row.update(id=aid, title=title, content=body, summary='', count=20, extension='txt',
                   folderId=folder, categoryId='volume', orderKey=f'{num:012d}000000', rank=10000+num*3, updateTime=10, createTime=1)
        c.execute('INSERT INTO Article VALUES('+','.join('?' for _ in cols)+')', [row[k] for k in cols])
    c.commit(); c.close()
    h = sqlite3.connect(root / 'History.db')
    h.execute('CREATE TABLE History(id INTEGER PRIMARY KEY, createTime INTEGER NOT NULL,' +
              ','.join('article_'+k+' '+('TEXT' if k in ['id','title','content','summary','extension','folderId','categoryId','orderKey'] else 'INTEGER') for k in cols)+')')
    h.commit(); h.close()


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        fixture(self.root)
        self.store = Store(self.root, app_running=lambda: False)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, aid='a'):
        with closing(self.store.read()) as c:
            return dict(c.execute('SELECT * FROM Article WHERE id=?', (aid,)).fetchone())

    def changes(self):
        return [dict(chapter_id='a', expected_revision=revision(self.read()), edits=[
            dict(old_text='门口很冷。', new_text='他把手缩进袖口。'),
            dict(old_text='他笑了。', new_text='他扯了一下嘴角。')])]

    def test_preview_is_read_only_and_preserves_format(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = self.store.batch(self.changes(), book='book', dry_run=True)
        self.assertTrue(result['ok'])
        self.assertIn('他把手缩进袖口。', result['chapters'][0]['diff'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_batch_commit_history_retry_and_undo(self):
        changes = self.changes()
        before = self.read()['content']
        result = self.store.batch(changes, book='book', dry_run=False, request_id='round-1')
        self.assertEqual(self.read()['content'], '　　他把手缩进袖口。\r\n\r\n　　他扯了一下嘴角。\r\n原样保留。')
        self.assertTrue(result['verified'])
        retry = self.store.batch(changes, book='book', dry_run=False, request_id='round-1')
        self.assertTrue(retry['replayed'])
        with db(self.root/'History.db') as h:
            self.assertEqual(h.execute('SELECT COUNT(*) FROM History').fetchone()[0], 2)
        self.store.undo(result['operation_id'], dry_run=False, request_id='undo-1')
        self.assertEqual(self.read()['content'], before)

    def test_compressed_backup_contains_readable_pre_edit_databases(self):
        before=self.read()['content']
        result=self.store.batch(self.changes(),book='book',dry_run=False,request_id='backup')
        with zipfile.ZipFile(Path(result['backup_dir'])/'databases.zip') as archive:
            self.assertIsNone(archive.testzip())
            with closing(sqlite3.connect(':memory:')) as c:
                c.deserialize(archive.read('Room.db'))
                self.assertEqual(c.execute("SELECT content FROM Article WHERE id='a'").fetchone()[0],before)
                self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                c.deserialize(archive.read('History.db'))
                self.assertEqual(c.execute('SELECT COUNT(*) FROM History').fetchone()[0],0)

    def test_bad_later_chapter_prevents_all_writes(self):
        changes = self.changes()+[dict(chapter_id='b', expected_revision=revision(self.read('b')),
            edits=[dict(old_text='不存在', new_text='错误')])]
        before = self.read()
        with self.assertRaises(Conflict):
            self.store.batch(changes, book='book', dry_run=False, request_id='bad')
        self.assertEqual(before, self.read())

    def test_two_chapters_commit_and_undo_as_one_operation(self):
        changes=self.changes()+[dict(chapter_id='b',expected_revision=revision(self.read('b')),
            edits=[dict(old_text='收尾。',new_text='新的结尾。')])]
        before_a=self.read()['content']; before_b=self.read('b')['content']
        result=self.store.batch(changes,book='book',dry_run=False,request_id='multi')
        self.assertEqual(result['chapter_count'],2)
        self.assertEqual(len(self.store.operations()),1)
        self.assertEqual(self.read('b')['content'],'重复。重复。\n新的结尾。')
        self.store.undo(result['operation_id'],dry_run=False,request_id='undo-multi')
        self.assertEqual(self.read()['content'],before_a)
        self.assertEqual(self.read('b')['content'],before_b)

    def test_trigger_modification_is_detected_and_rolled_back(self):
        with db(self.root/'Room.db') as c:
            c.execute("CREATE TRIGGER mutate AFTER UPDATE ON Article BEGIN UPDATE Article SET content='外部触发器篡改' WHERE id=NEW.id; END")
        before=self.read()
        with self.assertRaises(Conflict):
            self.store.batch(self.changes(),book='book',dry_run=False,request_id='trigger')
        self.assertEqual(self.read(),before)
        with db(self.root/'History.db') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM History').fetchone()[0],0)

    def test_second_chapter_failure_rolls_back_first_chapter_and_history(self):
        with db(self.root/'History.db') as c:
            c.execute("CREATE TRIGGER second_fail BEFORE INSERT ON History WHEN NEW.article_id='b' BEGIN SELECT RAISE(ABORT,'second failure'); END")
        before_a=self.read(); before_b=self.read('b')
        changes=self.changes()+[dict(chapter_id='b',expected_revision=revision(before_b),
            edits=[dict(old_text='收尾。',new_text='改尾。')])]
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.batch(changes,book='book',dry_run=False,request_id='second-fail')
        self.assertEqual(self.read(),before_a); self.assertEqual(self.read('b'),before_b)
        with db(self.root/'History.db') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM History').fetchone()[0],0)

    def test_change_between_preflight_and_lock_is_detected(self):
        calls=0
        def raced_check():
            nonlocal calls
            calls+=1
            if calls==1:
                with db(self.root/'Room.db') as c:
                    c.execute("UPDATE Article SET content='刚刚改过' WHERE id='a'")
            return False
        self.store.app_running=raced_check
        with self.assertRaises(Conflict):
            self.store.batch(self.changes(),book='book',dry_run=False,request_id='race')
        self.assertEqual(self.read()['content'],'刚刚改过')

    def test_stale_revision_and_cross_book_id_rejected(self):
        for mutation in ['stale', 'foreign']:
            changes = self.changes()
            if mutation == 'stale': changes[0]['expected_revision'] = 'old'
            else: changes[0].update(chapter_id='x', expected_revision=revision(self.read('x')))
            with self.assertRaises(Conflict):
                self.store.batch(changes, book='book', dry_run=False, request_id=mutation)
        self.assertEqual(self.read('x')['content'], '别的书，不能改。')

    def test_undo_refuses_to_overwrite_later_manual_edit(self):
        op = self.store.batch(self.changes(), book='book', dry_run=False, request_id='first')
        with db(self.root/'Room.db') as c:
            c.execute("UPDATE Article SET content='我刚刚手改的新稿' WHERE id='a'")
        with self.assertRaises(Conflict): self.store.undo(op['operation_id'], dry_run=False, request_id='undo')
        self.assertEqual(self.read()['content'], '我刚刚手改的新稿')

    def test_history_failure_rolls_back_manuscript_and_operation(self):
        before = self.read()
        with db(self.root/'History.db') as h:
            h.execute("CREATE TRIGGER fail_history BEFORE INSERT ON History BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.batch(self.changes(), book='book', dry_run=False, request_id='failure')
        self.assertEqual(self.read(), before)
        self.assertEqual(self.store.operations(), [])

    def test_history_before_and_after_have_unambiguous_order(self):
        with patch('pw_engine.now_ms',return_value=100):
            self.store.batch(self.changes(),book='book',dry_run=False,request_id='same-millisecond')
        with db(self.root/'History.db') as c:
            rows=c.execute('SELECT createTime,article_content FROM History ORDER BY createTime').fetchall()
        self.assertLess(rows[0][0],rows[1][0])
        self.assertIn('门口很冷。',rows[0][1])
        self.assertIn('他把手缩进袖口。',rows[1][1])

    def test_running_app_refuses_write_but_allows_preview(self):
        self.store.app_running = lambda: True
        self.assertTrue(self.store.batch(self.changes(), book='book', dry_run=True)['ok'])
        with self.assertRaises(Conflict):
            self.store.batch(self.changes(), book='book', dry_run=False, request_id='running')
        self.assertFalse((self.root/'MCP').exists())

    def test_app_starting_during_transaction_rolls_everything_back(self):
        states=iter([False,False,False,True])
        self.store.app_running=lambda:next(states,True)
        before=self.read()
        with self.assertRaises(Conflict):
            self.store.batch(self.changes(),book='book',dry_run=False,request_id='start-during-write')
        self.assertEqual(self.read(),before)
        with db(self.root/'History.db') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM History').fetchone()[0],0)

    def test_cross_process_writer_lock_blocks_other_assistant(self):
        folder=self.root/'MCP'; folder.mkdir()
        script="""import json,sys
from pathlib import Path
from pw_engine import Store,Conflict
try:
 Store(Path(sys.argv[1])).batch(json.loads(sys.argv[2]),book='book',dry_run=False,request_id='other-client')
except Conflict:
 sys.exit(3)
sys.exit(4)
"""
        before=self.read()
        with writer_lock(folder/'writer.lock'):
            result=subprocess.run([sys.executable,'-B','-c',script,str(self.root),json.dumps(self.changes())],
                capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,3,result.stderr)
        self.assertEqual(self.read(),before)

    def test_reused_request_id_cannot_hide_different_edit(self):
        self.store.batch(self.changes(), book='book', dry_run=False, request_id='same')
        changes = [dict(chapter_id='b', expected_revision=revision(self.read('b')),
                        edits=[dict(old_text='收尾。', new_text='新的收尾。')])]
        with self.assertRaises(Conflict):
            self.store.batch(changes, book='book', dry_run=False, request_id='same')

    def test_wal_rejected_without_switching_native_journal(self):
        with db(self.root/'Room.db') as c: c.execute('PRAGMA journal_mode=WAL')
        with self.assertRaises(Conflict):
            self.store.batch(self.changes(), book='book', dry_run=False, request_id='wal')
        with db(self.root/'Room.db') as c:
            self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0], 'wal')

    def test_overlapping_and_ambiguous_patches_rejected(self):
        for body, edits in [
            ('abcde', [dict(old_text='abc',new_text='X'),dict(old_text='bcd',new_text='Y')]),
            ('重复。重复。', [dict(old_text='重复。',new_text='一句。')]),
            ('abc', [dict(old_text='',new_text='X')])]:
            with self.assertRaises(Conflict): patch_text(body, edits)

    def test_patches_resolve_against_original_not_previous_replacements(self):
        result = patch_text('甲。乙。', [dict(old_text='甲。',new_text='乙。'), dict(old_text='乙。',new_text='丙。')])
        self.assertEqual(result, '乙。丙。')

    def test_missing_database_does_not_create_empty_file(self):
        with self.assertRaises(FileNotFoundError): Store(self.root/'missing').read()
        self.assertFalse((self.root/'missing').exists())


if __name__ == '__main__': unittest.main()
