#!/usr/bin/env python3
"""RC1.5.1: three existing post-analysis observers, warnings kept in journal."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent
STEPS = [('NIGHT_SNAPSHOT','meteor-night-snapshot.py',[]),
         ('EVENT_REVIEW','meteor-event-review.py',[]),
         ('AUTO_PACKAGE','meteor-auto-package.py',['--limit','10'])]


def classify(label, ret, lines):
    if ret != 0:
        return 'ERROR_EXIT_'+str(ret)
    error_prefix = {'NIGHT_SNAPSHOT':'NIGHT_ANALYSIS_SNAPSHOT_UNAVAILABLE:',
                    'EVENT_REVIEW':'EVENT_REVIEW_UNAVAILABLE:',
                    'AUTO_PACKAGE':'EVENT_AUTO_PACKAGE_UNAVAILABLE:'}
    if any(s.startswith(error_prefix[label]) for s in lines):
        return 'ERROR_REPORTED'
    success_prefix = {'NIGHT_SNAPSHOT':'NIGHT_ANALYSIS_SNAPSHOT:',
                      'EVENT_REVIEW':'EVENT_REVIEW_INDEX:',
                      'AUTO_PACKAGE':'EVENT_AUTO_PACKAGE:'}
    matches = [s.split(': ',1)[1] for s in lines if s.startswith(success_prefix[label])]
    if len(matches) != 1:
        return 'ERROR_MISSING_RESULT'
    try:
        doc = json.loads(matches[0])
        if not isinstance(doc, dict):
            return 'ERROR_BAD_RESULT'
        if label == 'NIGHT_SNAPSHOT':
            if doc.get('schema') != 'meteormesh.night-analysis-snapshot.v1':
                return 'ERROR_BAD_SCHEMA'
            return 'WARNING_PARTIAL' if doc.get('status') != 'PROVISIONAL_ANALYZED' else 'OK'
        if label == 'EVENT_REVIEW':
            if type(doc.get('failed_sources')) is not int:
                return 'ERROR_BAD_SCHEMA'
            missing = doc.get('missing_source_count', 0)
            if type(missing) is not int or missing < 0:
                return 'ERROR_BAD_SCHEMA'
            if doc['failed_sources'] and missing:
                return 'WARNING_SOURCE_ERRORS_AND_MISSING'
            if doc['failed_sources']:
                return 'WARNING_SOURCE_ERRORS'
            if missing:
                return 'WARNING_SOURCE_MISSING'
            return 'OK'
        if label == 'AUTO_PACKAGE':
            if type(doc.get('invalid')) is not int:
                return 'ERROR_BAD_SCHEMA'
            return 'WARNING_INVALID_RECEIPTS' if doc['invalid'] else 'OK'
    except (ValueError, KeyError, TypeError):
        return 'ERROR_BAD_RESULT'
    return 'ERROR_UNEXPECTED_STEP'


def main():
    results = []
    for label, name, args in STEPS:
        try:
            p = subprocess.run(['/usr/bin/python3','-B',str(BASE/name),*args],
                               capture_output=True,text=True,timeout=300)
            lines = p.stdout.splitlines()
            for line in lines:
                print(line, flush=True)
            if p.stderr:
                print('RC151_HOOK_STDERR: '+label+' '+p.stderr[:240].replace('\n',' '),flush=True)
            status = classify(label, p.returncode, lines)
        except (OSError, subprocess.TimeoutExpired) as e:
            status = 'ERROR_'+type(e).__name__
        results.append(label+'='+status)
    print('RC151_AFTER_ANALYSIS: '+','.join(results),flush=True)
    return 0  # Never masks original detector/service result.

if __name__ == '__main__':
    sys.exit(main())
