#!/usr/bin/env python3
"""One-time ADB UI setup for three user-owned MeteorNode 0.13.2 phones.

Only performs ordinary foreground UI interactions on *recognized* devices:
grant CAMERA runtime permission, open MainActivity, select HP HTTPS pairing,
enter current HP LAN IPv4 (PIN already defaults to 0000), press Save, press
"1 LAN + UZAKTAN KONTROLU HAZIRLA", then read the existing local Hub status.
It NEVER triggers "KAYDI BASLAT", deletes files, modifies Android security
settings, changes the Linux host configuration, or exposes derived tokens.
Requires device screen unlocked; does not defeat Android lock/consent dialogs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
from urllib import request
import xml.etree.ElementTree as ET

PACKAGE='org.meteormesh.node.compactfixedtest'
ACTIVITY=PACKAGE+'/org.meteormesh.node.MainActivity'
PHONE_CODES=('surya','ruby','viva')
HP_IP='192.168.0.39'
CERT=Path.home()/'.local/share/meteormesh/control-013/server.crt'
APP_CERT_SHA='aa2f35e659a795bbd50ba8fade8de916ae7308c5cef3247a3fba74683c282c7b'
UI_FILE='/sdcard/Download/.meteor-node-0132-ui.xml'

class SetupError(Exception):pass

def run(cmd,timeout=20):
    try:
        p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise SetupError('COMMAND_TIMEOUT_OR_MISSING: '+str(cmd[0])) from exc
    if p.returncode:
        raise SetupError('COMMAND_FAILED: '+str(cmd[0])+' '+str(cmd[1:3])+
                         ' '+(p.stderr or p.stdout).strip()[:145])
    return p.stdout

def adb(serial,*args,timeout=25):
    return run(['adb','-s',serial,*args],timeout=timeout)

def verify_hub():
    if CERT.is_symlink() or not CERT.is_file():
        raise SetupError('CONTROL_TLS_CERT_NOT_FOUND')
    try:
        certpem=CERT.read_text(encoding='ascii')
        der=ssl.PEM_cert_to_DER_cert(certpem)
    except (OSError,ValueError) as ex:
        raise SetupError('TLS_CERT_NOT_READABLE') from ex
    if hashlib.sha256(der).hexdigest()!=APP_CERT_SHA:
        raise SetupError('TLS_CERT_PIN_MISMATCH__STOP_BEFORE_PAIRING')
    out=run(['ip','-4','-o','addr','show','dev','enp2s0'])
    if not re.search(r'\b'+re.escape(HP_IP)+r'/\d+\b',out):
        raise SetupError('HUB_LAN_IPV4_NO_LONGER_'+HP_IP)
    print('HUB_LAN_AND_PINNED_CERT: PASS',flush=True)

def find_phones():
    out=run(['adb','devices','-l'],timeout=10)
    found={}
    for line in out.splitlines():
        cols=line.split()
        if len(cols)<3 or cols[1]!='device':continue
        for code in PHONE_CODES:
            if 'device:'+code in cols:
                found.setdefault(code,[]).append(cols[0])
    dup={k:v for k,v in found.items() if len(v)>1}
    if dup:raise SetupError('AMBIGUOUS_ADB_DEVICE__STOP')
    return {k:found[k][0] for k in PHONE_CODES if k in found}

def dump(serial):
    # This temporary XML is created and removed by this script; app data
    # and media remain untouched.
    try:
        adb(serial,'shell','uiautomator','dump',UI_FILE,timeout=22)
        text=adb(serial,'exec-out','cat',UI_FILE,timeout=10)
    finally:
        try:adb(serial,'shell','rm','-f',UI_FILE,timeout=8)
        except SetupError:pass
    index=text.find('<?xml')
    if index<0:raise SetupError('UI_XML_UNAVAILABLE__UNLOCK_SCREEN')
    try:return ET.fromstring(text[index:])
    except ET.ParseError as ex:raise SetupError('UI_XML_MALFORMED') from ex

def center(bound):
    m=re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',bound or '')
    if not m:raise SetupError('UNEXPECTED_UI_BOUNDS')
    x1,y1,x2,y2=map(int,m.groups())
    if x2<=x1 or y2<=y1 or x2>5000 or y2>5000:
        raise SetupError('UNSAFE_UI_BOUNDS')
    return str((x1+x2)//2),str((y1+y2)//2)

def find_button(root,label):
    matches=[n for n in root.iter('node')
             if n.get('class')=='android.widget.Button'
             and n.get('text','').strip()==label]
    if len(matches)!=1:raise SetupError('EXPECTED_BUTTON_NOT_VISIBLE: '+label)
    n=matches[0]
    if n.get('enabled')=='false':raise SetupError('BUTTON_DISABLED: '+label)
    return center(n.get('bounds'))

def press(serial,where):
    adb(serial,'shell','input','tap',*where,timeout=8)

def pair_and_arm(code,serial):
    print('=== '+code.upper()+' ===',flush=True)
    pkg=adb(serial,'shell','dumpsys','package',PACKAGE,timeout=10)
    if 'versionCode=15' not in pkg or 'versionName=0.13.2-pin0000-alpha' not in pkg:
        raise SetupError('INSTALLED_VERSION_NOT_0132')
    # This is a normal runtime permission grant via developer-authorized ADB.
    adb(serial,'shell','pm','grant',PACKAGE,'android.permission.CAMERA',timeout=8)
    try:
        adb(serial,'shell','pm','grant',PACKAGE,'android.permission.POST_NOTIFICATIONS',timeout=8)
    except SetupError:
        print('NOTIFICATIONS_PERMISSION: NOT_AUTO_GRANTED',flush=True)
    adb(serial,'shell','input','keyevent','KEYCODE_WAKEUP',timeout=8)
    adb(serial,'shell','am','start','-n',ACTIVITY,timeout=15)
    time.sleep(1.3)
    root=dump(serial)
    if not any('MeteorNode 0.13.2' in n.get('text','') for n in root.iter('node')):
        raise SetupError('APP_FOREGROUND_NOT_VISIBLE__UNLOCK_PHONE')
    press(serial,find_button(root,'HP G62 HTTPS ESLESTIRME'))
    time.sleep(0.6)
    root=dump(serial)
    title=[n.get('text','') for n in root.iter('node')
           if n.get('text','').startswith('MeteorHub ortak sifre:')]
    if len(title)!=1 or title[0]!='MeteorHub ortak sifre: cam-'+code:
        raise SetupError('PAIRING_DIALOG_NOT_CORRECT_DEVICE')
    edits=[n for n in root.iter('node') if n.get('class')=='android.widget.EditText']
    if len(edits)!=2:raise SetupError('PAIRING_DIALOG_INPUTS_UNEXPECTED')
    press(serial,center(edits[0].get('bounds')))
    adb(serial,'shell','input','text',HP_IP,timeout=8)
    # The current application itself pre-fills PIN 0000. Only check masked
    # password input exists; do NOT extract its value or log it.
    if edits[1].get('password')!='true':
        raise SetupError('PAIRING_PASSWORD_FIELD_NOT_MASKED')
    press(serial,find_button(root,'Kaydet'))
    time.sleep(0.6)
    root=dump(serial)
    press(serial,find_button(root,'1  LAN + UZAKTAN KONTROLU HAZIRLA'))
    print('PAIRING_UI_SAVED_AND_LAN_ARM_BUTTON_PRESSED',flush=True)

def hub_status():
    ctx=ssl.create_default_context(cafile=str(CERT))
    try:
        with request.urlopen('https://127.0.0.1:8443/v1/status',context=ctx,timeout=7) as response:
            doc=json.load(response)
    except Exception as ex:
        raise SetupError('LOCAL_HTTPS_STATUS_UNREACHABLE: '+type(ex).__name__) from ex
    if doc.get('schema')!='meteormesh.control-status.v1' or not isinstance(doc.get('devices'),dict):
        raise SetupError('LOCAL_HUB_SCHEMA_MISMATCH')
    for code in PHONE_CODES:
        obj=doc['devices'].get('cam-'+code,{})
        print('cam-'+code+': '+('ONLINE' if obj.get('online') is True else 'OFFLINE_PENDING_HEARTBEAT'),
              flush=True)

def selftest():
    assert center('[120,200][300,240]')==('210','220')
    try:center('[10,10][5,5]')
    except SetupError:pass
    else:raise AssertionError('MALFORMED_BOUNDS_WERE_ACCEPTED')
    xml=ET.fromstring('<hierarchy><node class="android.widget.Button" '
                      'text="HP G62 HTTPS ESLESTIRME" enabled="true" bounds="[0,0][100,50]"/>'
                      '</hierarchy>')
    assert find_button(xml,'HP G62 HTTPS ESLESTIRME')==('50','25')
    print('ADB_0132_SETUP_SELFTEST: PASS; NO_DEVICES_TOUCHED')

def main():
    p=argparse.ArgumentParser()
    m=p.add_mutually_exclusive_group(required=True)
    m.add_argument('--self-test',action='store_true')
    m.add_argument('--apply',action='store_true')
    a=p.parse_args()
    if a.self_test:return selftest()
    if os.geteuid()==0:raise SetupError('DO_NOT_RUN_AS_ROOT')
    verify_hub()
    phones=find_phones()
    print('ADB_IDENTIFIED: '+', '.join(k for k in PHONE_CODES if k in phones),flush=True)
    if len(phones)!=3:raise SetupError('THREE_ADB_PHONES_REQUIRED')
    failures={}
    for code in PHONE_CODES:
        try:pair_and_arm(code,phones[code])
        except SetupError as ex:
            failures[code]=str(ex)
            print(code.upper()+'_SETUP_STOPPED: '+str(ex),flush=True)
    # A freshly armed service should start a heartbeat within ~3 seconds.
    # On some devices it takes longer; OFFLINE here is not a final failure.
    time.sleep(5)
    hub_status()
    if failures:
        raise SetupError('PARTIAL_UI_SETUP__SEE_PER_DEVICE_ERRORS')
    print('ADB_UI_SETUP_COMPLETE__NO_RECORDING_STARTED',flush=True)
    print('VERIFY_HEARTBEAT_IN_60_SECONDS_IF_STILL_OFFLINE',flush=True)

if __name__=='__main__':
    try:main()
    except (SetupError,OSError,ValueError,ET.ParseError) as ex:
        print('ADB_0132_SETUP_STOPPED: '+str(ex)[:220],file=sys.stderr)
        sys.exit(2)
