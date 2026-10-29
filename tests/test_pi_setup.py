"""Exercise Pi setup sequencing in a temporary directory with fake CLI tools.

These tests never invoke Docker, install packages, alter ownership or contact
devices. A PTY supplies the script's required interactive terminal.
"""
from pathlib import Path
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PiSetupTests(unittest.TestCase):
    def run_fixture(self, *, architecture="aarch64", certificate_failure=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, binaries = root / "application", root / "bin"
            (project / "scripts").mkdir(parents=True)
            binaries.mkdir()
            shutil.copy2(ROOT / "scripts" / "setup-pi.sh", project / "scripts" / "setup-pi.sh")
            log = root / "docker-calls.jsonl"
            tools = {
                "uname": f'#!/bin/sh\nif [ "$1" = -s ]; then echo Linux; else echo {architecture}; fi\n',
                "id": '#!/bin/sh\necho 0\n',
                "getconf": '#!/bin/sh\necho 64\n',
                "ip": '#!/bin/sh\necho "192.0.2.1 via 192.168.1.1 dev eth0 src 192.168.1.20"\n',
                "sleep": '#!/bin/sh\nexit 0\n',
                "sha256sum": '#!/bin/sh\necho "fixture-sha256  homebase-rootCA.crt"\n',
                "install": f'''#!{sys.executable}
from pathlib import Path
import shutil,sys
args=sys.argv[1:]
if '-d' in args:
    for folder in args[-2:]: Path(folder).mkdir(parents=True,exist_ok=True)
else:
    shutil.copyfile(args[-2],args[-1])
''',
                "docker": f'''#!{sys.executable}
from pathlib import Path
import json,os,sys
args=sys.argv[1:]
with open(os.environ['SETUP_FIXTURE_LOG'],'a') as stream: stream.write(json.dumps(args)+'\\n')
if 'app.lan_setup' in args:
    if os.environ.get('SETUP_FIXTURE_FAIL_CERT') == '1': sys.exit(9)
    for index,arg in enumerate(args):
        if arg == '--volume' and args[index+1].endswith(':/certs'):
            folder=Path(args[index+1][:-len(':/certs')])
            for name in ('server.crt','server.key','homebase-rootCA.crt'): (folder/name).write_text('fixture')
    print(json.dumps({{'hostname':'homebase-12345678.local','url':'https://homebase-12345678.local:8420'}}))
''',
            }
            for name, source in tools.items():
                destination = binaries / name
                destination.write_text(source)
                destination.chmod(0o755)
            env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                       SETUP_FIXTURE_LOG=str(log), SETUP_FIXTURE_FAIL_CERT="1" if certificate_failure else "0")
            master, slave = pty.openpty()
            process = subprocess.Popen(["bash", str(project / "scripts" / "setup-pi.sh")],
                                       stdin=slave, stdout=slave, stderr=slave, env=env)
            os.close(slave)
            output = bytearray()
            try:
                os.write(master, b"192.168.1.20\nadmin\n")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    readable, _, _ = select.select([master], [], [], 0.1)
                    if readable:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                    elif process.poll() is not None:
                        break
                returncode = process.wait(timeout=2)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(master)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return returncode, output.decode(errors="replace"), calls

    def test_account_and_tls_are_ready_before_lan_start(self):
        code, output, calls = self.run_fixture()
        self.assertEqual(code, 0, output)
        admin = next(i for i, call in enumerate(calls) if "app.admin" in call)
        certificates = next(i for i, call in enumerate(calls) if "app.lan_setup" in call)
        startup = next(i for i, call in enumerate(calls) if "up" in call)
        self.assertLess(admin, certificates)
        self.assertLess(certificates, startup)
        self.assertIn("--if-missing", calls[admin])
        self.assertIn("--entrypoint", calls[admin])
        self.assertNotIn("-T", calls[admin])
        self.assertIn("compose.lan.yaml", calls[startup])
        self.assertFalse(any("--user" in call or "--cap-add" in call for call in calls))
        self.assertIn("Dashboard ready: https://192.168.1.20:8420", output)

    def test_certificate_failure_never_starts_lan_listener(self):
        code, output, calls = self.run_fixture(certificate_failure=True)
        self.assertNotEqual(code, 0, output)
        self.assertTrue(any("app.admin" in call for call in calls))
        self.assertFalse(any("up" in call for call in calls))

    def test_32_bit_pi_is_rejected_before_docker(self):
        code, output, calls = self.run_fixture(architecture="armv7l")
        self.assertNotEqual(code, 0)
        self.assertIn("requires a 64-bit", output)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
