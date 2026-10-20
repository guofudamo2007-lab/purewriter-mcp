"""Exercise generated launch settings with the SDK, not installed client UIs."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import configure_clients as config
from test_engine import fixture


@unittest.skipUnless(os.environ.get('PW_RUN_PROTOCOL_TEST') == '1', 'explicit protocol subprocess integration')
class GeneratedLaunchTests(unittest.TestCase):
    def test_generated_stdio_launch_shapes_with_unicode_paths_and_foreign_cwd(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / '中文 server with spaces'
            source.mkdir()
            for name in ('purewriter_mcp_server.py', 'pw_server.py', 'pw_engine.py'):
                shutil.copy2(config.ROOT / name, source / name)
            data = root / '临时 database'
            data.mkdir()
            fixture(data)
            foreign = root / 'unrelated working directory'
            foreign.mkdir()
            with patch.object(config, 'ROOT', source):
                settings = config.build_settings(root, python=sys.executable, data_dir=data, book='book')

            async def run(client):
                snippet = json.loads(config.config_snippet(client, settings))
                entry = snippet[config.CLIENTS[client][1]]['purewriter']
                if client == 'opencode':
                    command, *args = entry['command']
                    environment = entry['environment']
                else:
                    command, args, environment = entry['command'], entry['args'], entry['env']
                params = StdioServerParameters(command=command, args=args,
                                                env=dict(os.environ, **environment), cwd=str(foreign))
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        initialized = await session.initialize()
                        self.assertIn('expected_revision', initialized.instructions)
                        self.assertEqual(len((await session.list_tools()).tools), 21)
                        result = await session.call_tool('pw_read_chapter', {'chapter_id_or_title': 'a'})
                        self.assertFalse(result.is_error, str(result.content))
                        text_result = json.loads(result.content[0].text)
                        self.assertEqual(result.structured_content, text_result)
                        self.assertEqual(len(text_result['article']['revision']), 64)

            for client in ('vscode', 'opencode'):
                with self.subTest(client=client):
                    asyncio.run(run(client))
