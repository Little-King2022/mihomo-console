import os
import tempfile
import unittest
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from app import db, service, rulesets
from app.main import app
from app.render import render_config


class RulesetsTest(unittest.TestCase):
    def setUp(self):
        self.previous = db._conn
        db._conn = None
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name, MIHOMO_HOME=self.tmp.name + '/core')
        self.env.start()
        db.init_db()
        self.client = TestClient(app)
        self.client.cookies.set('mc_token', db.get_setting('web_token'))
        self.apply = patch.object(service, 'apply_config')
        self.apply.start()

    def tearDown(self):
        self.apply.stop()
        self.client.close()
        db._conn.close()
        db._conn = self.previous
        self.env.stop()
        self.tmp.cleanup()

    def create(self, **kwargs):
        return self.client.post('/api/rulesets', json=dict(name='example', type='inline', behavior='domain', payload=['+.example.com'], **kwargs))

    def test_authenticated_crud_and_restart_does_not_restore_deleted_optional_set(self):
        self.client.cookies.clear()
        for method, path in [('get','/api/rulesets'), ('post','/api/rulesets'), ('put','/api/rulesets/example'), ('delete','/api/rulesets/example')]:
            self.assertEqual(getattr(self.client, method)(path, **({'json': {}} if method in ('post','put') else {})).status_code, 401)
        self.client.cookies.set('mc_token', db.get_setting('web_token'))
        self.assertEqual(self.create().status_code, 200)
        self.assertEqual(self.create().status_code, 409)
        self.assertEqual(self.client.put('/api/rulesets/example', json={'payload': '+.changed.example'}).status_code, 200)
        self.assertEqual(self.client.put('/api/rulesets/example', json={'name': 'renamed'}).status_code, 400)
        self.assertEqual(self.client.delete('/api/rulesets/example').status_code, 200)
        self.assertEqual(self.client.delete('/api/rulesets/google').status_code, 200)
        db.init_db()
        self.assertNotIn('google', [x['name'] for x in self.client.get('/api/rulesets').json()])
        self.assertEqual(self.client.delete('/api/rulesets/missing').status_code, 404)

    def test_references_protect_disabled_rules_and_base_sets(self):
        self.create()
        group = db.list_groups()[0]
        response = self.client.post('/api/rules', json=dict(name='Example', group_id=group['id'], rule_sets=['example'], enabled=False))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.delete('/api/rulesets/example').status_code, 409)
        for name in rulesets.BASE:
            self.assertEqual(self.client.delete('/api/rulesets/' + name).status_code, 409)
        self.assertEqual(self.client.put('/api/rulesets/cncidr', json={'format': 'mrs'}).status_code, 400)

    def test_validation_and_failed_apply_leave_database_unchanged(self):
        before = db.get_setting('rulesets')
        for invalid in [dict(name='../path'), dict(name='<img>'), dict(type='file'), dict(format='mrs',behavior='classical'),
                        dict(type='http',url='file:///etc/passwd'), dict(type='http',url='https://' + 'user:pass' + '@example.com'),
                        dict(behavior='ipcidr',payload=['invalid-cidr']), dict(behavior='classical',payload=['invalid']), dict(interval=True), dict(interval=0), dict(payload=[]), dict(payload=[5])]:
            payload = dict(name='example', type='inline', behavior='domain', payload=['+.example.com'])
            payload.update(invalid)
            self.assertEqual(self.client.post('/api/rulesets', json=payload).status_code, 400, invalid)
        self.assertEqual(db.get_setting('rulesets'), before)
        with patch.object(service, 'apply_config', side_effect=RuntimeError('private upstream detail')):
            response = self.create()
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('private upstream detail', response.text)
        self.assertEqual(db.get_setting('rulesets'), before)

    def test_custom_set_rendering_and_dns_are_consistent(self):
        self.create()
        group = db.list_groups()[0]
        db.save_rule(dict(name='New',domains='',rule_sets='example',group_id=group['id'],priority=5,enabled=1))
        settings = {'rulesets': db.get_setting('rulesets')}
        config = yaml.safe_load(render_config([], db.list_groups(), [], settings, db.list_rules()))
        self.assertEqual(config['rule-providers']['example']['payload'], ['+.example.com'])
        self.assertIn(f"RULE-SET,example,{group['name']}", config['rules'])
        from tests.test_router import example
        settings['router_config'] = db.dump_json(example())
        config = yaml.safe_load(render_config([], db.list_groups(), [], settings, db.list_rules()))
        self.assertIn('RULE-SET,example,fake-ip', config['dns']['fake-ip-filter'])

    def test_provider_cache_changes_with_source_and_formats(self):
        item = rulesets.defaults()[0]
        old = rulesets.provider(item)['path']
        changed = dict(item,url='https://example.com/new.yaml')
        self.assertNotEqual(old, rulesets.provider(changed)['path'])
        self.assertEqual(rulesets.provider(dict(changed,interval=60))['path'], rulesets.provider(changed)['path'])


