from collections import Counter

def assess(db, scope, device_ids):
    devices=set(device_ids)
    if not devices or scope is None:
        raise ValueError('NIGHT_AND_DEVICES_REQUIRED')

    verified={
        (d,s,h)
        for d,s,h in db.execute(
            "SELECT device_id,segment_id,sha256 FROM segments WHERE state='INGEST_VERIFIED'"
        )
        if d in devices and scope.allows(d,s,h)
    }

    ok=set()
    states=Counter()

    for d,s,h,state in db.execute(
        'SELECT device_id,segment_id,video_sha256,state FROM jobs'
    ):
        if d in devices and scope.allows(d,s,h):
            states[state]+=1
            if state=='SUCCEEDED':
                ok.add((d,s,h))

    missing=sorted(devices-{d for d,_,_ in verified})
    unresolved=sum(states[k] for k in ('WAITING','RUNNING','FAILED'))

    return dict(
        analysis_rows_complete=bool(verified) and not missing and not (verified-ok) and not unresolved,
        verified_segments=len(verified),
        succeeded_segments=len(verified & ok),
        missing_devices=missing,
        not_succeeded_segments=len(verified-ok),
        current_unresolved_jobs=unresolved,
        states=dict(states),
        full_night_accepted=False,
        deletion_authorized=False
    )
