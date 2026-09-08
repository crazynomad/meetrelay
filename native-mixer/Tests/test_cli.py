"""CLI contract/session control tests. No microphone, Tap or audio device is started."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import uuid

CLI = Path(__file__).resolve().parents[1] / 'build/meeting-bridge'


def command(*args):
    result = subprocess.run([str(CLI), *args], capture_output=True, text=True, timeout=12)
    return result.returncode, json.loads(result.stdout)


class CLITests(unittest.TestCase):
    def test_help_and_argument_errors_are_machine_readable(self):
        code, data = command('help')
        self.assertEqual(code, 0)
        self.assertEqual(data['schema_version'], 1)
        for args in [('unknown',), ('status',), ('devices', '--config', '/tmp/x'),
                     ('status', '--session',), ('status', '--session', '/x', '--session', '/y')]:
            with self.subTest(args=args):
                code, data = command(*args)
                self.assertEqual(code, 1)
                self.assertEqual(data['state'], 'error')

    def test_invalid_config_does_not_create_session(self):
        with tempfile.TemporaryDirectory() as parent:
            config = Path(parent)/'bad.json'
            config.write_text('{"schema_version":1}')
            session = Path(parent)/'not-created'
            code, data = command('run', '--config', str(config), '--session', str(session))
            self.assertEqual(code, 1)
            self.assertFalse(session.exists())

    def test_orphan_status_is_not_reported_running_or_signalled(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent)
            (path/'lock').touch(mode=0o600)
            (path/'status.json').write_text(json.dumps(dict(state='running', pid=os.getpid(),
                instance_id=str(uuid.uuid4()), updated_at=time.time())))
            for verb in ['status', 'stop']:
                code, state = command(verb, '--session', parent)
                self.assertEqual(code, 0)
                self.assertEqual(state['state'], 'interrupted')
                self.assertFalse(state['owner_lock_held'])
            self.assertEqual(list(path.glob('stop-*')), [])

    def test_stop_is_scoped_to_uuid_and_acknowledged_by_owner(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent)
            descriptor = os.open(path/'lock', os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            token = str(uuid.uuid4())
            state = dict(state='running', pid=os.getpid(), instance_id=token, updated_at=time.time())
            (path/'status.json').write_text(json.dumps(state))
            ready = threading.Event()
            def owner():
                deadline = time.time()+8
                while time.time()<deadline and not (path/f'stop-{token}').exists():
                    time.sleep(.01)
                if (path/f'stop-{token}').exists():
                    state.update(state='stopped', updated_at=time.time())
                    temporary = path/'state.tmp'
                    temporary.write_text(json.dumps(state)); temporary.replace(path/'status.json')
                    ready.set()
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            worker = threading.Thread(target=owner)
            worker.start()
            try:
                code, observed = command('status', '--session', parent)
                self.assertTrue(observed['owner_lock_held'])
                code, stopped = command('stop', '--session', parent)
                self.assertEqual(code, 0)
                self.assertEqual(stopped['state'], 'stop_completed')
                self.assertEqual(stopped['stopped_instance'], token)
                self.assertTrue(ready.is_set())
            finally:
                worker.join(); os.close(descriptor)

    def test_stale_heartbeat_and_unsafe_directory(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent)
            descriptor = os.open(path/'lock', os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            (path/'status.json').write_text(json.dumps(dict(state='running',
                instance_id=str(uuid.uuid4()), updated_at=time.time()-30)))
            try:
                code, state = command('status', '--session', parent)
                self.assertEqual(state['state'], 'unresponsive')
                os.chmod(parent, 0o755)
                code, state = command('stop', '--session', parent)
                self.assertEqual(code, 1)
                self.assertEqual(state['state'], 'error')
                self.assertFalse(list(path.glob('stop-*')))
            finally:
                os.chmod(parent, 0o700); os.close(descriptor)


if __name__ == '__main__':
    unittest.main()
