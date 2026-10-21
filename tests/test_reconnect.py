import sys, tempfile, unittest, yaml
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import service, render

class ReconnectRegression(unittest.TestCase):
    def rendered(self, strategy, cooled):
        subs=[dict(id=1,enabled=1,url='https://example.invalid/sub',interval_hours=6)]
        group=dict(id=3,name='ChatGPT',strategy=strategy,enabled=1,is_default=1,selected_nodes=[dict(provider='sub1',name='node-a')])
        return yaml.safe_load(render.render_config(subs,[group],cooled,{},[]))['proxy-groups'][0]

    def test_fallback_keeps_membership_during_cooldown(self):
        a=self.rendered('fallback',[])
        b=self.rendered('fallback',['node-a','unused'])
        self.assertEqual(a,b)
        self.assertFalse(b['interrupt-exist-connections'])
        self.assertEqual(b['empty-fallback'],'REJECT')

    def test_url_test_uses_native_health(self):
        self.assertEqual(self.rendered('url-test',[]),self.rendered('url-test',['node-a']))

    def test_manual_select_retains_cooldown_exclusion(self):
        self.assertIn('node',self.rendered('select',['node-a'])['exclude-filter'])

    def test_cooldown_order_is_stable(self):
        self.assertEqual(render.cooldown_regex(['b','a','a']),render.cooldown_regex(['a','b']))

    def test_identical_active_config_skips_validation_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.yaml'
            p.write_text('unchanged\n')
            with patch.object(service,'config_lock',return_value=nullcontext()), \
                 patch.object(service,'config_path',return_value=p), \
                 patch.object(service,'render_config',return_value='unchanged\n'), \
                 patch.object(service,'core_active',return_value=True), \
                 patch.object(service.db,'list_subscriptions',return_value=[]), \
                 patch.object(service.db,'list_groups',return_value=[]), \
                 patch.object(service.db,'list_rules',return_value=[]), \
                 patch.object(service.db,'list_cooldowns',return_value=[]), \
                 patch.object(service.db,'get_setting',return_value=''), \
                 patch.object(service.subprocess,'run') as run, \
                 patch.object(service,'reload_core') as reload, \
                 patch.object(service.os,'replace') as replace:
                result=service.apply_config()
                self.assertFalse(result['changed'])
                run.assert_not_called()
                reload.assert_not_called()
                replace.assert_not_called()

    def test_health_reports_no_reload_for_unchanged_config(self):
        payload={'providers':{'sub1':{'proxies':[{'name':'node-a','history':[{'time':'2026-10-22T02:00:00+08:00','delay':0}]}]}}}
        http=MagicMock()
        http.__enter__.return_value.get.return_value.json.return_value=payload
        with patch.object(service,'core_active',return_value=True), \
             patch.object(service,'client',return_value=http), \
             patch.object(service.db,'list_subscriptions',return_value=[{'id':1,'enabled':1}]), \
             patch.object(service.db,'list_node_health',return_value=[{'node_name':'node-a','fails':1,'checked_at':'old'}]), \
             patch.object(service.db,'list_cooldowns',return_value=[]), \
             patch.object(service.db,'get_setting',side_effect=lambda k,d=None: None if k=='router_config' else d), \
             patch.object(service.db,'upsert_node_health'), patch.object(service.db,'replace_cooldowns'), \
             patch.object(service,'node_catalog',return_value=[]), patch.object(service,'migrate_group_nodes'), \
             patch.object(service,'apply_config',return_value={'changed':False}) as apply:
            result=service.collect_health()
            self.assertFalse(result['reloaded'])
            self.assertEqual(result['cooldown'],1)
            apply.assert_called_once()

if __name__=='__main__': unittest.main(verbosity=2)
