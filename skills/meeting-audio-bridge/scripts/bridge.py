#!/usr/bin/env python3
"""Read-only macOS audio inventory and declarative meeting route planner."""

import argparse
import json
import platform
from pathlib import Path
import subprocess
import sys


def audio_devices(payload):
    """Walk system_profiler groups, retaining only actual audio device entries."""
    found = []

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            if node.get('_name') and any(
                key in node for key in (
                    'coreaudio_device_input', 'coreaudio_device_output',
                    'coreaudio_device_transport', 'coreaudio_device_uid',
                )
            ):
                found.append({
                    'name': node['_name'],
                    'uid': node.get('coreaudio_device_uid'),
                    'input_channels': node.get('coreaudio_device_input'),
                    'output_channels': node.get('coreaudio_device_output'),
                    'sample_rate': node.get('coreaudio_device_srate'),
                    'default_input': node.get('coreaudio_default_audio_input_device'),
                    'default_output': node.get('coreaudio_default_audio_output_device'),
                })
            visit(node.get('_items', []))

    if not isinstance(payload, dict) or 'SPAudioDataType' not in payload:
        raise ValueError('Expected a system_profiler SPAudioDataType JSON object')
    visit(payload['SPAudioDataType'])
    return found


def inventory(payload):
    devices = audio_devices(payload)
    names = {device['name'].casefold() for device in devices}
    return {
        'schema_version': 1,
        'status': 'observed' if devices else 'needs_ui_verification',
        'devices': devices,
        'blackhole': {
            name: 'observed' if name.casefold() in names else 'not_observed'
            for name in ('BlackHole 2ch', 'BlackHole 16ch')
        },
        'end_to_end': 'untested',
        'note': 'Not observed does not prove absence. Confirm in Audio MIDI Setup. '
                'This report does not inspect app selections, permissions or audio flow.',
    }


def doctor(fixture=None):
    if fixture:
        report = inventory(json.loads(Path(fixture).read_text(encoding='utf-8')))
        report['source'] = 'fixture'
        return report
    if platform.system() != 'Darwin':
        return {'status': 'unsupported_platform', 'platform': platform.system(),
                'end_to_end': 'untested'}
    try:
        result = subprocess.run(
            ['/usr/sbin/system_profiler', 'SPAudioDataType', '-json'],
            capture_output=True, text=True, check=True, timeout=25,
        )
        report = inventory(json.loads(result.stdout))
        report['source'] = 'system_profiler'
        return report
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {'status': 'needs_ui_verification', 'end_to_end': 'untested',
                'error_type': type(exc).__name__,
                'note': 'Inspect Audio MIDI Setup; audio inventory is unavailable.'}


