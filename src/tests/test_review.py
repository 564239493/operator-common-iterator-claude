import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
from workbench.adapters import review
from workbench.paths import PathEscapeError

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'runs' / 'demo'
        self.it = self.run / 'iter_001'
        self.it.mkdir(parents=True)
        (self.run / 'inputs').mkdir()
        (self.run / 'inputs' / 'operator.md').write_text('first\nsecond', encoding='utf-8')
        (self.run / 'run_state.json').write_text(json.dumps({'state': 'SUCCESS', 'current_iteration': 1, 'operator_doc': '/old/root/inputs/operator.md'}))
        self.original = {'operator_name': 'demo', 'inputs': {'x': {'type': 'tensor'}}, 'constraints_in_parameters': {'A2': [{'id': 'C-001', 'expr': 'x > 0', 'expr_type': 'value_dependency', 'relation_params': ['x'], 'src_text': 'positive', 'src_txt_line': [1]}]}}
        self.source = self.it / 'constraints.json'
        self.source.write_text(json.dumps(self.original))

    def request(self):
        data = review.get(self.root, ['runs', 'demo', 'iter_001', 'constraints'], {}, 'token')
        req = dict(data['_review'], constraints=copy.deepcopy(data))
        req['constraints']['constraints_in_parameters']['A2'][0]['expr'] = 'x > 1'
        return req

    def test_save_preserves_baseline_and_state(self):
        before = self.source.read_bytes()
        state = (self.run / 'run_state.json').read_bytes()
        result = review.save(self.root, 'demo', 'iter_001', self.request())
        self.assertFalse(result['execution_started'])
        saved = review.read(self.it / 'constraints_copy.json')
        self.assertEqual(saved['inputs'], self.original['inputs'])
        self.assertEqual(saved['constraints_in_parameters']['A2'][0]['expr'], 'x > 1')
        self.assertNotIn('_review', saved)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual((self.run / 'run_state.json').read_bytes(), state)
        self.assertFalse((self.run / 'iter_002').exists())

    def test_stale_base_and_copy_rejected(self):
        req = self.request()
        review.save(self.root, 'demo', 'iter_001', req)
        with self.assertRaises(review.Conflict):
            review.save(self.root, 'demo', 'iter_001', req)
        fresh = self.request()
        self.source.write_text('{}')
        with self.assertRaises(review.Conflict):
            review.save(self.root, 'demo', 'iter_001', fresh)

    def test_invalid_structure_does_not_write(self):
        for bad in [None, [], {'A2': 'bad'}, {'other': []}, {'A2': [{'expr': ''}]}]:
            req = self.request()
            req['constraints']['constraints_in_parameters'] = bad
            with self.assertRaises(ValueError):
                review.save(self.root, 'demo', 'iter_001', req)
        self.assertFalse((self.it / 'constraints_copy.json').exists())

    def test_duplicate_ids_rejected_new_ids_assigned(self):
        req = self.request()
        rows = req['constraints']['constraints_in_parameters']['A2']
        rows.append(copy.deepcopy(rows[0]))
        with self.assertRaises(ValueError):
            review.save(self.root, 'demo', 'iter_001', req)
        rows[1].pop('id')
        review.save(self.root, 'demo', 'iter_001', req)
        saved = review.read(self.it / 'constraints_copy.json')
        self.assertEqual(saved['constraints_in_parameters']['A2'][1]['id'], 'C-002')

    def test_symlink_and_traversal_rejected(self):
        (self.it / 'constraints_copy.json').symlink_to(self.root / 'outside.json')
        with self.assertRaises(PathEscapeError):
            self.request()
        with self.assertRaises(PathEscapeError):
            review.get(self.root, ['runs', '..', 'iter_001', 'constraints'], {}, 'token')

    def test_document_uses_snapshot_not_old_absolute_path(self):
        doc = review.get(self.root, ['runs', 'demo', 'operator_doc'], {}, '')
        self.assertEqual(doc['content'], 'first\nsecond')
        with self.assertRaises(ValueError):
            review.get(self.root, ['runs', 'demo', 'operator_doc'], {'source': ['supplement']}, '')

    def test_coverage_is_independent_and_safe(self):
        report = self.root / 'ops_cov_report' / 'report1'
        report.mkdir(parents=True)
        (report / 'demo_coverage.json').write_text('{"operator":"demo","granularities":{}}')
        (report / 'aclnnLogSumExp_analysis.md').write_text('# Test')
        self.assertEqual(review.get(self.root, ['cover', 'dirs'], {}, '')[0]['operator'], 'demo')
        self.assertEqual(review.get(self.root, ['cover', 'report1', 'analysis'], {}, '')['content'], '# Test')
        with self.assertRaises(PathEscapeError):
            review.get(self.root, ['cover', '..', 'analysis'], {}, '')

    def test_run_embedded_coverage(self):
        # 任务内嵌覆盖率：runs/<run>/ops_cov_report/<报告目录>/，按路径关联，不做名称匹配
        report = self.run / 'ops_cov_report' / 'r1'
        report.mkdir(parents=True)
        (report / 'demo_coverage.json').write_text('{"operator":"demo","granularities":{}}')
        (report / 'aclnnLogSumExp_analysis.md').write_text('# Run Cov')
        empty = self.run / 'ops_cov_report' / 'empty'
        empty.mkdir()
        dirs = review.get(self.root, ['runs', 'demo', 'cover', 'dirs'], {}, '')
        self.assertEqual(len(dirs), 1)
        self.assertEqual(dirs[0]['operator'], 'demo')
        self.assertTrue(dirs[0]['has_analysis'])
        self.assertEqual(review.get(self.root, ['runs', 'demo', 'cover', 'r1', 'coverage'], {}, '')['operator'], 'demo')
        self.assertEqual(review.get(self.root, ['runs', 'demo', 'cover', 'r1', 'analysis'], {}, '')['content'], '# Run Cov')
        # 缺报告 / 逃逸 / 不存在的报告目录
        with self.assertRaises(FileNotFoundError):
            review.get(self.root, ['runs', 'demo', 'cover', 'r1', 'nope'], {}, '')
        with self.assertRaises(PathEscapeError):
            review.get(self.root, ['runs', 'demo', 'cover', '..', 'analysis'], {}, '')
        with self.assertRaises(FileNotFoundError):
            review.get(self.root, ['runs', 'demo', 'cover', 'missing', 'coverage'], {}, '')

