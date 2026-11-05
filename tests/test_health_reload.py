import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch
from app import service


class HealthReloadTest(unittest.TestCase):
    def test_new_failure_and_cooldown_do_not_reload_automatic_groups(self):
        response = MagicMock()
        response.json.return_value = {'providers': {'sub1': {'proxies': [
            {'name': 'A', 'history': [{'time': '2026-11-05T10:00:00Z', 'delay': 0}]}
        ]}}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(service, 'core_active', return_value=True))
            client = stack.enter_context(patch.object(service, 'client'))
            client.return_value.__enter__.return_value.get.return_value = response
            for name, value in {
                'list_subscriptions': [{'id': 1, 'enabled': 1}],
                'list_node_health': [{'node_name': 'A', 'fails': 1, 'checked_at': 'old'}],
                'list_cooldowns': [],
                'list_groups': [{'strategy': 'url-test', 'enabled': 1}],
            }.items():
                stack.enter_context(patch.object(service.db, name, return_value=value))
            stack.enter_context(patch.object(service.db, 'get_setting', side_effect=lambda key, default=None: default))
            stack.enter_context(patch.object(service.db, 'upsert_node_health'))
            cooldown = stack.enter_context(patch.object(service.db, 'replace_cooldowns'))
            stack.enter_context(patch.object(service, 'node_catalog', return_value=[]))
            stack.enter_context(patch.object(service, 'migrate_group_nodes'))
            apply = stack.enter_context(patch.object(service, 'apply_config'))
            result = service.collect_health()
            self.assertEqual(result['cooldown'], 1)
            self.assertFalse(result['reloaded'])
            self.assertEqual(cooldown.call_args.args[0][0]['node_name'], 'A')
            apply.assert_not_called()
