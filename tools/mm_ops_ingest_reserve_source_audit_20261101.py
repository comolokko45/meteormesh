#!/usr/bin/env python3
"""MeteorMesh ingest reserve source audit (read-only, bounded and non-invasive).

Read ONLY named installed Python source files in active RC1.5 sync release.
No tests of MM-01 guard, no video access, no SQLite, no timers/changes,
no data/disk writes and no network. Keyword/AST evidence is NOT a proof
that a 100 GiB reserve is enforced on every ingest write.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys

HOME=Path.home()
BASE=HOME/'.local/share/meteormesh/releases/rc15-20261031-consolidated/MeteorHub'
SCRIPTS=('meteor_morning.py','meteornode_pull.py','meteormesh_pipeline.py',
         'meteormesh_hub.py','storage_guard.py','hub_workflow.py')
RE=re.compile(r'statvfs|disk_usage|f_bavail|f_frsize|free[_ -]?(gib|gb|byte|space)'
              r'|reserve|min[_ -]?free|required[_ -]?bytes|available[_ -]?bytes'
              r'|capacity|ENOSPC|disk[_ -]?full|insufficient[_ -]?space'
              r'|MIN[_A-Z]*FREE|--min-free',re.I)
MAX_SOURCE=512*1024

def where(lineno,defs):
    active='top-level'
    for line,name in defs:
        if line>lineno:break
        active=name
    return active

def scan(p):
    try:
        s=p.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_size>MAX_SOURCE or s.st_size==0:
            return {'state':'SKIPPED_NONREGULAR_OR_LARGE'}
        raw=p.read_bytes()
        if len(raw)!=s.st_size:
            return {'state':'SOURCE_CHANGED_DURING_READ'}
        content=raw.decode('utf-8')
        tree=ast.parse(content,filename=p.name)
        defs=sorted((node.lineno,node.name) for node in ast.walk(tree)
                    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)))
        matches=[]
        for no,line in enumerate(content.splitlines(),1):
            if not RE.search(line):continue
            # No arbitrary raw file contents; show a bounded single relevant
            # source line for operator to distinguish guard from reserve.
            matches.append({'line':no,'scope':where(no,defs),
                            'code':line.strip()[:190]})
        return {'state':'SOURCE_READ_ONLY','sha256_first16':hashlib.sha256(raw).hexdigest()[:16],
                'candidate_line_count':len(matches),'candidate_lines':matches[:30],
                'output_truncated':len(matches)>30}
    except (OSError,ValueError,SyntaxError,UnicodeError):
        return {'state':'SOURCE_NOT_READABLE'}

def effective_sync():
    try:
        p=subprocess.run(['systemctl','--user','show','meteormesh-morning.service',
                          '--no-pager','-p','ExecStart','--value'],
                         text=True,capture_output=True,timeout=6)
        if p.returncode:return {'state':'UNKNOWN'}
        cmd=p.stdout
        return {'state':'SYNC_REFERENCE_VERIFIED' if str(BASE/'meteor_morning.py') in cmd
                and '--phase sync' in cmd else 'ACTIVE_SOURCE_PATH_DIFFERS',
                'expected_release':'rc15-20261031-consolidated'}
    except (OSError,subprocess.TimeoutExpired):
        return {'state':'UNKNOWN'}

def main():
    p=argparse.ArgumentParser()
    modes=p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-test',action='store_true')
    modes.add_argument('--preview',action='store_true')
    a=p.parse_args()
    if a.self_test:
        assert RE.search('if free_bytes < reserve_bytes:')
        assert RE.search('statvfs(data_dir)')
        assert not RE.search('print("capture started")')
        print('INGEST_RESERVE_SOURCE_AUDIT_SELFTEST: PASS')
        return
    print(json.dumps({
        'schema':'meteormesh.ingest-reserve-source-audit.v1',
        'active_sync':effective_sync(),
        'release_sources':{f:scan(BASE/f) for f in SCRIPTS},
        'judgement':'KEYWORD_AND_AST_ONLY__100_GIB_WRITE_GATE_NOT_PROVEN',
        'followup':'Review candidate call paths before any isolated policy patch',
        'source_content_not_modified':True,
        'production_sqlite_opened':False,
        'raw_mp4_opened':False,
        'disk_guard_tests_repeated':False,
        'service_or_timer_changed':False,
        'writes_performed':0,
    },ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except (OSError,ValueError) as ex:
        print('INGEST_RESERVE_AUDIT_ERROR:',type(ex).__name__,str(ex)[:120],file=sys.stderr)
        sys.exit(2)
