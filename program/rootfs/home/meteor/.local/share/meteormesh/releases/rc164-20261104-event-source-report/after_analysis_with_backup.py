#!/usr/bin/env python3
"""RC1.5.3 single post-analysis chain; preserves existing observer and SSD backup."""
from pathlib import Path
import subprocess
import sys

HOME=Path.home()
ORIGINAL=HOME/'.local/share/meteormesh/releases/rc164-20261104-event-source-report/hooks/after_analysis_rc151.py'
BACKUP=Path(__file__).resolve().parent/'meteor_db_backup.py'

def main():
    for label,script,timeout in (('REPORT',ORIGINAL,900),('DB_BACKUP',BACKUP,180)):
        try:
            r=subprocess.run(['/usr/bin/python3','-B',str(script)],capture_output=True,text=True,timeout=timeout)
            for line in r.stdout.splitlines():print(line,flush=True)
            if r.stderr:print('RC153_CHAIN_STDERR: '+label+' '+r.stderr[:200].replace('\n',' '),flush=True)
            status='OK' if r.returncode==0 else 'ERROR_EXIT_'+str(r.returncode)
            if r.returncode==0 and label=='REPORT':
                summaries=[line for line in r.stdout.splitlines() if line.startswith('RC151_AFTER_ANALYSIS: ')]
                if len(summaries)!=1:status='ERROR_MISSING_REPORT_SUMMARY'
                elif 'ERROR_' in summaries[0]:status='WARNING_REPORT_ERRORS'
                elif 'WARNING_' in summaries[0]:status='WARNING_PARTIAL_REPORT'
            if r.returncode==0 and label=='DB_BACKUP' and 'MM153_DB_BACKUP: PASS' not in r.stdout:
                status='ERROR_MISSING_BACKUP_RESULT'
        except (OSError,subprocess.TimeoutExpired) as e:
            status='ERROR_'+type(e).__name__
        print('RC153_CHAIN: '+label+'='+status,flush=True)
    return 0  # Observer does not mask detector result.

if __name__=='__main__':sys.exit(main())
