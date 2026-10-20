import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from config_formats import parse_jsonc, update_jsonc
from configure_clients import (CLIENTS, PROJECT_PATHS, apply_plans, build_settings,
                               client_entry, config_path, config_snippet, main, plan_configs)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.project = self.home / '中文 project'
        self.project.mkdir()
        self.settings = build_settings(self.home, python=sys.executable, book='示例小说')

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_adapters_round_trip_preserve_other_settings_and_are_idempotent(self):
        for name, (_, root_key, _) in CLIENTS.items():
            with self.subTest(client=name):
                path = self.project / (name + ('.toml' if name == 'codex' else '.json'))
                original = ('model = "custom"\n[mcp_servers.other]\ncommand = "other"\n'
                            if name == 'codex' else '{"custom":true,"' + root_key + '":{"other":{"command":"other"}}}')
                path.write_text(original, encoding='utf-8')
                before = path.read_bytes()
                plans, _ = plan_configs(self.home, client=name, python=sys.executable,
                                        config_path_override=path, book='示例小说')
                self.assertEqual(path.read_text(encoding='utf-8'), original)
                changed = apply_plans(plans)
                self.assertEqual(Path(changed[0]['backup']).read_bytes(), before)
                actual = path.read_text(encoding='utf-8')
                obj = tomllib.loads(actual) if name == 'codex' else parse_jsonc(actual)[0]
                self.assertEqual(obj[root_key]['other'], {'command': 'other'})
                self.assertEqual(obj[root_key]['purewriter'], client_entry(name, self.settings))
                again, _ = plan_configs(self.home, client=name, python=sys.executable,
                                        config_path_override=path, book='示例小说')
                self.assertEqual(apply_plans(again), [])

    def test_transport_schemas_and_no_automatic_approval(self):
        for name, (_, root_key, _) in CLIENTS.items():
            with self.subTest(client=name):
                snippet = config_snippet(name, self.settings)
                obj = tomllib.loads(snippet) if name == 'codex' else json.loads(snippet)
                entry = obj[root_key]['purewriter']
                if name == 'opencode':
                    self.assertEqual(entry['type'], 'local')
                    self.assertEqual(entry['command'], [self.settings['command'], *self.settings['args']])
                    self.assertEqual(entry['environment'], self.settings['env'])
                    self.assertNotIn('args', entry)
                    self.assertNotIn('env', entry)
                else:
                    self.assertEqual(entry['command'], self.settings['command'])
                    self.assertEqual(entry['args'], self.settings['args'])
                if name in ('vscode', 'copilot-cli', 'claude-code'):
                    self.assertEqual(entry['type'], 'stdio')
                for key in ('trust', 'autoApprove', 'alwaysAllow', 'tools', 'disabled', 'enabled'):
                    self.assertNotIn(key, entry)

    def test_preserve_permissions_disabled_timeout_and_custom_environment(self):
        for name in ('codex', 'cline', 'opencode', 'gemini-cli'):
            with self.subTest(client=name):
                key = CLIENTS[name][1]
                env_key = 'environment' if name == 'opencode' else 'env'
                entry = client_entry(name, self.settings)
                entry.update(enabled=False, disabled=True, autoApprove=['pw_read_chapter'],
                             trust=False, timeout=12345)
                entry[env_key]['CUSTOM_TOKEN'] = 'private-example-value'
                entry[env_key]['DEFAULT_BOOK_NAME'] = 'old'
                entry[env_key]['NOVEL_TXT_PATH'] = 'old.txt'
                path = self.project / name
                if name == 'codex':
                    from config_formats import toml_snippet
                    original = toml_snippet(entry)
                else:
                    original = json.dumps({key: {'purewriter': entry}})
                path.write_text(original, encoding='utf-8')
                before = path.read_bytes()
                plans, _ = plan_configs(self.home, client=name, python=sys.executable, config_path_override=path)
                updated = tomllib.loads(plans[0][2]) if name == 'codex' else parse_jsonc(plans[0][2])[0]
                result = updated[key]['purewriter']
                for field in ('enabled', 'disabled', 'autoApprove', 'trust', 'timeout'):
                    self.assertEqual(result[field], entry[field])
                self.assertEqual(result[env_key]['CUSTOM_TOKEN'], 'private-example-value')
                self.assertNotIn('DEFAULT_BOOK_NAME', result[env_key])
                self.assertNotIn('NOVEL_TXT_PATH', result[env_key])

    def test_project_and_environment_paths(self):
        for name, relative in PROJECT_PATHS.items():
            with self.subTest(client=name):
                self.assertEqual(config_path(self.home, name, project_dir=self.project), self.project / relative)
        for name, var, suffix in (
            ('codex', 'CODEX_HOME', 'config.toml'),
            ('copilot-cli', 'COPILOT_HOME', 'mcp-config.json'),
            ('claude-desktop', 'APPDATA', 'Claude/claude_desktop_config.json'),
            ('devin', 'APPDATA', 'devin/mcp_config.json'),
        ):
            self.assertEqual(config_path(self.home, name, environ={var: str(self.project)}), self.project / suffix)
        for name, var in (('cline', 'CLINE_MCP_SETTINGS_PATH'), ('opencode', 'OPENCODE_CONFIG')):
            target = self.project / 'custom.jsonc'
            self.assertEqual(config_path(self.home, name, environ={var: str(target)}), target)
        for name in ('claude-code', 'vscode', 'roo-code', 'windsurf'):
            with self.assertRaises(ValueError):
                config_path(self.home, name)

    def test_opencode_existing_jsonc_and_ambiguous_files(self):
        config = self.project / 'opencode.jsonc'
        config.write_text('{}', encoding='utf-8')
        self.assertEqual(config_path(self.home, 'opencode', project_dir=self.project), config)
        (self.project / 'opencode.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            config_path(self.home, 'opencode', project_dir=self.project)

    def test_shared_project_config_deduplicates_and_rejects_ambiguous_override(self):
        plans, _ = plan_configs(self.home, client=['claude-code', 'copilot-cli', 'claude-code'],
                                python=sys.executable, project_dir=self.project)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0][0], self.project / '.mcp.json')
        with self.assertRaises(ValueError):
            plan_configs(self.home, client=['codex', 'cursor'], python=sys.executable,
                         config_path_override=self.project / 'config.json')

    def test_new_empty_file_after_preview_is_a_conflict(self):
        path = self.project / 'new.json'
        plans, _ = plan_configs(self.home, client='cursor', python=sys.executable, config_path_override=path)
        path.write_text('{}', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            apply_plans(plans)
        self.assertEqual(path.read_text(encoding='utf-8'), '{}')
        self.assertEqual(list(self.project.glob('*.bak')), [])

    def test_malformed_duplicate_or_remote_config_refuses_without_writes(self):
        for original in ('{"mcpServers": []}', '{"mcpServers":null}', '{"mcpServers":{"purewriter":null}}',
                         '{"mcpServers":{},"mcpServers":{}}', '{"mcpServers":{"purewriter":{"url":"https://example.invalid"}}}',
                         '{"mcpServers":{"purewriter":{"env":[]}}}', '{,}'):
            with self.subTest(original=original):
                path = self.project / 'bad.json'
                path.write_text(original, encoding='utf-8')
                with self.assertRaises(ValueError):
                    plan_configs(self.home, client='cursor', python=sys.executable, config_path_override=path)
                self.assertEqual(path.read_text(encoding='utf-8'), original)
                self.assertEqual(list(self.project.glob('*.bak')), [])

    def test_preview_and_errors_never_print_existing_secrets(self):
        path = self.project / 'private.json'
        path.write_text('{"token":"private-example-value","mcpServers":{}}', encoding='utf-8')
        output = io.StringIO()
        argv = ['configure_clients.py', '--client', 'cursor', '--python', sys.executable, '--config-path', str(path)]
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
            main()
        self.assertNotIn('private-example-value', output.getvalue())
        path.write_text('{"private-example-value"', encoding='utf-8')
        errors = io.StringIO()
        with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            main()
        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn('private-example-value', errors.getvalue())

    def test_list_and_snippet_do_not_read_or_write_user_config(self):
        for argv in (['--list-clients'], ['--client', 'claude-code', '--python', sys.executable, '--print-config']):
            output = io.StringIO()
            with patch.object(sys, 'argv', ['configure_clients.py', *argv]), \
                 patch('configure_clients._read', side_effect=AssertionError('must not read configs')), \
                 contextlib.redirect_stdout(output):
                main()
            self.assertIsInstance(json.loads(output.getvalue()), dict)

    def test_unknown_client_fails_instead_of_succeeding_with_no_changes(self):
        with self.assertRaises(ValueError):
            plan_configs(self.home, client='not-a-client', python=sys.executable)


class JsoncTests(unittest.TestCase):
    def test_insert_across_object_layouts_with_comments_and_trailing_commas(self):
        samples = [
            '{}', '{"other":1}', '{"mcpServers":{}}',
            '{"mcpServers":{"other":{"args":["a,b",],},},}',
            '{// top\n"mcpServers":{/* start */"other":{} // last, comment\n},\n"tail":1}',
            '{"mcpServers":{"other":{}, /* trailing */}, "text":"https://example.invalid/*x*/"}',
            '\ufeff{\r\n  // comment\r\n  "mcpServers": { "other": {} }\r\n}\r\n',
            '{"quote":"escaped \\" and \\\\ slash", "mcpServers":{}}',
        ]
        for original in samples:
            with self.subTest(original=original):
                before = parse_jsonc(original)[0]
                new = update_jsonc(original, 'mcpServers', {'command': '新目录/python.exe', 'args': ['x y']})
                expected = dict(before)
                expected['mcpServers'] = dict(before.get('mcpServers', {}), purewriter={'command': '新目录/python.exe', 'args': ['x y']})
                self.assertEqual(parse_jsonc(new)[0], expected)
                for comment in ('// top', '/* start */', '// last, comment', '/* trailing */', '// comment'):
                    if comment in original:
                        self.assertIn(comment, new)
                self.assertEqual(update_jsonc(new, 'mcpServers', expected['mcpServers']['purewriter']), new)
                if original.startswith('\ufeff'):
                    self.assertTrue(new.startswith('\ufeff'))
                    self.assertNotIn('\n', new.replace('\r\n', ''))

    def test_replacing_entry_preserves_other_text_byte_for_byte(self):
        prefix = '{\n// keep\n"mcpServers":{\n"other":{"env":{"KEY":"value"}}, // keep after other\n"purewriter":'
        suffix = ' /* keep beside entry */\n},\n"unknown":[1,2,],\n}\n'
        original = prefix + '{"command":"old"}' + suffix
        updated = update_jsonc(original, 'mcpServers', {'command': 'new'})
        self.assertTrue(updated.startswith(prefix))
        self.assertTrue(updated.endswith(suffix))
        self.assertEqual(parse_jsonc(updated)[0]['unknown'], [1, 2])

    def test_invalid_jsonc_is_not_silently_repaired(self):
        for original in ('{/* unclosed', '{"x":[,]}', '{"x":1,,}', '{"x":}', '[]', '{"x":1,"x":2}', '{"x":NaN}'):
            with self.subTest(original=original), self.assertRaises(ValueError):
                parse_jsonc(original)


class TomlPreservationTests(unittest.TestCase):
    def test_bom_supported_and_quoted_layout_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve()
            path = home / 'config.toml'
            original = '\ufeff[mcp_servers.purewriter]\ncommand = "old"\n[other]\nvalue = 7\n'
            path.write_bytes(original.encode('utf-8'))
            plans, _ = plan_configs(home, python=sys.executable, config_path_override=path)
            self.assertTrue(plans[0][2].startswith('\ufeff'))
            self.assertEqual(tomllib.loads(plans[0][2].lstrip('\ufeff'))['other'], {'value': 7})
            quoted = '[mcp_servers."purewriter"]\ncommand = "old"\n[other]\nvalue = 7\n'
            path.write_bytes(quoted.encode('utf-8'))
            with self.assertRaises(ValueError):
                plan_configs(home, python=sys.executable, config_path_override=path)
            self.assertEqual(path.read_bytes(), quoted.encode('utf-8'))