class RuleEntryTest(unittest.TestCase):
    def setUp(self):
        RulesetsTest.setUp(self)
        from pathlib import Path
        self.directory = Path(self.tmp.name) / 'core/config'
        (self.directory / 'ruleset').mkdir(parents=True)
        self.source = self.directory / 'ruleset/reject.yaml'
        self.source.write_text('payload:\n  - +.siftscience.com\n  - +.keep.example\n  - +.othersiftscience.com\n')

    def tearDown(self):
        RulesetsTest.tearDown(self)

    def test_paginated_search_delete_restore_and_restart_persistence(self):
        path = '/api/rulesets/reject/entries'
        self.assertEqual(self.client.get(path+'?query=siftscience').json()['matched'], 2)
        before = self.source.read_bytes()
        self.assertEqual(self.client.post(path, json={'entry':'siftscience.com'}).status_code, 200)
        result = self.client.get(path).json()
        self.assertEqual(result['entries'], ['+.keep.example', '+.othersiftscience.com'])
        self.assertEqual(result['excluded'], ['+.siftscience.com'])
        self.assertEqual(self.source.read_bytes(), before)
        db.init_db()
        self.assertEqual(self.client.get(path).json()['total'], 2)
        self.assertEqual(self.client.post(path, json={'entry':'+.siftscience.com','restore':True}).status_code, 200)
        self.assertEqual(self.client.get(path).json()['total'], 3)

    def test_failure_rolls_back_exclusions_and_requires_authentication(self):
        path = '/api/rulesets/reject/entries'
        before = db.get_setting('rulesets')
        with patch.object(service, 'apply_config', side_effect=RuntimeError('invalid core')):
            self.assertEqual(self.client.post(path, json={'entry':'siftscience.com'}).status_code, 400)
        self.assertEqual(db.get_setting('rulesets'), before)
        self.assertEqual(self.client.post(path, json={'entry':'not-present.example'}).status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post(path,json={'entry':'siftscience.com'}).status_code, 401)

    def test_upstream_updates_retain_exclusions_and_old_snapshot(self):
        from pathlib import Path
        self.client.post('/api/rulesets/reject/entries',json={'entry':'siftscience.com'})
        items = rulesets.load(db.get_setting('rulesets'))
        with patch('shutil.chown'):
            paths = rulesets.prepare_overrides(items,self.directory)
        old_path = self.directory / paths['reject']
        old_bytes = old_path.read_bytes()
        self.assertNotIn('+.siftscience.com', yaml.safe_load(old_bytes)['payload'])
        self.assertIn('+.othersiftscience.com', yaml.safe_load(old_bytes)['payload'])
        self.source.write_text('payload:\n  - +.siftscience.com\n  - +.new.example\n')
        with patch('shutil.chown'):
            updated = rulesets.prepare_overrides(items,self.directory)
        self.assertNotEqual(updated,paths)
        self.assertEqual(old_path.read_bytes(),old_bytes)
        self.assertEqual(yaml.safe_load((self.directory/updated['reject']).read_text())['payload'],['+.new.example'])
        settings = {'rulesets':db.get_setting('rulesets'),'rule_provider_paths':updated}
        config=yaml.safe_load(render_config([],db.list_groups(),[],settings,db.list_rules()))
        self.assertEqual(config['rule-providers']['reject']['type'],'file')
        self.assertEqual(config['rule-providers']['__console_source_reject']['type'],'http')
        self.assertNotIn('__console_source_reject', '\n'.join(config['rules']))

    def test_text_inline_empty_and_invalid_entries(self):
        item=rulesets.clean(dict(name='example',type='http',url='https://example.com/rules.txt',format='text',behavior='domain',excluded=['+.gone.example']))
        self.assertEqual(rulesets.filtered(item,rulesets.parse_payload('# comment\n+.gone.example\n+.keep.example\n','text')),['+.keep.example'])
        self.assertEqual(rulesets.filtered(item,['+.gone.example']),[])
        self.assertEqual(self.client.get('/api/rulesets/reject/entries?page=0').status_code,400)
        self.assertEqual(self.client.get('/api/rulesets/missing/entries').status_code,404)
