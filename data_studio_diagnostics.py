"""Structured in-memory and JSONL diagnostics for desktop and headless flows."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json, uuid

class DiagnosticLog:
    def __init__(self, events=None, jsonl_path: str | Path | None = None, max_events: int = 2000):
        self.events = events if isinstance(events,list) else []
        self.jsonl_path = Path(jsonl_path) if jsonl_path else None
        self.max_events=max_events
    def emit(self, level: str, component: str, action: str, message: str, **context: Any) -> dict[str, Any]:
        event={"timestamp_utc":datetime.now(timezone.utc).isoformat(),"correlation_id":context.pop("correlation_id",uuid.uuid4().hex[:12]),"level":level.upper(),"component":component,"action":action,"message":message,"context":context}
        self.events.append(event)
        if len(self.events)>self.max_events: del self.events[:-self.max_events]
        if self.jsonl_path:
            self.jsonl_path.parent.mkdir(parents=True,exist_ok=True)
            with self.jsonl_path.open('a',encoding='utf-8') as handle: handle.write(json.dumps(event,default=str,ensure_ascii=False)+'\n')
        return event
    def info(self, component, action, message, **context): return self.emit("INFO",component,action,message,**context)
    def warning(self, component, action, message, **context): return self.emit("WARNING",component,action,message,**context)
    def error(self, component, action, message, **context): return self.emit("ERROR",component,action,message,**context)
