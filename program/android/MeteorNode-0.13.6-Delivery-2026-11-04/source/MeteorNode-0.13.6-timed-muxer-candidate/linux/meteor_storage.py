#!/usr/bin/env python3
"""MeteorNode 0.11: remote shared-storage management from an authorized PC.

Requires an administrator key manually read once from MeteorNode on the phone.
No phone confirmation per file operation; no shell, app data or root capabilities.
Destructive actions only occur for explicit CLI subcommands (never on a timer).
"""
from __future__ import annotations
import argparse
import hashlib
import http.client
import json
import os
import pathlib
import sys
import urllib.parse

LAB_TOKEN = '0' * 32


def api(host, key, endpoint, *, method='GET', body=None, timeout=30, stream_to=None):
    conn = http.client.HTTPConnection(host, 8080, timeout=timeout)
    url = '/api/v1/storage/' + endpoint
    headers = {'Authorization': 'Bearer ' + LAB_TOKEN, 'X-Meteor-Storage-Key': key}
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if hasattr(body, 'read'):
        raise ValueError('Use upload() for streamed files')
    try:
        conn.request(method, url, body=body, headers=headers)
        response = conn.getresponse()
        if stream_to is None:
            payload = response.read()
            if response.status not in (200, 206):
                raise RuntimeError(f'HTTP {response.status}: {payload[:250].decode("utf-8", "replace")}')
            return json.loads(payload)
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}: {response.read(300).decode("utf-8", "replace")}')
        with open(stream_to, 'xb') as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        return {'downloaded': str(stream_to), 'bytes': os.path.getsize(stream_to)}
    finally:
        conn.close()


def upload(host, key, local, remote, overwrite=False):
    size = local.stat().st_size
    if size == 0 or size > 512 * 1024**2:
        raise ValueError('Upload size must be 1 byte through 512 MiB')
    q = urllib.parse.urlencode({'path': remote, 'overwrite': '1' if overwrite else '0'})
    conn = http.client.HTTPConnection(host, 8080, timeout=90)
    conn.putrequest('POST', '/api/v1/storage/upload?' + q)
    conn.putheader('Authorization', 'Bearer ' + LAB_TOKEN)
    conn.putheader('X-Meteor-Storage-Key', key)
    conn.putheader('Content-Type', 'application/octet-stream')
    conn.putheader('Content-Length', size)
    conn.endheaders()
    try:
        with local.open('rb') as f:
            while chunk := f.read(1 << 20):
                conn.send(chunk)
        response = conn.getresponse()
        result = response.read()
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}: {result[:300]!r}')
        obj = json.loads(result)
        checksum = hashlib.sha256(local.read_bytes()).hexdigest() if size < 16 * 1024**2 else None
        if checksum is not None and obj.get('sha256') != checksum:
            raise RuntimeError('Upload SHA-256 verification failed')
        return obj
    finally:
        conn.close()


def get_listing(host, key, directory):
    return api(host, key, 'list?' + urllib.parse.urlencode({'dir': directory}))


def find_exact(host, key, path):
    parent, sep, base = path.rpartition('/')
    if not base:
        raise ValueError('A file path under shared storage is required')
    rows = get_listing(host, key, parent)['entries']
    return next((v for v in rows if v['path'] == path), None)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True, help='Phone LAN IP, e.g. 192.168.0.105')
    p.add_argument('--key-file', type=pathlib.Path,
                   default=pathlib.Path.home() / '.config/meteormesh/storage-admin.key')
    subs = p.add_subparsers(dest='command', required=True)
    for verb, parameters in {
        'ls': [('directory', '?')],
        'get': [('remote', None), ('local', None)],
        'put': [('local', None), ('remote', None)],
        'replace': [('local', None), ('remote', None)],
        'mkdir': [('remote', None)],
        'mv': [('source', None), ('destination', None)],
        'rm': [('remote', None)],
        'rmtree': [('remote', None)],
        'purge': [('receipt_json', None)],
    }.items():
        sub = subs.add_parser(verb)
        for name, mode in parameters:
            if mode == '?':
                sub.add_argument(name, nargs='?', default='')
            else:
                sub.add_argument(name)
    a = p.parse_args(argv)
    if not a.host.startswith('192.168.'):
        p.error('Only trusted 192.168.x.x network devices are permitted')
    if not a.key_file.is_file() or a.key_file.stat().st_mode & 0o077:
        p.error('Administrator key file is missing or readable by others (chmod 600)')
    key = a.key_file.read_text().strip()
    if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        p.error('Invalid administrator key')
    if a.command == 'ls': result = get_listing(a.host, key, a.directory)
    elif a.command == 'get':
        path = pathlib.Path(a.local)
        result = api(a.host, key, 'file?' + urllib.parse.urlencode({'path': a.remote}), stream_to=path, timeout=90)
    elif a.command in ('put', 'replace'):
        result = upload(a.host, key, pathlib.Path(a.local), a.remote, overwrite=a.command == 'replace')
    elif a.command == 'mkdir': result = api(a.host, key, 'mkdir', method='POST', body={'path': a.remote})
    elif a.command == 'mv':
        result = api(a.host, key, 'move', method='POST', body={'from': a.source, 'to': a.destination})
    elif a.command == 'rm':
        item = find_exact(a.host, key, a.remote)
        if item is None or item['directory']:
            raise RuntimeError('Missing file or a directory was supplied')
        result = api(a.host, key, 'delete', method='POST', body={'files': [item]})
    elif a.command == 'purge':
        payload = json.loads(pathlib.Path(a.receipt_json).read_text())
        if not isinstance(payload, dict) or not isinstance(payload.get('segments'), list):
            raise ValueError('Expected {segments:[{segment_id,sha256}]}')
        result = api(a.host, key, 'purge-recordings', method='POST', body=payload, timeout=900)
    elif a.command == 'rmtree':
        item = find_exact(a.host, key, a.remote)
        if item is None or not item['directory']:
            raise RuntimeError('Missing directory or a file was supplied')
        result = api(a.host, key, 'delete-tree', method='POST',
                     body={'path': a.remote, 'modified_ms': item['modified_ms'], 'recursive': True})
    else: raise AssertionError(a.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