def make_plan(mode, headphones, microphone, forward='BlackHole 2ch',
              return_bus='BlackHole 16ch', speaker=None, mixer=None,
              mix_to_a=None, mix_to_b=None):
    if mode == 'mix':
        return make_mix_plan(headphones, microphone, forward, return_bus,
                             speaker, mixer, mix_to_a, mix_to_b)
    if any(value is not None for value in (mixer, mix_to_a, mix_to_b)):
        raise ValueError('Mixer options require --mode mix')
    speaker = 'local' if speaker is None else speaker
    if mode not in ('listen', 'two-way'):
        raise ValueError('Supported modes: listen, two-way, mix (requires a mixer).')
    if speaker not in ('local', 'colleague'):
        raise ValueError('Speaker must be local or colleague')
    if mode == 'listen' and speaker != 'local':
        raise ValueError('Colleague speech requires two-way mode')
    values = [headphones, microphone, forward, return_bus]
    if any(not isinstance(value, str) or not value.strip() or
           any(ord(char) < 32 for char in value) for value in values):
        raise ValueError('Use non-empty device names without control characters')
    headphones, microphone, forward, return_bus = [value.strip() for value in values]
    normalize = str.casefold
    if normalize(forward) == normalize(return_bus):
        raise ValueError('Forward and return buses must be independent devices')
    if normalize(headphones) in {normalize(forward), normalize(return_bus)} or \
       normalize(microphone) in {normalize(forward), normalize(return_bus)}:
        raise ValueError('Headphones/microphone must be physical endpoints, not bridge buses')
    multi_a, multi_b = 'Bridge A to B', 'Bridge B to A'
    if {normalize(multi_a), normalize(multi_b)} & set(map(normalize, values)):
        raise ValueError('An endpoint name conflicts with a generated multi-output name')

    multi_outputs = [{'name': multi_a, 'members': [headphones, forward]}]
    if mode == 'two-way':
        multi_outputs.append({'name': multi_b, 'members': [headphones, return_bus]})
    settings = {
        'A': {'app': '腾讯会议', 'input': return_bus if speaker == 'colleague' else microphone,
              'output': multi_a, 'start_muted': True},
        'B': {'app': '钉钉', 'input': forward,
              'output': multi_b if mode == 'two-way' else headphones,
              'must_unmute_to_forward': True},
    }
    # Graph represents only local digital routing, not remote call software/acoustic echo.
    edges = [['A.output', forward], [forward, 'B.input'],
             ['A.output', 'headphones'], ['B.output', 'headphones']]
    if mode == 'two-way':
        edges.append(['B.output', return_bus])
    edges.append([return_bus if speaker == 'colleague' else 'local.mic', 'A.input'])
    operations = {
        'local_speech': [
            {'app': 'A', 'set_muted': True},
            {'app': 'B', 'set_input': forward},
            {'app': 'A', 'set_input': microphone},
        ],
        'internal_discussion': [
            {'app': 'A', 'set_muted': True},
            {'app': 'B', 'set_input': microphone},
        ],
    }
    if mode == 'two-way':
        operations['colleague_speech'] = [
            {'app': 'A', 'set_muted': True},
            {'app': 'B', 'set_input': forward},
            {'app': 'A', 'set_input': return_bus},
        ]
    return {
        'schema_version': 1, 'status': 'planned_only', 'mode': mode, 'speaker': speaker,
        'end_to_end': 'untested',
        'required_virtual_devices': [forward] + ([return_bus] if mode == 'two-way' else []),
        'virtual_channels': [1, 2], 'multi_outputs': multi_outputs,
        'settings': settings, 'signal_edges': edges, 'operations': operations,
        'limitations': [
            'No device changes were made. Device presence and app compatibility are unverified.',
            'Local microphone is not heard in B during bridging; this is not simultaneous mixing.',
            'Operations leave A muted. Unmute only when actual speech/testing is requested.',
            'Internal discussion pauses A-to-B forwarding; restore B input before bridging again.',
            'Custom bus names must refer to independent devices, not aliases or mirrored devices.',
        ],
    }


