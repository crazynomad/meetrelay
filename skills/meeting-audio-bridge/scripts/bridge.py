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
              return_bus='BlackHole 16ch', speaker='local'):
    if mode not in ('listen', 'two-way'):
        raise ValueError('Supported modes: listen, two-way. Simultaneous speech needs a mixer.')
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
    plan.add_argument('--mode', choices=['listen', 'two-way'], default='two-way')
    plan.add_argument('--speaker', choices=['local', 'colleague'], default='local')
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
                               args.forward, args.return_bus, args.speaker)
        emit(result, args.output)
        return 0 if result['status'] in ('observed', 'planned_only') else 2
    except (OSError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
