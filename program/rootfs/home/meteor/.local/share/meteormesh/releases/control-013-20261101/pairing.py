#!/usr/bin/env python3
"""Display one device's private pairing details locally over SSH; never send to ChatGPT."""
import argparse
import hashlib
import json
import ssl
from pathlib import Path

ROOT=Path.home()/'.local/share/meteormesh/control-013'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('device_id',choices=['cam-viva','cam-ruby','cam-surya'])
    args=p.parse_args()
    file=ROOT/'config.json'
    if file.is_symlink() or not file.is_file() or file.stat().st_mode&0o077:
        raise SystemExit('UNSAFE_CONFIGURATION')
    cfg=json.loads(file.read_text())
    cert=Path(cfg['cert'])
    der=ssl.PEM_cert_to_DER_cert(cert.read_text())
    print('DEVICE:',args.device_id)
    print('HUB_IP: <HP_G62_LAN_IP_FROM_IP_ADDR>')
    print('HUB_PORT: 8443')
    print('CERT_SHA256:',hashlib.sha256(der).hexdigest())
    print('DEVICE_TOKEN:',cfg['tokens'][args.device_id])
    print('Only enter this into the corresponding phone. Never send the token in chat.')
if __name__=='__main__':main()
