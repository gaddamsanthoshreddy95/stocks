"""Display-only validity: never upgrades a saved decision or sends market requests."""
import pandas as pd


def entry_status(row, now):
    decision=row.get('final_decision') or row.get('Decision') or row.get('decision')
    if decision!='APPROVED':
        return 'RESEARCH / NOT APPROVED'
    expiry=row.get('entry_valid_until') or row.get('Entry valid until')
    try:
        expiry=pd.Timestamp(expiry)
        now=pd.Timestamp(now)
        if expiry.tz is None or now.tz is None or pd.isna(expiry):
            return 'UNVERIFIED — RESCAN'
        remaining=(expiry-now).total_seconds()
        if remaining<0:return 'EXPIRED — RESCAN'
        if remaining>30:return 'UNVERIFIED — RESCAN'
        return 'APPROVED SNAPSHOT — RECHECK BEFORE ENTRY'
    except (ValueError,TypeError):
        return 'UNVERIFIED — RESCAN'
