import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/meeting-audio-bridge/scripts/bridge.py'
spec = importlib.util.spec_from_file_location('bridge', SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def reaches(edges, start, target):
    seen, pending = set(), [start]
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            pending.extend(b for a, b in edges if a == node)
    return False


class RouteTests(unittest.TestCase):
    def plan(self, **kwargs):
        return bridge.make_plan(headphones='USB Headset', microphone='Mac Microphone', **kwargs)

    def test_listen_has_no_return_to_a(self):
        p = self.plan(mode='listen')
        self.assertTrue(reaches(p['signal_edges'], 'A.output', 'B.input'))
        self.assertFalse(reaches(p['signal_edges'], 'B.output', 'A.input'))
        self.assertEqual(p['required_virtual_devices'], ['BlackHole 2ch'])
        self.assertEqual(p['settings']['B']['output'], 'USB Headset')

    def test_two_way_colleague_reaches_a_without_digital_self_return(self):
        p = self.plan(mode='two-way', speaker='colleague')
        edges = p['signal_edges']
        self.assertTrue(reaches(edges, 'A.output', 'B.input'))
        self.assertTrue(reaches(edges, 'B.output', 'A.input'))
        for side in ('A', 'B'):
            self.assertFalse(reaches(edges, side + '.output', side + '.input'))
            self.assertTrue(reaches(edges, side + '.output', 'headphones'))
        self.assertFalse(reaches(edges, 'local.mic', 'A.input'))

    def test_switching_preserves_muted_handoff_and_restore_of_b(self):
        p = self.plan(mode='two-way')
        self.assertTrue(reaches(p['signal_edges'], 'local.mic', 'A.input'))
        for actions in p['operations'].values():
            self.assertEqual(actions[0], {'app': 'A', 'set_muted': True})
            self.assertFalse(any(action.get('set_muted') is False for action in actions))
        for mode in ('local_speech', 'colleague_speech'):
            self.assertEqual(p['operations'][mode][1], {'app': 'B', 'set_input': 'BlackHole 2ch'})

    def test_invalid_routes_rejected(self):
        for args in [
            {'mode': 'listen', 'speaker': 'colleague'},
            {'mode': 'two-way', 'return_bus': ' blackhole 2CH '},
            {'mode': 'two-way', 'forward': 'USB Headset'},
            {'mode': 'two-way', 'forward': 'Bridge A to B'},
            {'mode': 'two-way', 'forward': '\n'},
            {'mode': 'simultaneous'},
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.plan(**args)


class InventoryTests(unittest.TestCase):
    def test_empty_inventory_is_unknown_not_absent(self):
        p = bridge.inventory({'SPAudioDataType': [{'_name': 'coreaudio_device', '_items': []}]})
        self.assertEqual(p['status'], 'needs_ui_verification')
        self.assertEqual(p['blackhole']['BlackHole 2ch'], 'not_observed')
        self.assertEqual(p['end_to_end'], 'untested')

    def test_nested_devices_and_duplicate_names_are_preserved(self):
        devices = [{'_name': 'BlackHole 2ch', 'coreaudio_device_input': 2,
                    'coreaudio_device_output': 2},
                   {'_name': 'USB', 'coreaudio_device_output': 2, 'coreaudio_device_uid': 'a'},
                   {'_name': 'USB', 'coreaudio_device_output': 2, 'coreaudio_device_uid': 'b'}]
        report = bridge.inventory({'SPAudioDataType': [{'_items': devices}]})
        self.assertEqual(len(report['devices']), 3)
        self.assertEqual(report['blackhole']['BlackHole 2ch'], 'observed')
        self.assertEqual(report['blackhole']['BlackHole 16ch'], 'not_observed')

    def test_bad_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            bridge.inventory({'unrelated': []})

    def test_timeout_is_not_success(self):
        with patch.object(bridge.platform, 'system', return_value='Darwin'), \
             patch.object(bridge.subprocess, 'run', side_effect=subprocess.TimeoutExpired('test', 25)):
            self.assertEqual(bridge.doctor()['status'], 'needs_ui_verification')

    def test_non_mac_does_not_launch_profiler(self):
        with patch.object(bridge.platform, 'system', return_value='Linux'), \
             patch.object(bridge.subprocess, 'run') as runner:
            self.assertEqual(bridge.doctor()['status'], 'unsupported_platform')
            runner.assert_not_called()


class CLITests(unittest.TestCase):
    def test_plan_json_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'plan.json'
            command = [sys.executable, str(SCRIPT), 'plan', '--headphones', '耳机',
                       '--microphone', '麦克风', '--output', str(output)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            original = output.read_bytes()
            self.assertEqual(json.loads(original)['status'], 'planned_only')
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 1)
            self.assertEqual(output.read_bytes(), original)

    def test_empty_fixture_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'empty.json'
            fixture.write_text('{"SPAudioDataType": []}', encoding='utf-8')
            result = subprocess.run([sys.executable, str(SCRIPT), 'doctor', '--fixture', str(fixture)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['source'], 'fixture')


if __name__ == '__main__':
    unittest.main()
