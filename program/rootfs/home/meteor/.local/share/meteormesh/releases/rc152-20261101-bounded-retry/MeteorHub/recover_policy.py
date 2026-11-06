"""Bounded recovery for already-stored MeteorMesh analysis jobs.

Exactly one retry after an ordinary failure; at most two claims total for stale
worker jobs too. Scope validation prevents unrelated old/test footage reruns.
"""
from __future__ import annotations
import time
from hub_workflow import DEFAULT_JOB_LEASE_SECONDS, utc_now


def recover(db, *, scope):
    if scope is None:
        raise ValueError('APPROVED_NIGHT_SCOPE_REQUIRED')
    threshold = time.time() - DEFAULT_JOB_LEASE_SECONDS
    counts = {'failed_requeued':0, 'stale_requeued':0, 'stale_exhausted':0, 'waiting_exhausted':0}
    db.execute('BEGIN IMMEDIATE')
    try:
        rows = db.execute("""
            SELECT id,device_id,segment_id,video_sha256,state,attempts
            FROM jobs WHERE
                (state='FAILED' AND attempts < 2)
                OR (state='RUNNING' AND claimed_at IS NOT NULL
                    AND claimed_at < ?)
                OR (state='WAITING' AND attempts >= 2)
            ORDER BY id""", (threshold,)).fetchall()
        for jid, dev, seg, sha, state, attempts in rows:
            if not scope.allows_queue(dev, seg, sha):
                continue
            if state == 'FAILED':
                cur = db.execute("""UPDATE jobs SET state='WAITING',
                    claimed_at=NULL, last_error=NULL, finished_utc=NULL
                    WHERE id=? AND state='FAILED' AND attempts<2""", (jid,))
                counts['failed_requeued'] += cur.rowcount
            elif state == 'WAITING':
                cur = db.execute("""UPDATE jobs SET state='FAILED',
                    claimed_at=NULL, finished_utc=?,
                    last_error='Retry limit exhausted (previously requeued)'
                    WHERE id=? AND state='WAITING' AND attempts>=2""",
                    (utc_now(), jid))
                counts['waiting_exhausted'] += cur.rowcount
            elif attempts < 2:
                cur = db.execute("""UPDATE jobs SET state='WAITING',
                    claimed_at=NULL, last_error='Recovered expired worker lease'
                    WHERE id=? AND state='RUNNING' AND attempts<2""", (jid,))
                counts['stale_requeued'] += cur.rowcount
            else:
                cur = db.execute("""UPDATE jobs SET state='FAILED',
                    claimed_at=NULL, finished_utc=?,
                    last_error='Retry limit exhausted after worker interruption'
                    WHERE id=? AND state='RUNNING' AND attempts>=2""",
                    (utc_now(), jid))
                counts['stale_exhausted'] += cur.rowcount
        db.commit()
        return counts
    except BaseException:
        db.rollback()
        raise
