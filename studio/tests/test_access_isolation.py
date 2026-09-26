import http.client
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from studio.server import ROOT, Studio, Handler, write_config, atomic_json
from studio.access import Access
from studio.isolation import isolated_command, clean_environment
from studio.tls_server import ThreadingTLSServer
from studio.tests.sandbox_support import requires_sandbox


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
        with urllib.request.urlopen(self.base+'/') as r:self.assertIn(b'id="login"',r.read())

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

    def tls_server(self):
        cert=self.root/'cert.pem';key=self.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1',
            '-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        server=ThreadingTLSServer(('127.0.0.1',0),Handler);server.studio=self.studio;server.tls=True
        server.ssl_context=context  # Same wiring as main(): the handshake runs per connection.
        self.studio.preview.ssl_context=context
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown);self.addCleanup(self.studio.preview.stop)
        return server,cert

    def test_https_login_and_preview_use_secure_cookies(self):
        server,cert=self.tls_server()
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

    def test_idle_tls_client_blocks_neither_studio_nor_preview_shutdown(self):
        server,cert=self.tls_server()
        client=ssl.create_default_context(cafile=str(cert))
        idle=socket.create_connection(('127.0.0.1',server.server_port));self.addCleanup(idle.close)
        started=time.monotonic()
        request=urllib.request.Request(f'https://127.0.0.1:{server.server_port}/api/state',headers={'Authorization':'Bearer '+self.key})
        with urllib.request.urlopen(request,context=client,timeout=5) as response:self.assertIn('token',json.load(response))
        self.assertLess(time.monotonic()-started,3)
        (self.root/'project/index.html').write_text('<h1>TLS preview</h1>')
        preview=self.studio.preview.start({'project':next(iter(self.studio.projects)),'entry':'index.html'},server.server_port)
        port=urllib.parse.urlsplit(preview['url']).port
        stalled=socket.create_connection(('127.0.0.1',port));self.addCleanup(stalled.close)
        browser=urllib.request.build_opener(urllib.request.HTTPSHandler(context=client),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with browser.open(preview['url'],timeout=5) as response:self.assertIn(b'TLS preview',response.read())
        started=time.monotonic();self.studio.preview.stop()
        self.assertLess(time.monotonic()-started,3)
        self.assertFalse(self.studio.preview.status()['running'])

    def test_full_session_table_evicts_oldest_and_logout_revokes(self):
        a=self.studio.access
        first,_=a.login(self.key,'owner')
        for i in range(140):self.assertEqual(a.login(self.key,f'198.51.100.{i%200}')[1],200)
        self.assertLessEqual(len(a.sessions),128)
        self.assertFalse(a.authenticated({'Cookie':'miner_session='+first}))
        session,_=a.login(self.key,'owner')
        self.assertTrue(a.authenticated({'Cookie':'miner_session='+session}))
        headers={'Cookie':'miner_session='+session,'Content-Type':'application/json','X-Studio-Token':self.studio.token}
        with urllib.request.urlopen(urllib.request.Request(self.base+'/auth/logout',data=b'{}',headers=headers)) as r:
            self.assertIn('Max-Age=0',r.headers['Set-Cookie'])
        self.assertFalse(a.authenticated({'Cookie':'miner_session='+session}))
        other,_=a.login(self.key,'owner');token=self.studio.token
        request=urllib.request.Request(self.base+'/auth/logout',data=b'{"everywhere":true}',headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json','X-Studio-Token':token})
        with urllib.request.urlopen(request):pass
        self.assertFalse(a.authenticated({'Cookie':'miner_session='+other}))
        self.assertNotEqual(self.studio.token,token)

    def test_login_errors_are_specific(self):
        def login(key):
            request=urllib.request.Request(self.base+'/auth/login',data=json.dumps({'key':key}).encode(),headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as cm:urllib.request.urlopen(request)
            return cm.exception.code,json.load(cm.exception)['error']
        for _ in range(5):self.assertEqual(login('wrong'),(401,'Access key was not accepted.'))
        status,message=login('wrong');self.assertEqual(status,429);self.assertIn('Too many sign-in attempts',message)

    def test_early_errors_on_large_bodies_are_delivered_not_reset(self):
        body=b'{"content":"'+b'a'*1_500_000+b'"}'
        for headers,status in [({'Cookie':'miner_session=stale'},401),({'Authorization':'Bearer '+self.key,'X-Studio-Token':'wrong'},403)]:
            for _ in range(3):
                connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
                try:
                    connection.request('POST','/api/file',body=body,headers={**headers,'Content-Type':'application/json'})
                    self.assertEqual(connection.getresponse().status,status)
                finally:connection.close()

    def test_malformed_bodies_and_internal_errors_return_json(self):
        auth={'Authorization':'Bearer '+self.key,'X-Studio-Token':self.studio.token,'Content-Type':'application/json'}
        def call(path,data=None,headers=auth):
            with self.assertRaises(urllib.error.HTTPError) as cm:urllib.request.urlopen(urllib.request.Request(self.base+path,data=data,headers=headers))
            return cm.exception.code,json.load(cm.exception)['error']
        for data in (b'[1]',b'null',b'"text"'):
            for path in ('/api/preview/stop','/api/models','/api/browser-pilot'):
                self.assertEqual(call(path,data),(400,'JSON object expected.'))
        self.assertEqual(call('/api/preview/stop',b''),(400,'Request body is empty.'))
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        try:
            connection.putrequest('POST','/api/preview/stop')
            for k,v in auth.items():connection.putheader(k,v)
            connection.endheaders();self.assertEqual(connection.getresponse().status,411)
        finally:connection.close()
        with patch.object(self.studio,'public_state',side_effect=AttributeError('synthetic')),patch('studio.server.traceback.print_exc'):
            self.assertEqual(call('/api/state',headers={'Authorization':'Bearer '+self.key}),(500,'Internal error; see the Studio log.'))
        import sqlite3
        with patch.object(self.studio,'public_state',side_effect=sqlite3.OperationalError('database is locked')):
            self.assertEqual(call('/api/state',headers={'Authorization':'Bearer '+self.key})[0],503)
        self.studio.runs['busy']={'id':'busy','project':'x','status':'running','created':time.time()}
        try:
            frame={'query':'','filtered':False,'visibleItems':['Blue desk']}
            self.assertEqual(call('/api/browser-pilot',json.dumps({'frame':frame}).encode())[0],409)
        finally:self.studio.runs.pop('busy')

    def test_stalled_request_is_timed_out(self):
        with patch.object(Handler,'timeout',0.5):
            stalled=socket.create_connection(('127.0.0.1',self.server.server_port));self.addCleanup(stalled.close)
            stalled.sendall(f'POST /auth/login HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{{"key"'.encode())
            stalled.settimeout(10)
            self.assertIn(b' 408 ',stalled.recv(4096))

    def test_project_registration_rejects_controller_state_application_and_home(self):
        auth={'Authorization':'Bearer '+self.key,'X-Studio-Token':self.studio.token,'Content-Type':'application/json'}
        def register(path):
            request=urllib.request.Request(self.base+'/api/projects',data=json.dumps({'path':str(path)}).encode(),headers=auth)
            try:
                with urllib.request.urlopen(request) as r:return r.status
            except urllib.error.HTTPError as exc:return exc.code
        (self.root/'state'/'runs').mkdir(exist_ok=True)
        for path in [self.root/'state',self.root/'state'/'runs',self.root,ROOT,ROOT/'studio',Path.home(),Path('/')]:
            with self.subTest(path=path):self.assertEqual(register(path),400)
        self.assertNotIn(str((self.root/'state').resolve()),{p['path'] for p in self.studio.projects.values()})
        other=self.root/'other-project';other.mkdir()
        self.assertEqual(register(other),200)
        # Controller-managed workspaces inside the state directory stay registrable internally.
        workspace=self.root/'state'/'workspaces'/'mission';workspace.mkdir(parents=True)
        self.assertEqual(self.studio.add_project(str(workspace))['path'],str(workspace.resolve()))


class IsolationTests(unittest.TestCase):
    @requires_sandbox
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

    @requires_sandbox
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
