"""The asset page reads one reviewed content file; never infers text relations."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.adapters import assets

SOURCE = Path(__file__).resolve().parents[1] / 'contents.jsonc'


class TestAssets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'src').mkdir()
        # Independent fixture parse: the committed header uses full-line comments.
        self.data = json.loads('\n'.join(line for line in SOURCE.read_text().splitlines()
                                         if not line.startswith('//')))
        self.path = self.root / 'src/contents.jsonc'
        self.save()

    def save(self):
        self.path.write_text('// release notes\n' + json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def test_real_content_is_valid_and_complete(self):
        catalog = assets.load_assets(SOURCE.parent.parent)
        self.assertEqual(catalog, self.data)
        self.assertTrue(catalog['agents'])
        self.assertTrue(catalog['knowledge'])

    def test_edits_are_read_without_restarting_or_rebuilding(self):
        assets.load_assets(self.root)
        self.data['skills'][0]['description'] = '本次发布的新说明'
        self.data['ui']['title'] = '新的页面标题'
        self.data['agents'][0]['relations'] = {}
        self.save()
        result = assets.load_assets(self.root)
        self.assertEqual(result['skills'][0]['description'], '本次发布的新说明')
        self.assertEqual(result['ui']['title'], '新的页面标题')
        self.assertEqual(result['agents'][0]['relations'], {})

    def test_prose_and_directories_cannot_invent_relations(self):
        self.data['agents'][0]['relations'] = {}
        self.data['agents'][0]['description'] = '禁止加载 `extract-constraints` 技能。必载知识。'
        self.save()
        outside = self.root / '.opencode/agent'
        outside.mkdir(parents=True)
        (outside / 'new-agent.md').write_text('立即用 skill 工具加载 `unknown` 技能。')
        result = assets.load_assets(self.root)
        self.assertEqual(result['agents'][0]['relations'], {})
        self.assertEqual(len(result['agents']), len(self.data['agents']))

    def test_new_agent_asset_and_family_need_no_code_mapping(self):
        item = {'id': 'new:rule', 'name': 'rule', 'title': '新增知识', 'description': '新增说明',
                'kind': 'knowledge', 'availability': 'ready', 'family': 'third', 'scope': 'other'}
        self.data['families'].append({'id': 'third', 'title': '第三类', 'description': '第三类算子', 'expanded_scopes': []})
        self.data['knowledge'].append(item)
        self.data['agents'].append({'name': 'new-agent', 'role': '新角色', 'color': '#123456',
            'description': '新职责', 'when_to_use': '需要时', 'outcome': '分析结果', 'availability': 'ready',
            'relations': {'new:rule': 'conditional'}})
        self.save()
        result = assets.load_assets(self.root)
        self.assertEqual(result['agents'][-1]['relations'], {'new:rule': 'conditional'})
        self.assertEqual(result['knowledge'][-1], item)

    def test_comments_do_not_corrupt_urls_or_comment_like_strings(self):
        self.data['ui']['subtitle'] = 'https://example.test/a // 字符串 /* 原样保留 */'
        self.path.write_text('/* 注释 */\n' + json.dumps(self.data) + '\n// end\n')
        self.assertEqual(assets.load_assets(self.root)['ui']['subtitle'], self.data['ui']['subtitle'])

    def test_missing_file_does_not_fall_back_to_scanning(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            assets.load_assets(self.root)

    def test_duplicate_json_keys_are_rejected(self):
        self.path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, '重复'):
            assets.load_assets(self.root)

    def test_bad_references_and_states_fail_closed(self):
        original = copy.deepcopy(self.data)
        mutations = [
            lambda d: d['agents'][0]['relations'].update({'missing:skill': 'required'}),
            lambda d: d['agents'][0]['relations'].update({d['skills'][0]['id']: 'maybe'}),
            lambda d: d['knowledge'][0].update(family='nonexistent'),
            lambda d: d['knowledge'][0].update(scope='nonexistent'),
            lambda d: d.update(default_agent='nonexistent'),
            lambda d: d.update(default_family='nonexistent'),
            lambda d: d['skills'].append(copy.deepcopy(d['skills'][0])),
            lambda d: d['agents'].append(copy.deepcopy(d['agents'][0])),
            lambda d: d['skills'][0].update(availability='installed'),
            lambda d: d['ui'].pop('title'),
            lambda d: d['skills'][0].update(description=None),
            lambda d: d['families'][0].update(expanded_scopes=['nonexistent']),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.data = copy.deepcopy(original)
                mutate(self.data)
                self.save()
                with self.assertRaises(ValueError):
                    assets.load_assets(self.root)

    def test_content_symlink_outside_src_is_rejected(self):
        other = self.root / 'outside.jsonc'
        other.write_text(json.dumps(self.data))
        self.path.unlink()
        self.path.symlink_to(other)
        with self.assertRaises(ValueError):
            assets.load_assets(self.root)

    def test_unclosed_comment_is_rejected(self):
        self.path.write_text('/* unfinished')
        with self.assertRaises(ValueError):
            assets.load_assets(self.root)

    def test_non_json_numbers_are_rejected_even_in_extra_fields(self):
        self.path.write_text(json.dumps(self.data)[:-1] + ',"extra": NaN}')
        with self.assertRaises(ValueError):
            assets.load_assets(self.root)


if __name__ == '__main__':
    unittest.main()
