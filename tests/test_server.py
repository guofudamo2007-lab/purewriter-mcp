import asyncio
import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pw_server as api
from pw_engine import Conflict
from test_engine import fixture, db


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name); fixture(self.root)
        self.data=patch.object(api,'DATA',self.root); self.data.start()
        self.proc=patch.object(api,'running_pids',return_value=[]); self.proc.start()
        self.book='book'

    def tearDown(self):
        self.proc.stop(); self.data.stop(); self.tmp.cleanup()

    def read(self, aid='a'):
        return api.pw_read_chapter(aid,self.book)['article']

    def test_original_read_tools_and_revision(self):
        self.assertTrue(api.pw_get_status()['write_ready'])
        self.assertEqual(api.pw_get_book_outline(self.book)['book']['total_chapters'],2)
        self.assertEqual(api.pw_list_books()['count'],2)
        self.assertEqual(api.pw_search('门口',self.book)['hit_count'],1)
        self.assertEqual(len(api.pw_read_chapters(['a','b'],self.book)['articles']),2)
        self.assertEqual(len(self.read()['revision']),64)

    def test_create_update_append_delete_and_restore(self):
        create=api.pw_create_chapter('新章','　　第一段。',category_name='volume',book=self.book,
            dry_run=False,request_id='create')
        aid=create['chapters'][0]['id']; row=self.read(aid)
        api.pw_update_chapter(aid,row['revision'],content='  保留缩进。\r\n',new_title='新标题',
            book=self.book,dry_run=False,request_id='update')
        row=self.read(aid)
        self.assertEqual(row['content'],'  保留缩进。\r\n')
        api.pw_append_chapter(aid,'追加。',row['revision'],book=self.book,dry_run=False,request_id='append')
        row=self.read(aid); self.assertEqual(row['content'],'  保留缩进。\r\n追加。')
        hist=api.pw_list_history(aid,self.book)['history']
        self.assertIsInstance(hist[0]['id'],str)
        api.pw_delete_chapter(aid,row['revision'],book=self.book,dry_run=False,request_id='delete')
        row=self.read(aid); self.assertEqual(row['deleted'],1)
        api.pw_restore_history(hist[0]['id'],row['revision'],book=self.book,dry_run=False,request_id='restore')
        row=self.read(aid); self.assertEqual(row['deleted'],0)
        self.assertEqual(row['title'],'新标题')
        self.assertEqual(row['content'],'  保留缩进。\r\n追加。')

    def test_insert_in_middle_and_create_category(self):
        created=api.pw_create_category('新卷',book=self.book,dry_run=False,request_id='cat')
        self.assertEqual(created['row_count'],1)
        api.pw_create_chapter('中间章','正文',category_name='volume',after_chapter='a',book=self.book,
            dry_run=False,request_id='middle')
        with contextlib.closing(api.store().read()) as c:
            titles=[r[0] for r in c.execute("SELECT title FROM Article WHERE folderId='book' AND deleted=0 ORDER BY orderKey,rank")]
        self.assertEqual(titles,['第一章','中间章','第二章'])

    def test_duplicate_title_never_silently_selects_first(self):
        api.pw_create_chapter('第一章','同名章节',category_name='volume',book=self.book,
            dry_run=False,request_id='duplicate')
        with self.assertRaises(Conflict): api.pw_read_chapter('第一章',self.book)

    def test_category_undo_does_not_orphan_later_chapter(self):
        cat=api.pw_create_category('后续卷',book=self.book,dry_run=False,request_id='new-category')
        api.pw_create_chapter('后续章','内容',category_name='后续卷',book=self.book,dry_run=False,request_id='later')
        with self.assertRaises(Conflict):
            api.pw_undo_operation(cat['operation_id'],dry_run=False,request_id='undo-category')

    def test_export_requires_current_hash_before_overwriting(self):
        # Outside the app data directory; the entire fixture stays temporary.
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'draft.txt'; out.write_text('不能覆盖的新稿',encoding='utf-8')
            before=out.read_bytes()
            preview=api.pw_export_book_txt(self.book,str(out))
            self.assertEqual(out.read_bytes(),before)
            with self.assertRaises(Conflict): api.pw_export_book_txt(self.book,str(out),dry_run=False)
            result=api.pw_export_book_txt(self.book,str(out),dry_run=False,expected_file_sha256=preview['existing_sha256'])
            self.assertTrue(result['ok'])
            self.assertTrue(out.read_text(encoding='utf-8').startswith('测试小说\n'))
            self.assertEqual(Path(result['backup_txt_path']).read_bytes(),before)

    def test_process_detection_error_fails_closed(self):
        row=self.read()
        with patch.object(api,'running_pids',side_effect=Conflict('进程检查失败')):
            with self.assertRaises(Conflict):
                api.pw_patch_chapter('a','门口很冷。','修改。',row['revision'],book=self.book,dry_run=False,request_id='error')
        self.assertEqual(self.read()['content'],row['content'])

    def test_no_implicit_book_or_txt_and_portable_export_directory(self):
        with self.assertRaises(Conflict): api.pw_read_chapter('a',book='')
        with self.assertRaises(Conflict): api.pw_diff_with_txt(txt_path='',book=self.book)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(api,'EXPORT_DIR',Path(folder)):
                preview=api.pw_export_book_txt(self.book)
                self.assertEqual(Path(preview['output_path']).parent,Path(folder))
                self.assertFalse(Path(preview['output_path']).exists())


@unittest.skipUnless(os.environ.get('PW_RUN_PROTOCOL_TEST')=='1','explicit protocol subprocess integration')
class ProtocolTests(unittest.TestCase):
    def test_real_stdio_preview_commit_error_undo(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); fixture(root)
            async def run():
                params=StdioServerParameters(command=sys.executable,args=['-B',str(Path(api.__file__).with_name('purewriter_mcp_server.py'))],
                    env=dict(os.environ,PUREWRITER_DATA_DIR=str(root),DEFAULT_BOOK_NAME='book',PYTHONIOENCODING='utf-8'))
                async with stdio_client(params) as (reader,writer):
                    async with ClientSession(reader,writer) as session:
                        await session.initialize()
                        tools=(await session.list_tools()).tools
                        self.assertIn('pw_batch_patch',[t.name for t in tools])
                        self.assertTrue(next(t for t in tools if t.name=='pw_read_chapter').annotations.read_only_hint)
                        async def call(name,args):
                            result=await session.call_tool(name,args)
                            self.assertFalse(result.is_error,str(result.content))
                            return result.structured_content or json.loads(result.content[0].text)
                        row=(await call('pw_read_chapter',{'chapter_id_or_title':'a'}))['article']
                        changes=[dict(chapter_id='a',expected_revision=row['revision'],edits=[dict(old_text='门口很冷。',new_text='协议测试改写。')])]
                        preview=await call('pw_batch_patch',{'changes':changes})
                        self.assertTrue(preview['dry_run'])
                        applied=await call('pw_batch_patch',{'changes':changes,'dry_run':False,'request_id':'protocol'})
                        bad=await session.call_tool('pw_batch_patch',{'changes':changes,'dry_run':False,'request_id':'stale'})
                        self.assertTrue(bad.is_error)
                        self.assertIn('版本不符',str(bad.content))
                        await call('pw_undo_operation',{'operation_id':applied['operation_id'],'dry_run':False,'request_id':'undo-protocol'})
                        restored=(await call('pw_read_chapter',{'chapter_id_or_title':'a'}))['article']
                        self.assertEqual(restored['content'],row['content'])
            asyncio.run(run())


if __name__=='__main__': unittest.main()