if __name__ == '__main__':
    unittest.main()


class CoverageTreeCacheTests(unittest.TestCase):
    """覆盖率树缓存（ops_final_cov_report）：命中复用、TTL 过期重建、后台定时刷新。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'ops_final_cov_report'
        review._TREE_CACHE.clear()
        self.addCleanup(review._TREE_CACHE.clear)
        self.addCleanup(review.stop_coverage_tree_refresher)
        self.make_version('v1')

    def make_version(self, name, covered=1):
        """造一份版本层汇总报告（level=agg 形态，带 op 粒度 metrics）。"""
        d = self.base / name / 'lib'
        d.mkdir(parents=True, exist_ok=True)
        (d / ('%s_coverage.json' % name)).write_text(json.dumps({
            'level': 'lib',
            'summary': {'op': {'metrics': [
                {'name': '函数覆盖率', 'covered': covered, 'total': 2, 'rate': covered * 50},
                {'name': '语句覆盖率', 'covered': covered, 'total': 4, 'rate': covered * 25},
                {'name': '分支覆盖率', 'covered': 0, 'total': 2, 'rate': 0},
            ]}},
        }), encoding='utf-8')
        return d

    def tree(self):
        return review.get(self.root, ['cover', 'tree'], {}, '')

    def test_repeated_reads_hit_cache(self):
        first = self.tree()
        second = self.tree()
        self.assertIs(first, second)  # 命中缓存对象：不重复遍历目录/解析 json
        self.assertEqual([v['name'] for v in first['versions']], ['v1'])
        self.assertEqual(first['versions'][0]['children'][0]['name'], 'lib')
        self.assertIn('cached_at', first)
        self.assertIsInstance(first['build_ms'], int)

    def test_expired_cache_rebuilds(self):
        first = self.tree()
        self.make_version('v2')
        # TTL 内新增报告不生效（缓存即快照）
        self.assertEqual([v['name'] for v in self.tree()['versions']], ['v1'])
        with mock.patch.object(review, 'COVERAGE_TREE_TTL', 0.0):
            rebuilt = self.tree()
        self.assertIsNot(rebuilt, first)
        self.assertEqual([v['name'] for v in rebuilt['versions']], ['v1', 'v2'])

    def test_refresher_rebuilds_in_background(self):
        self.tree()
        review.start_coverage_tree_refresher(self.root, interval=0.05)
        self.make_version('v2')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and review.coverage_tree_status(self.root)['versions'] != 2:
            time.sleep(0.05)
        status = review.coverage_tree_status(self.root)
        self.assertTrue(status['cached'])
        self.assertEqual(status['versions'], 2)

    def test_status_reports_cache_freshness(self):
        self.assertFalse(review.coverage_tree_status(self.root)['cached'])
        self.tree()
        status = review.coverage_tree_status(self.root)
        self.assertTrue(status['cached'])
        self.assertEqual(status['ttl_seconds'], review.COVERAGE_TREE_TTL)
        self.assertLess(status['age_seconds'], review.COVERAGE_TREE_TTL)

    def test_ttl_from_env_override(self):
        with mock.patch.dict('os.environ', {'WORKBENCH_COVER_TREE_TTL': '30'}):
            self.assertEqual(review._ttl_from_env(), 30.0)
        with mock.patch.dict('os.environ', {'WORKBENCH_COVER_TREE_TTL': 'bad'}):
            self.assertEqual(review._ttl_from_env(), review.DEFAULT_COVERAGE_TREE_TTL)
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(review._ttl_from_env(), review.DEFAULT_COVERAGE_TREE_TTL)

class ReviewHttpTests(ReviewTests):
    def handler(self, payload, token='test-token', origin='http://localhost:8420'):
        import io
        from workbench.server import _Handler
        handler = object.__new__(_Handler)
        handler.root = self.root
        handler.review_token = 'test-token'
        handler.path = '/api/review/runs/demo/iter_001/constraints_update'
        data = json.dumps(payload).encode()
        handler.headers = {'Content-Length': str(len(data)), 'Origin': origin, 'Host': 'localhost:8420', 'X-Review-Token': token}
        handler.rfile = io.BytesIO(data)
        self.response = None
        handler._send_json = lambda status, body: setattr(self, 'response', (status, body))
        return handler

    def test_post_requires_token_and_same_origin(self):
        for token, origin in [('', 'http://localhost:8420'), ('test-token', 'http://evil.example')]:
            self.handler(self.request(), token, origin).do_POST()
            self.assertEqual(self.response[0], 403)
        self.assertFalse((self.it / 'constraints_copy.json').exists())

    def test_post_and_conflict_response(self):
        req = self.request()
        self.handler(req).do_POST()
        self.assertEqual(self.response[0], 200)
        self.handler(req).do_POST()
        self.assertEqual(self.response[0], 409)

    def test_other_methods_and_routes_cannot_write(self):
        handler = self.handler(self.request())
        handler.do_PUT()
        self.assertEqual(self.response[0], 405)
        handler.path = '/api/runs/demo/continue'
        handler.do_POST()
        self.assertEqual(self.response[0], 405)
        self.assertFalse((self.it / 'constraints_copy.json').exists())
