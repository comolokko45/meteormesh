#!/usr/bin/env python3
"""RC1.5 single post-analysis sequencer. Independent nonfatal journal observers."""
from __future__ import annotations
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent
STEPS = [('NIGHT_SNAPSHOT','meteor-night-snapshot.py',[]),
         ('EVENT_REVIEW','meteor-event-review.py',[]),
         ('AUTO_PACKAGE','meteor-auto-package.py',['--limit','10'])]

def main():
    results=[]
    for label,name,args in STEPS:
        try:
            p=subprocess.run(['/usr/bin/python3','-B',str(BASE/name),*args],
                 capture_output=True,text=True,timeout=300)
            for line in p.stdout.splitlines():print(line,flush=True)
            if p.stderr:print('RC15_HOOK_STDERR: '+label+' '+p.stderr[:240].replace('\n',' '),flush=True)
            status='OK' if p.returncode==0 else 'ERROR_'+str(p.returncode)
        except (OSError,subprocess.TimeoutExpired) as e:
            status=type(e).__name__
        results.append(label+'='+status)
    print('RC15_AFTER_ANALYSIS: '+','.join(results),flush=True)
    return 0   # Never masks detector exit status

if __name__=='__main__':sys.exit(main())
