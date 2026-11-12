"""Background-task foundation independent of the GUI toolkit."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Lock
from typing import Any, Callable
import uuid

@dataclass
class TaskRecord:
    id: str; name: str; status: str="queued"; progress: int=0; message: str=""; created_utc: str=field(default_factory=lambda:datetime.now(timezone.utc).isoformat()); started_utc: str|None=None; finished_utc: str|None=None; error: str|None=None; result: Any=None

class TaskManager:
    def __init__(self,max_workers: int=2): self.executor=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix='data-studio'); self.records={}; self._cancel={}; self._lock=Lock()
    def submit(self,name: str, fn: Callable[...,Any], *args, **kwargs) -> tuple[TaskRecord,Future]:
        record=TaskRecord(uuid.uuid4().hex[:12],name); cancel=Event()
        with self._lock:self.records[record.id]=record;self._cancel[record.id]=cancel
        def progress(value:int,message:str=''):
            with self._lock: record.progress=max(0,min(100,int(value)));record.message=message
        def runner():
            record.status='running';record.started_utc=datetime.now(timezone.utc).isoformat()
            try:
                record.result=fn(*args,progress=progress,cancel_event=cancel,**kwargs);record.status='cancelled' if cancel.is_set() else 'completed';return record.result
            except Exception as exc:record.status='failed';record.error=str(exc);raise
            finally:record.finished_utc=datetime.now(timezone.utc).isoformat();record.progress=100 if record.status=='completed' else record.progress
        return record,self.executor.submit(runner)
    def cancel(self,task_id:str)->bool:
        event=self._cancel.get(task_id)
        if not event:return False
        event.set();return True
    def shutdown(self):self.executor.shutdown(wait=False,cancel_futures=True)
