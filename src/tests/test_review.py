import copy
import json
from pathlib import Path
import tempfile
import unittest
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
        (report / 'analysis.md').write_text('# Test')
        self.assertEqual(review.get(self.root, ['cover', 'dirs'], {}, '')[0]['operator'], 'demo')
        self.assertEqual(review.get(self.root, ['cover', 'report1', 'analysis'], {}, '')['content'], '# Test')
        with self.assertRaises(PathEscapeError):
            review.get(self.root, ['cover', '..', 'analysis'], {}, '')

if __name__ == '__main__':
    unittest.main()

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
