import importlib.util
import itertools
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


class MixTests(unittest.TestCase):
    def plan(self, **kwargs):
        args = dict(mode='mix', headphones='USB Headset', microphone='Mac Microphone',
                    mixer='ladiocast', forward='IdeaShare 2ch', return_bus='Return Raw',
                    mix_to_a='Mixed A', mix_to_b='Mixed B')
        args.update(kwargs)
        return bridge.make_plan(**args)

    def test_emitted_mixer_configuration_has_mix_minus_and_no_local_monitor(self):
        p = self.plan()
        # Build reachability from the actual configuration, independently of signal_edges.
        edges = []
        for side in ('A', 'B'):
            settings = p['settings'][side]
            edges.extend([(side + '.output', settings['output']),
                          (settings['input'], side + '.input')])
            self.assertTrue(settings['start_muted'])
        for group in p['multi_outputs']:
            edges.extend((group['name'], member) for member in group['members'])
        for output in p['mixer']['outputs']:
            self.assertIsNone(output['monitor'])
            self.assertEqual(output['channels'], [1, 2])
            for source in output['sources']:
                edges.append((source['device'], output['device']))
                self.assertNotIn(source['device'], output['excluded_sources'])
                self.assertLess(source['gain_db'], -6)
            mic = next(s for s in output['sources'] if s['device'] == 'Mac Microphone')
            self.assertEqual(mic['channel_map'], [[1, 1], [1, 2]])
        for side, other in [('A', 'B'), ('B', 'A')]:
            self.assertTrue(reaches(edges, 'Mac Microphone', side + '.input'))
            self.assertTrue(reaches(edges, other + '.output', side + '.input'))
            self.assertFalse(reaches(edges, side + '.output', side + '.input'))
            self.assertTrue(reaches(edges, side + '.output', 'USB Headset'))
            self.assertFalse(reaches(p['signal_edges'], side + '.output', side + '.input'))
        self.assertFalse(reaches(edges, 'Mac Microphone', 'USB Headset'))
        self.assertEqual(p['status'], 'planned_only')
        self.assertEqual(p['end_to_end'], 'untested')
        self.assertFalse(p['mixer']['recording'])
        self.assertFalse(p['mixer']['network_streaming'])

    def test_native_taps_need_two_destinations_and_direct_headphones(self):
        p = self.plan(mixer='native')
        self.assertEqual(p['required_virtual_devices'], ['Mixed A', 'Mixed B'])
        self.assertEqual(p['multi_outputs'], [])
        edges = p['signal_edges']
        for side, other in [('A', 'B'), ('B', 'A')]:
            self.assertEqual(p['settings'][side]['output'], 'USB Headset')
            self.assertTrue(reaches(edges, 'local.mic', side + '.input'))
            self.assertTrue(reaches(edges, other + '.output', side + '.input'))
            self.assertFalse(reaches(edges, side + '.output', side + '.input'))
        self.assertNotIn('exit_to_switching', p['operations'])
        for args in ({'mix_to_a': None}, {'mix_to_b': 'Mixed A'}, {'mix_to_a': 'USB Headset'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.plan(mixer='native', **args)

    def test_all_bus_collisions_and_physical_endpoint_reuse_rejected(self):
        keys = ['forward', 'return_bus', 'mix_to_a', 'mix_to_b']
        for a, b in itertools.combinations(keys, 2):
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                self.plan(**{a: ' Duplicate ', b: 'duplicate'})
        for key in ('mix_to_a', 'mix_to_b'):
            for name in ('USB Headset', 'Mac Microphone', 'Bridge A to B',
                         'Bridge B to A', 'A.output', '  ', 'bad\nname'):
                with self.subTest(key=key, name=name), self.assertRaises(ValueError):
                    self.plan(**{key: name})

    def test_backend_and_speaker_requirements(self):
        for kwargs in ({'mixer': None}, {'mixer': 'unknown'}, {'mix_to_a': None},
                       {'mix_to_b': None}, {'speaker': 'local'}, {'speaker': 'colleague'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.plan(**kwargs)
        p = self.plan(mixer='loopback', mix_to_a=None, mix_to_b=None)
        self.assertEqual(p['settings']['A']['input'], 'Bridge Mix to A')
        self.assertEqual(p['mixer']['output_device_provisioning'], 'create_in_loopback')
        with self.assertRaises(ValueError):
            self.plan(mode='two-way')

    def test_enter_exit_and_internal_discussion_keep_a_muted(self):
        p = self.plan()
        for actions in p['operations'].values():
            self.assertEqual(actions[0], {'app': 'A', 'set_muted': True})
            self.assertFalse(any(action.get('set_muted') is False for action in actions))
        self.assertEqual(p['operations']['exit_to_switching'][2:], [
            {'app': 'A', 'set_input': 'Mac Microphone'},
            {'app': 'B', 'set_input': 'IdeaShare 2ch'},
        ])
        self.assertEqual(p['operations']['internal_discussion'],
                         [{'app': 'A', 'set_muted': True}])


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
    def test_mix_cli_requires_independent_outputs_and_generates_both_speakers(self):
        command = [sys.executable, str(SCRIPT), 'plan', '--mode', 'mix',
                   '--mixer', 'ladiocast', '--headphones', '耳机', '--microphone', '麦克风']
        invalid = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(invalid.returncode, 1)
        self.assertEqual(invalid.stdout, '')
        result = subprocess.run(command + ['--mix-to-a', '混音甲', '--mix-to-b', '混音乙'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['speaker'], 'both')
        self.assertEqual(plan['settings']['B']['input'], '混音乙')
        self.assertEqual(plan['mixer']['status'], 'requires_configuration')

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
