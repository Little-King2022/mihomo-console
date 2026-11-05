"""Regression coverage for removal of the legacy balancing feature."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from app import db, service
from app.main import app
from app.render import render_config


class BalanceRemovalTest(unittest.TestCase):
    def setUp(self):
        self.previous = db._conn
        db._conn = None
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name, MIHOMO_HOME=self.tmp.name + '/core')
        self.env.start()
        db.init_db()
        self.client = TestClient(app)
        self.client.cookies.set('mc_token', db.get_setting('web_token'))
        self.primary = [dict(provider='sub1', name='台湾 A'), dict(provider='sub2', name='日本 B')]
        self.ident = db.add_group(dict(name='Legacy', strategy='load-balance', selected_nodes=self.primary,
                                      balance_config={'backup_nodes': [dict(provider='sub3', name='Backup')]}))

    def tearDown(self):
        self.client.close()
        db._conn.close()
        db._conn = self.previous
        self.env.stop()
        self.tmp.cleanup()

    def test_migration_preserves_identity_rules_and_pools_and_is_idempotent(self):
        before = db.get_group(self.ident)
        rules = db.list_rules()
        with patch.object(service, 'apply_config') as apply:
            service.migrate_load_balance()
            service.migrate_load_balance()
            apply.assert_called_once()
        after = db.get_group(self.ident)
        self.assertEqual(after, dict(before, strategy='fallback'))
        self.assertEqual(db.list_rules(), rules)
        subscriptions = [dict(id=i, enabled=1, url=f'https://example.com/{i}') for i in (1, 2, 3)]
        config = yaml.safe_load(render_config(subscriptions, [after], [], {}, []))
        group = config['proxy-groups'][0]
        self.assertEqual(group['type'], 'fallback')
        self.assertEqual(group['proxies'], [])
        self.assertEqual(group['use'], ['sub1', 'sub2'])
        self.assertEqual(group['empty-fallback'], 'REJECT')
        self.assertNotIn('Backup', group['filter'])
        self.assertNotIn('__console_lb_', str(config))

    def test_failed_migration_rolls_back_database(self):
        before = copy.deepcopy(db.get_group(self.ident))
        with patch.object(service, 'apply_config', side_effect=RuntimeError('validation failed')):
            with self.assertRaises(RuntimeError):
                service.migrate_load_balance()
        self.assertEqual(db.get_group(self.ident), before)

    def test_removed_routes_and_strategy_cannot_be_used(self):
        self.assertEqual(self.client.get('/api/balance').status_code, 404)
        self.assertEqual(self.client.post('/api/balance/health').status_code, 404)
        with patch.object(service, 'apply_config'):
            self.assertEqual(self.client.post('/api/groups', json=dict(name='New', strategy='load-balance')).status_code, 400)
