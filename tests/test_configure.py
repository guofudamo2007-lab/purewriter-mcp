import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

from configure_clients import plan_configs, apply_plans


class ConfigureTests(unittest.TestCase):
    def test_preview_preserves_unrelated_settings_and_defaults_to_one_client(self):
        with tempfile.TemporaryDirectory() as root:
            home=Path(root)
            path=home/'.codex/config.toml'; path.parent.mkdir()
            original='model = "example-model"\n[mcp_servers.other]\ncommand = "other-server"\n'
            path.write_text(original,encoding='utf-8')
            plans,settings=plan_configs(home,python=sys.executable)
            self.assertEqual(len(plans),1)
            self.assertEqual(path.read_text(encoding='utf-8'),original)
            self.assertNotIn('DEFAULT_BOOK_NAME',settings['env'])
            self.assertNotIn('NOVEL_TXT_PATH',settings['env'])
            obj=tomllib.loads(plans[0][2])
            self.assertEqual(obj['model'],'example-model')
            self.assertEqual(obj['mcp_servers']['other'],{'command':'other-server'})
            self.assertFalse((home/'.gemini').exists())

    def test_apply_backup_replace_entry_and_idempotent_repeat(self):
        with tempfile.TemporaryDirectory() as root:
            home=Path(root)
            path=home/'.gemini/config/mcp_config.json'; path.parent.mkdir(parents=True)
            original={'mcpServers':{'other':{'command':'other-server'},'purewriter':{'command':'old'}},'custom':True}
            path.write_text(json.dumps(original),encoding='utf-8')
            plans,settings=plan_configs(home,client='both',python=sys.executable,book='示例小说')
            changed=apply_plans(plans)
            self.assertEqual(json.loads(Path(changed[0]['backup']).read_text(encoding='utf-8')),original)
            actual=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(actual['mcpServers']['other'],original['mcpServers']['other'])
            self.assertTrue(actual['custom'])
            self.assertEqual(actual['mcpServers']['purewriter'],settings)
            again,_=plan_configs(home,client='both',python=sys.executable,book='示例小说')
            self.assertEqual(apply_plans(again),[])

    def test_changed_config_stops_all_writes(self):
        with tempfile.TemporaryDirectory() as root:
            home=Path(root)
            plans,_=plan_configs(home,client='both',python=sys.executable)
            codex=home/'.codex/config.toml'; codex.parent.mkdir()
            codex.write_text('model = "changed"\n',encoding='utf-8')
            with self.assertRaises(RuntimeError): apply_plans(plans)
            self.assertFalse((home/'.gemini').exists())
            self.assertEqual(codex.read_text(encoding='utf-8'),'model = "changed"\n')