def make_mix_plan(headphones, microphone, forward, return_bus, speaker,
                  mixer, mix_to_a, mix_to_b):
    """Plan two mix-minus outputs for an external mixer; never run audio."""
    if speaker not in (None, 'both'):
        raise ValueError('Mix mode uses both speakers; omit --speaker or use both')
    if mixer == 'native':
        return make_native_plan(headphones, microphone, forward, return_bus, mix_to_a, mix_to_b)
    if mixer not in ('loopback', 'ladiocast'):
        raise ValueError('Mix mode requires --mixer native, loopback or ladiocast')
    if mixer == 'ladiocast' and (mix_to_a is None or mix_to_b is None):
        raise ValueError('LadioCast requires two independent output devices: '
                         '--mix-to-a and --mix-to-b')
    # Reuse the existing bus and physical-endpoint checks, including whitespace handling.
    plan = make_plan('two-way', headphones, microphone, forward, return_bus)
    headphones = plan['multi_outputs'][0]['members'][0]
    microphone = plan['settings']['A']['input']
    forward, return_bus = plan['required_virtual_devices']
    mixed = [mix_to_a if mix_to_a is not None else 'Bridge Mix to A',
             mix_to_b if mix_to_b is not None else 'Bridge Mix to B']
    if any(not isinstance(name, str) or not name.strip() or
           any(ord(char) < 32 for char in name) for name in mixed):
        raise ValueError('Use non-empty mix device names without control characters')
    mix_to_a, mix_to_b = [name.strip() for name in mixed]
    buses = [forward, return_bus, mix_to_a, mix_to_b]
    if len({name.casefold() for name in buses}) != 4:
        raise ValueError('Raw and mixed buses must be four independent devices')
    reserved = {'a.input', 'a.output', 'b.input', 'b.output', 'local.mic', 'headphones'}
    if set(map(str.casefold, buses + [headphones, microphone])) & reserved:
        raise ValueError('Device names must not collide with signal graph node names')
    endpoints = {headphones.casefold(), microphone.casefold(),
                 'bridge a to b', 'bridge b to a'}
    if {mix_to_a.casefold(), mix_to_b.casefold()} & endpoints:
        raise ValueError('Mixed outputs must not reuse physical or multi-output devices')

    def output(device, remote, excluded):
        return {
            'device': device, 'channels': [1, 2],
            'sources': [
                {'device': microphone, 'gain_db': -9,
                 'channel_map': [[1, 1], [1, 2]]},
                {'device': remote, 'gain_db': -9,
                 'channel_map': [[1, 1], [2, 2]]},
            ],
            'excluded_sources': [excluded],
            'monitor': None,
        }

    mute_both = [{'app': 'A', 'set_muted': True}, {'app': 'B', 'set_muted': True}]
    plan.update({
        'mode': 'mix', 'speaker': 'both',
        'required_virtual_devices': buses,
        'mixer': {
            'backend': mixer, 'status': 'requires_configuration',
            'outputs': [output(mix_to_a, return_bus, forward),
                        output(mix_to_b, forward, return_bus)],
            'output_device_provisioning': 'create_in_loopback' if mixer == 'loopback'
                                          else 'supply_independent_loopback_devices',
            'recording': False, 'network_streaming': False,
        },
        'signal_edges': [
            ['A.output', forward], ['B.output', return_bus],
            ['A.output', 'headphones'], ['B.output', 'headphones'],
            ['local.mic', mix_to_a], [return_bus, mix_to_a], [mix_to_a, 'A.input'],
            ['local.mic', mix_to_b], [forward, mix_to_b], [mix_to_b, 'B.input'],
        ],
        'operations': {
            'enable_mix': mute_both + [
                {'app': 'A', 'set_input': mix_to_a},
                {'app': 'B', 'set_input': mix_to_b},
            ],
            # B already receives the local mic. A stays private; its output still reaches B.
            'internal_discussion': [{'app': 'A', 'set_muted': True}],
            'resume_mix': mute_both + [
                {'app': 'A', 'set_input': mix_to_a},
                {'app': 'B', 'set_input': mix_to_b},
            ],
            'exit_to_switching': mute_both + [
                {'app': 'A', 'set_input': microphone},
                {'app': 'B', 'set_input': forward},
            ],
        },
        'limitations': [
            'Plan only: no mixer is installed, started or configured by this command.',
            'Four independent raw/mixed devices are required; names do not prove independence.',
            'The microphone uses channel 1 duplicated to stereo; verify the real device mapping.',
            'Start each source at -9 dB and check simultaneous speech for clipping and processing artifacts.',
            'Headphones are monitored by the two multi-output devices; mixer monitors stay off.',
            'Both clients must be unmuted under test/speech authorization for full three-way speech.',
            'In mix mode, muting B also prevents the local microphone from reaching B.',
            'Local meters do not verify network transmission, echo isolation or remote intelligibility.',
        ],
    })
    plan['settings']['A']['input'] = mix_to_a
    plan['settings']['B'].update(input=mix_to_b, start_muted=True)
    return plan



