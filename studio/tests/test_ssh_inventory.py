import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from studio.ssh_inventory import SSHInventory, InventoryBroker, SECTIONS, execute, ssh_argv, targets_for
from studio.ssh_probe import collect, integration_names, project_files


class SSHInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.target = {'id': 'cloud', 'host': '192.0.2.20', 'port': 22222, 'user': 'operator',
                       'identity_file': str(self.root / 'id_ed25519'), 'missions': ['approved']}
        self.config = self.root / 'ssh-targets.json'
        self.config.write_text(json.dumps({'targets': [self.target]}))
        self.mission = {'id': 'approved', 'phase': 'build'}

    def test_scope_is_explicit_mission_and_build_only(self):
        self.assertEqual(targets_for(self.root, None), [])
        self.assertEqual(targets_for(self.root, {'id': 'other', 'phase': 'build'}), [])
        for phase in ('plan', 'review', 'final'):
            self.assertEqual(targets_for(self.root, {'id': 'approved', 'phase': phase}), [])
        self.assertEqual(targets_for(self.root, self.mission)[0]['port'], 22222)

    def test_unconfigured_studio_has_no_ssh_capability(self):
        self.config.unlink()
        self.assertEqual(targets_for(self.root, self.mission), [])

    def test_rejects_option_and_command_injection_in_operator_config(self):
        for key, value in [('id','../bad'),('host','-oProxyCommand=anything'),('user','operator;sh'),
                           ('port','22222'),('port',0),('identity_file','relative/key')]:
            with self.subTest(key=key):
                self.config.write_text(json.dumps({'targets':[{**self.target,key:value}]}))
                with self.assertRaises(ValueError):
                    targets_for(self.root,self.mission)

    def test_transport_is_noninteractive_verifies_host_key_and_has_no_forwarding(self):
        argv=ssh_argv(self.target,'services')
        for value in ('BatchMode=yes','StrictHostKeyChecking=yes','ForwardAgent=no','ClearAllForwardings=yes',
                      'IdentitiesOnly=yes','PasswordAuthentication=no'):
            self.assertIn(value,argv)
        self.assertEqual(argv[-1],'python3 -I -B - services')
        self.assertIn('22222',argv)
        with self.assertRaises(ValueError):ssh_argv(self.target,'services; touch /tmp/oops')

    def test_schema_has_no_arbitrary_command_or_path_and_uses_target_enum(self):
        inventory=SSHInventory({'cwd':str(self.root),'ssh_targets':[self.target]})
        tool=inventory.tool()
        self.assertEqual(set(tool.parameters['properties']),{'target','section'})
        self.assertEqual(tool.parameters['properties']['target']['enum'],['cloud'])

    def test_model_cannot_select_unconfigured_target_or_read_arbitrary_file(self):
        inventory=SSHInventory({'cwd':str(self.root),'ssh_targets':[self.target]})
        with patch('studio.ssh_inventory.execute',new_callable=AsyncMock) as runner:
            for args in ({'target':'other','section':'system'}, {'target':'cloud','section':'/etc/shadow'}):
                with self.assertRaises(ValueError):asyncio.run(inventory.invoke(**args))
            runner.assert_not_called()

    def test_success_saves_exact_timestamped_evidence_before_return(self):
        inventory=SSHInventory({'cwd':str(self.root),'ssh_targets':[self.target]})
        with patch('studio.ssh_inventory.execute',new_callable=AsyncMock,return_value={'version':1,'section':'system','data':{'hostname':'newweb'}}):
            output=json.loads(asyncio.run(inventory.invoke(target='cloud',section='system')))
        saved=json.loads((self.root/output['evidence_file']).read_text())
        self.assertEqual(saved['data']['hostname'],'newweb')
        self.assertEqual(saved['port'],22222)
        self.assertIn('observed_at',saved)
        self.assertNotIn('identity_file',saved)

    def test_symlink_escape_is_rejected_before_remote_action_or_mkdir(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root/'company').symlink_to(outside,target_is_directory=True)
            inventory=SSHInventory({'cwd':str(self.root),'ssh_targets':[self.target]})
            with patch('studio.ssh_inventory.execute',new_callable=AsyncMock) as runner:
                with self.assertRaises(ValueError):asyncio.run(inventory.invoke(target='cloud',section='system'))
                runner.assert_not_called()
            self.assertFalse((Path(outside)/'ssh-evidence').exists())

    def test_integration_probe_never_returns_env_values_or_package_scripts(self):
        p=self.root/'.env';p.write_text('VAPI_API_KEY=DO_NOT_RETURN\nBUFFER_TOKEN=SECRET\nPASSWORD=OTHER_SECRET\n')
        self.assertEqual(integration_names(p),{'variable_names':['BUFFER_TOKEN','VAPI_API_KEY']})
        p=self.root/'package.json';p.write_text(json.dumps({'scripts':{'start':'secret-command'},'dependencies':{'@vapi-ai/server-sdk':'https://secret@host/pkg'}}))
        result=json.dumps(integration_names(p));self.assertIn('@vapi-ai/server-sdk',result)
        self.assertNotIn('secret',result)

    def test_project_scan_skips_keys_dependencies_and_symlinks(self):
        for directory in ('app','.ssh','node_modules'):
            (self.root/directory).mkdir();(self.root/directory/'package.json').write_text('{}')
        (self.root/'link').symlink_to(self.root/'app',target_is_directory=True)
        with patch('studio.ssh_probe.ROOTS',[str(self.root)]):files,truncated=project_files()
        self.assertEqual(files,[self.root/'app/package.json']);self.assertFalse(truncated)

    def test_ssh_failure_has_no_evidence_and_no_uncontrolled_stderr(self):
        inventory=SSHInventory({'cwd':str(self.root),'ssh_targets':[self.target]})
        with patch('studio.ssh_inventory.execute',new_callable=AsyncMock,side_effect=RuntimeError('Permission denied')):
            with self.assertRaises(RuntimeError):asyncio.run(inventory.invoke(target='cloud',section='system'))
        self.assertEqual(list(self.root.rglob('cloud-*.json')),[])

    def test_transport_rejects_unbounded_output_and_redacts_stderr(self):
        import sys
        for script, expected in [("import sys; sys.stdin.read(); sys.stderr.write('PRIVATE_SECRET'); sys.exit(1)", RuntimeError),
                                 ("import sys; sys.stdin.read(); sys.stdout.write('x'*300000)", ValueError)]:
            with patch('studio.ssh_inventory.ssh_argv', return_value=[sys.executable,'-c',script]):
                with self.assertRaises(expected) as caught:
                    asyncio.run(execute(self.target,'system'))
                self.assertNotIn('PRIVATE_SECRET',str(caught.exception))

    def test_transport_cancellation_terminates_its_process(self):
        import sys
        async def exercise():
            with patch('studio.ssh_inventory.ssh_argv', return_value=[sys.executable,'-c','import sys,time; sys.stdin.read(); time.sleep(30)']):
                task=asyncio.create_task(execute(self.target,'system'))
                await asyncio.sleep(.15)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
        asyncio.run(exercise())

    def test_broker_only_allows_configured_typed_inventory(self):
        import socket
        broker=InventoryBroker([self.target], self.root/'inventory.sock')
        self.addCleanup(broker.close)
        def exchange(body):
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(str(broker.path));client.sendall(json.dumps(body).encode()+b'\n')
                return json.loads(client.recv(10000))
        with patch('studio.ssh_inventory.execute',new_callable=AsyncMock,return_value={'version':1,'section':'system','data':{'hostname':'fixture'}}) as runner:
            for request in [{'target':'other','section':'system'}, {'target':'cloud','section':'system','command':'cat secret'}, {'target':'cloud','section':'/etc/shadow'}]:
                self.assertFalse(exchange(request)['ok'])
            runner.assert_not_called()
            self.assertTrue(exchange({'target':'cloud','section':'system'})['ok'])
            runner.assert_awaited_once()

    def test_unusual_manifests_do_not_abort_the_whole_section(self):
        for content in ('[1, 2]', '"text"', '{"dependencies": ["a"], "devDependencies": {"vapi-sdk": "1"}}'):
            (self.root/'package.json').write_text(content)
            result = integration_names(self.root/'package.json')
            self.assertIsInstance(result['dependency_names'], list, content)
        self.assertEqual(result['integration_hints'], ['vapi-sdk'])
        (self.root/'app').mkdir();(self.root/'app'/'package.json').write_text('[]')
        with patch('studio.ssh_probe.ROOTS',[str(self.root)]):
            rows = collect('integrations')['files']
        self.assertEqual({row['path'] for row in rows}, {str(self.root/'package.json'), str(self.root/'app'/'package.json')})

    def test_system_section_works_without_proc_meminfo(self):
        original = Path.read_text
        def read_text(path, *args, **kwargs):
            if str(path) == '/proc/meminfo':
                raise FileNotFoundError(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read_text):
            system = collect('system')
        self.assertIsNone(system['memory_kib'])
        self.assertIn('hostname', system)

    @unittest.skipUnless(sys.platform == 'linux', 'long Unix socket paths use /proc/self/fd on Linux')
    def test_broker_works_in_a_deep_state_directory(self):
        import socket
        deep = self.root / ('state-directory-with-a-long-name-' * 3) / 'runs' / '0123456789abcdef'
        deep.mkdir(parents=True)
        self.assertGreater(len(str(deep / 'inventory.sock')), 110)
        broker = InventoryBroker([self.target], deep / 'inventory.sock')
        self.addCleanup(broker.close)
        self.assertTrue((deep / 'inventory.sock').is_socket())
        inventory = SSHInventory({'cwd': str(self.root), 'ssh_targets': [self.target], 'inventory_broker': str(deep / 'inventory.sock')})
        with patch('studio.ssh_inventory.execute', new_callable=AsyncMock, return_value={'version':1,'section':'system','data':{'hostname':'deep'}}):
            output = json.loads(asyncio.run(inventory.invoke(target='cloud', section='system')))
        self.assertEqual(output['data']['hostname'], 'deep')

