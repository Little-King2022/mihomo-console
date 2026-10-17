import os
import tempfile
import unittest
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="mihomo-console-test-")

from app.db import AI_DOMAINS, add_subscription, init_db, list_groups, list_subscriptions
from app.render import render_config


class RenderTest(unittest.TestCase):
    def test_seed_config_contains_ai_and_regular_groups(self):
        init_db()
        if not list_subscriptions():
            add_subscription("演示订阅", "https://subscription.example.com/sub", 6, True)
        text = render_config(
            list_subscriptions(),
            list_groups(),
            ["坏节点|A"],
            {"mode": "rule", "tun": "1", "mihomo_secret": "secret"},
        )
        self.assertIn("mode: rule", text)
        self.assertIn("enable: true", text)
        self.assertIn("name: \"台湾家宽\"", text)
        self.assertIn("name: \"境外常规\"", text)
        self.assertIn("type: fallback", text)
        self.assertIn("RULE-SET,openai,台湾家宽", text)
        self.assertIn("DOMAIN-SUFFIX,chatgpt.com,台湾家宽", text)
        self.assertIn("DOMAIN-SUFFIX,grok.com,台湾家宽", text)
        self.assertIn("MATCH,境外常规", text)
        self.assertIn("subscription.example.com,DIRECT", text)
        self.assertNotIn("additional-prefix:", text)
        self.assertIn("坏节点", text)
        self.assertIn("10.64.0.134", text)
        self.assertIn(AI_DOMAINS.splitlines()[0], text)
        self.assertNotIn("proxies:\n- name:", text)

    def test_explicit_node_names_are_literal_and_follow_subscription_prefixes(self):
        import re
        import json
        from app.render import selected_node_filter
        selected = [{'provider':'sub1','name':'A|B (fast).+'}]
        one = [{'id':1, 'enabled':1, 'url':'https://example.com/sub'}]
        two = one + [{'id':2, 'enabled':1, 'url':'https://second.example/sub'}]
        self.assertTrue(re.fullmatch(selected_node_filter(selected, one), 'A|B (fast).+'))
        self.assertFalse(re.fullmatch(selected_node_filter(selected, one), 'A'))
        self.assertTrue(re.fullmatch(selected_node_filter(selected, two), 's1-A|B (fast).+'))
        self.assertFalse(re.fullmatch(selected_node_filter(selected, two), 's2-A|B (fast).+'))
        group = dict(id=9, name='Explicit', enabled=1, is_default=1, strategy='fallback',
                     selected_nodes=selected, node_filter='legacy', exclude_filter='legacy-exclude')
        text = render_config(two, [group], ['s1-A|B (fast).+'], {}, [])
        self.assertIn('  use:\n    - sub1', text)
        self.assertIn('  proxies: []', text)
        self.assertIn('  empty-fallback: REJECT', text)
        self.assertNotIn('legacy', text)
        self.assertNotIn('  - DIRECT', text)
        self.assertEqual(selected_node_filter(selected, [two[1]]), '^$')
        group['selected_nodes'] = []
        text = render_config(two, [group], [], {}, [])
        self.assertIn('  filter: "^$"', text)
        self.assertIn('  use:\n    - sub1', text)
        text = render_config([], [group], [], {}, [])
        self.assertIn('  console-empty:', text)
        self.assertIn('    - console-empty', text)
        self.assertIn('  filter: "^$"', text)

    def test_direct_mode_and_tun_off(self):
        init_db()
        text = render_config(list_subscriptions(), list_groups(), [], {"mode": "direct", "tun": "0", "mihomo_secret": "s"})
        self.assertIn("mode: direct", text)
        self.assertIn("enable: false", text)


if __name__ == "__main__":
    unittest.main()