def make_native_plan(headphones, microphone, forward, return_bus, mix_to_a, mix_to_b):
    """Application taps supply raw audio; only two mixed virtual outputs are needed."""
    if mix_to_a is None or mix_to_b is None:
        raise ValueError('Native mixer requires --mix-to-a and --mix-to-b')
    # Validate actual destination names with the existing two-bus endpoint checks.
    checked = make_plan('two-way', headphones, microphone, mix_to_b, mix_to_a)
    headphones = checked['multi_outputs'][0]['members'][0]
    microphone = checked['settings']['A']['input']
    mix_to_b, mix_to_a = checked['required_virtual_devices']
    reserved = {'a.input', 'a.output', 'b.input', 'b.output', 'local.mic', 'headphones'}
    if set(map(str.casefold, [headphones, microphone, mix_to_a, mix_to_b])) & reserved:
        raise ValueError('Device names conflict with signal graph node names')
    muted = [{'app': 'A', 'set_muted': True}, {'app': 'B', 'set_muted': True}]
    enable = muted + [{'app': side, 'set_output': headphones} for side in ('A', 'B')] + [
        {'app': 'A', 'set_input': mix_to_a}, {'app': 'B', 'set_input': mix_to_b}]
    return {
        'schema_version': 1, 'status': 'planned_only', 'mode': 'mix', 'speaker': 'both',
        'end_to_end': 'untested', 'required_virtual_devices': [mix_to_a, mix_to_b],
        'virtual_channels': [1, 2], 'multi_outputs': [],
        'settings': {
            'A': {'app': '腾讯会议', 'input': mix_to_a, 'output': headphones, 'start_muted': True},
            'B': {'app': '钉钉', 'input': mix_to_b, 'output': headphones, 'start_muted': True},
        },
        'mixer': {
            'backend': 'native', 'status': 'experimental_requires_configuration',
            'capture': 'selected_coreaudio_process_taps', 'process_selection': 'required_in_app',
            'outputs': [
                {'device': mix_to_a, 'channels': [1, 2], 'sources': ['local.mic', 'B.output'],
                 'excluded_sources': ['A.output'], 'source_gain_db': -9},
                {'device': mix_to_b, 'channels': [1, 2], 'sources': ['local.mic', 'A.output'],
                 'excluded_sources': ['B.output'], 'source_gain_db': -9},
            ],
            'recording': False, 'network_streaming': False,
        },
        'signal_edges': [
            ['A.output', 'headphones'], ['B.output', 'headphones'],
            ['local.mic', mix_to_a], ['B.output', mix_to_a], [mix_to_a, 'A.input'],
            ['local.mic', mix_to_b], ['A.output', mix_to_b], [mix_to_b, 'B.input'],
        ],
        'operations': {'enable_mix': enable, 'resume_mix': enable,
                       'internal_discussion': [{'app': 'A', 'set_muted': True}]},
        'limitations': [
            'Prototype plan only; app build, permissions and per-client capture are unverified.',
            'Both clients must output directly to physical headphones, never old Bridge multi-outputs.',
            'Read and save existing settings before changing either client; keep both muted.',
            'Only two independent virtual destinations are needed; taps replace raw buses.',
            'Select the actual audio process of each client; no global system-audio fallback.',
            'Restore both input and output selections before returning to the old switching mode.',
            'No automatic microphone opening, driver installation, recording or remote verification.',
        ],
    }


def emit(data, output=None):
    rendered = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if output:
        # Exclusive creation prevents replacing an existing session or recovery record.
        with Path(output).open('x', encoding='utf-8') as stream:
            stream.write(rendered)
    else:
        sys.stdout.write(rendered)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('doctor', help='Read-only macOS device inventory')
    inspect.add_argument('--fixture', help='Parse saved system_profiler JSON instead of live devices')
    inspect.add_argument('--output', help='Create a JSON report; refuses overwrite')
    plan = commands.add_parser('plan', help='Generate JSON instructions; does not configure devices')
    plan.add_argument('--mode', choices=['listen', 'two-way', 'mix'], default='two-way')
    plan.add_argument('--speaker', choices=['local', 'colleague', 'both'])
    plan.add_argument('--mixer', choices=['native', 'loopback', 'ladiocast'])
    plan.add_argument('--mix-to-a', help='Independent mixed microphone device for A')
    plan.add_argument('--mix-to-b', help='Independent mixed microphone device for B')
    plan.add_argument('--headphones', required=True)
    plan.add_argument('--microphone', required=True)
    plan.add_argument('--forward', default='BlackHole 2ch')
    plan.add_argument('--return-bus', default='BlackHole 16ch')
    plan.add_argument('--output', help='Create a JSON plan; refuses overwrite')
    args = parser.parse_args(argv)
    try:
        if args.command == 'doctor':
            result = doctor(args.fixture)
        else:
            result = make_plan(args.mode, args.headphones, args.microphone,
                               args.forward, args.return_bus, args.speaker,
                               args.mixer, args.mix_to_a, args.mix_to_b)
        emit(result, args.output)
        return 0 if result['status'] in ('observed', 'planned_only') else 2
    except (OSError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
