import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from studio.server import Studio, Handler, write_config, atomic_json
from studio.access import Access
from studio.isolation import isolated_command, clean_environment


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve();project=self.root/'project';project.mkdir()
        config=self.root/'agent.toml';write_config(config,{'local':{'model':'test','auth':'none','protocol':'chat_completions','base_url':'http://127.0.0.1:1/v1'}},'local')
        self.studio=Studio(project,self.root/'state',config);self.addCleanup(self.studio.missions.close);self.addCleanup(self.studio.oauth.close)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.studio=self.studio
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base=f'http://127.0.0.1:{self.server.server_port}';self.key=(self.root/'state/access-key').read_text().strip()

    def test_anonymous_cannot_read_state_files_or_mutate(self):
        for path in ['/api/state','/api/companies','/api/file?project=x&path=y','/api/log?run=x']:
            with self.assertRaises(urllib.error.HTTPError) as cm:urllib.request.urlopen(self.base+path)
            self.assertEqual(cm.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(urllib.request.Request(self.base+'/api/projects',data=b'{"path":"/"}',headers={'Content-Type':'application/json'}))
        self.assertEqual(cm.exception.code,401)
        with urllib.request.urlopen(self.base+'/') as r:self.assertIn(b'Sign in to Miner',r.read())

    def test_login_cookie_and_bearer_work_and_csrf_still_required(self):
        req=urllib.request.Request(self.base+'/auth/login',data=json.dumps({'key':self.key}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req) as r:
            cookie=r.headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie);self.assertNotIn(self.key,r.read().decode())
        for headers in [{'Cookie':cookie.split(';')[0]},{'Authorization':'Bearer '+self.key}]:
            with urllib.request.urlopen(urllib.request.Request(self.base+'/api/state',headers=headers)) as r:data=json.load(r)
            self.assertIn('token',data)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(urllib.request.Request(self.base+'/api/projects',data=b'{}',headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'}))
        self.assertEqual(cm.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(urllib.request.Request(self.base+'/api/state',headers={'Authorization':'Bearer '+self.key,'Origin':'https://evil.invalid'}))
        self.assertEqual(cm.exception.code,403)

    def test_invalid_key_is_rate_limited_and_keys_are_private(self):
        a=self.studio.access
        for _ in range(5):self.assertEqual(a.login('wrong','fixture')[1],401)
        self.assertEqual(a.login('wrong','fixture')[1],429)
        previous=os.umask(0o022)
        try:atomic_json(self.root/'state'/'test.json',{'synthetic':'task'})
        finally:os.umask(previous)
        self.assertEqual(stat.S_IMODE((self.root/'state').stat().st_mode),0o700)
        for p in ['access-key','test.json']:self.assertEqual(stat.S_IMODE((self.root/'state'/p).stat().st_mode),0o600)
        self.assertTrue(Access(self.root/'state').valid_key(self.key))

    def test_symlink_key_is_rejected(self):
        state=self.root/'other';state.mkdir();target=self.root/'target';target.write_text('x'*43)
        (state/'access-key').symlink_to(target)
        with self.assertRaises(OSError):Access(state)

    def test_https_login_and_preview_use_secure_cookies(self):
        import ssl
        import http.cookiejar
        cert=self.root/'cert.pem';key=self.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1',
            '-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.studio=self.studio;server.tls=True
        server.socket=context.wrap_socket(server.socket,server_side=True)
        self.studio.preview.ssl_context=context
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown);self.addCleanup(self.studio.preview.stop)
        base=f'https://127.0.0.1:{server.server_port}'
        browser=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        login=urllib.request.Request(base+'/auth/login',data=json.dumps({'key':self.key}).encode(),headers={'Content-Type':'application/json','Origin':base})
        with browser.open(login) as response:self.assertIn('Secure',response.headers['Set-Cookie'])
        with browser.open(base+'/api/state') as response:state=json.load(response)
        (self.root/'project/index.html').write_text('<h1>TLS preview</h1>')
        request=urllib.request.Request(base+'/api/preview/start',data=json.dumps({'project':next(iter(self.studio.projects)),'entry':'index.html'}).encode(),headers={'Content-Type':'application/json','Origin':base,'X-Studio-Token':state['token']})
        with browser.open(request) as response:preview=json.load(response)
        self.assertTrue(preview['url'].startswith('https://'))
        with browser.open(preview['url']) as response:self.assertIn(b'TLS preview',response.read())


class IsolationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='linux','Linux bubblewrap integration')
    def test_untrusted_code_cannot_read_host_secret_controller_key_or_environment(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);project=root/'project';project.mkdir();secret=root/'secret';secret.write_text('synthetic')
            code='import os,pathlib,json; print(json.dumps({"secret":pathlib.Path('+repr(str(secret))+').exists(),"ssh":pathlib.Path.home().joinpath(".ssh").exists(),"env":"MINER_SECRET" in os.environ}));pathlib.Path("out.txt").write_text("OK")'
            p=subprocess.run(isolated_command([sys.executable,'-c',code],project),env=clean_environment(dict(os.environ,MINER_SECRET='synthetic')),capture_output=True,text=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(json.loads(p.stdout),{'secret':False,'ssh':False,'env':False})
            self.assertEqual((project/'out.txt').read_text(),'OK')

    def test_credentials_are_not_inherited(self):
        self.assertNotIn('OPENAI_API_KEY',clean_environment({'OPENAI_API_KEY':'synthetic','PATH':'/usr/bin'}))
        self.assertNotIn('SSH_AUTH_SOCK',clean_environment({'SSH_AUTH_SOCK':'synthetic'}))

    @unittest.skipUnless(sys.platform=='linux','Linux bubblewrap integration')
    def test_nested_controller_state_is_hidden_and_only_current_run_is_mounted(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d);state=project/'state';state.mkdir();run=state/'run';run.mkdir()
            (state/'access-key').write_text('synthetic-owner-key')
            code='from pathlib import Path; assert not Path("state/access-key").exists();Path("state/run/result").write_text("ok")'
            p=subprocess.run(isolated_command([sys.executable,'-c',code],project,protected=[state],writable=[run]),capture_output=True,text=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual((run/'result').read_text(),'ok')

    def test_missing_sandbox_fails_closed(self):
        from unittest.mock import patch
        with patch('studio.isolation.shutil.which',return_value=None):
            with self.assertRaises(RuntimeError):isolated_command(['true'],Path.cwd())
