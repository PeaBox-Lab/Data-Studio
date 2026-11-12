"""Shared scheduling, dependency, and retry policy primitives."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
import time

def parse_utc(value):
    if not value:return None
    parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)

def next_schedule_time(schedule: str, anchor: datetime):
    if schedule=="Hourly":return anchor+timedelta(hours=1)
    if schedule=="Daily":return anchor+timedelta(days=1)
    if schedule=="Weekly":return anchor+timedelta(days=7)
    if schedule=="Monthly":
        year=anchor.year+(anchor.month==12); month=1 if anchor.month==12 else anchor.month+1
        probe=datetime(year+(month==12),1 if month==12 else month+1,1,tzinfo=timezone.utc)
        last=(probe-timedelta(days=1)).day
        return anchor.replace(year=year,month=month,day=min(anchor.day,last))
    return None

def dependency_status(job: dict[str,Any], jobs: dict[str,dict[str,Any]]):
    blocked=[]
    for dep_id in job.get('depends_on',[]):
        dep=jobs.get(dep_id)
        if dep is None: blocked.append(f"{dep_id}: missing")
        elif dep.get('last_status')!='success': blocked.append(f"{dep_id}: {dep.get('last_status','never run')}")
    return not blocked, blocked

def execute_with_retry(action: Callable[[],Any], max_retries: int=0, retry_delay_seconds: float=0.0, sleep: Callable[[float],None]=time.sleep):
    attempts=[]
    for number in range(1,max(0,int(max_retries))+2):
        started=time.monotonic()
        try:
            value=action(); attempts.append({"attempt":number,"status":"success","duration_seconds":round(time.monotonic()-started,3),"error":None}); return value,attempts,None
        except Exception as exc:
            attempts.append({"attempt":number,"status":"failed","duration_seconds":round(time.monotonic()-started,3),"error":str(exc)})
            if number <= max_retries:
                if retry_delay_seconds: sleep(max(0,float(retry_delay_seconds)))
                continue
            return None,attempts,str(exc)
