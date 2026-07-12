"""Canonical events, sleep lifecycle, governance, and rebuildable views."""
from __future__ import annotations
import fnmatch, hashlib, json, os, tempfile, time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from .store_core import _lock_current_inode, _unlock, append, read_all, mark_tombstone


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str): raise TypeError(f"{field} must be a string")
    value=value.strip()
    if not value: raise ValueError(f"{field} must not be empty")
    return value

def _bool(value: Any, field: str) -> bool:
    if type(value) is not bool: raise TypeError(f"{field} must be a bool")
    return value

def _iso(value: Any):
    value=_text(value,"valid_from")
    try: parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError:
        try: parsed=datetime.combine(date.fromisoformat(value),datetime.min.time())
        except ValueError as exc: raise ValueError("valid_from must be a valid ISO-8601 date or datetime") from exc
    canonical=parsed.isoformat() if "T" in value or " " in value else parsed.date().isoformat()
    rank=parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    return canonical,rank

def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()

class DerivedViewsMixin:
    def _meta(self,name): return Path(self.base_dir)/".memkraft"/name
    def _canonical_events_path(self): return self._meta("events.jsonl")
    def _compiled_truth_path(self): return self._meta("compiled_truth.jsonl")
    def _policies_path(self): return self._meta("deny_policies.jsonl")
    def _audit_path(self): return self._meta("audit.jsonl")
    def _sleep_journal_path(self): return self._meta("sleep_journal.jsonl")
    def _governance_lock(self):
        p=self._meta("governance.lock"); p.parent.mkdir(parents=True,exist_ok=True)
        return _lock_current_inode(str(p),os.O_RDWR|os.O_CREAT)
    def _policies(self): return read_all(self._policies_path()).records
    def _denied(self,subject,key):
        token=f"{subject}:{key}"
        return any((p.get("subject")==subject and (p.get("key") is None or p.get("key")==key)) or (p.get("pattern") and (fnmatch.fnmatchcase(token,p["pattern"]) or fnmatch.fnmatchcase(key,p["pattern"]))) for p in self._policies())
    def _append_audit(self,row):
        operation_id=row.get("operation_id")
        if operation_id and any(r.get("operation_id")==operation_id for r in read_all(self._audit_path()).records): return None
        return append(self._audit_path(),{**row,"audit_seq":time.time_ns()})

    def append_event(self,subject_id,key,value,source=None,provenance=None,valid_from=None):
        if source is None and provenance is None: raise ValueError("source (or provenance) is required")
        s=_text(subject_id,"subject_id"); k=_text(key,"key")

        prov=_text(provenance,"provenance") if provenance is not None else None
        src=_text(source,"source") if source is not None else prov
        record={"subject_id":s,"key":k,"value":value,"source":src}
        if prov is not None: record["provenance"]=prov
        if valid_from is not None: record["valid_from"]=_iso(valid_from)[0]
        fd=self._governance_lock()
        try:
            if self._denied(s,k): raise PermissionError(f"memory denied for {s}:{k}")
            return append(self._canonical_events_path(),record)
        finally:_unlock(fd); os.close(fd)

    def compile_truth(self,dry_run=True):
        _bool(dry_run,"dry_run"); result=read_all(self._canonical_events_path()); winners={}; invalid=0
        for order,event in enumerate(result.records):
            if "value" not in event: invalid+=1; continue
            try:
                n=dict(event); n["subject_id"]=_text(event.get("subject_id"),"subject_id"); n["key"]=_text(event.get("key"),"key"); n["source"]=_text(event.get("source"),"source")
                if "provenance" in n: n["provenance"]=_text(n["provenance"],"provenance")
                rank=datetime.min
                if "valid_from" in n: n["valid_from"],rank=_iso(n["valid_from"])
            except (TypeError,ValueError): invalid+=1; continue
            if self._denied(n["subject_id"],n["key"]): continue
            ident=(n["subject_id"],n["key"]); score=(rank,order)
            if ident not in winners or score>winners[ident][0]: winners[ident]=(score,n)
        records=[]
        for ident in sorted(winners):
            e=winners[ident][1]; r={k:e[k] for k in ("subject_id","key","value","source")}
            for k in ("valid_from","provenance"):
                if k in e:r[k]=e[k]
            records.append(r)
        plan={"dry_run":dry_run,"skipped":result.skipped+invalid,"records":records}
        if dry_run:return plan
        target=self._compiled_truth_path(); target.parent.mkdir(parents=True,exist_ok=True); fd=_lock_current_inode(str(target)+".rebuild.lock",os.O_RDWR|os.O_CREAT); tmp=None
        try:
            h=tempfile.NamedTemporaryFile(dir=str(target.parent),prefix=target.name+".",suffix=".tmp",delete=False); tmp=Path(h.name); h.close()
            for r in records: append(tmp,{**r,"id":f"{r['subject_id']}:{r['key']}","created_at":"compiled"})
            os.replace(tmp,target)
        finally:
            if tmp: tmp.unlink(missing_ok=True)
            _unlock(fd); os.close(fd)
        plan["applied"]=True; return plan

    def sleep(self,strategy="default",dry_run=True):
        strategy=_text(strategy,"strategy"); _bool(dry_run,"dry_run")
        if strategy!="default": raise ValueError("strategy must be 'default'")
        truth=self.compile_truth(); raw=read_all(self._canonical_events_path(),True).records; policies=self._policies()
        tx=_digest({"events":raw,"policies":policies,"strategy":strategy})
        base={"schema_version":1,"dry_run":dry_run,"strategy":strategy,"steps":["compile_truth"],"transaction_id":tx,"event_count":sum(not r.get("tombstone") for r in raw),"record_count":len(truth["records"]),"skipped":truth["skipped"],"records":truth["records"],"applied":False,"status":"planned"}
        if dry_run:return base
        fd=self._governance_lock()
        try:
            # Re-plan under the governance lock: events or policies may have
            # changed after the optimistic preview above.
            base=self.sleep(strategy=strategy,dry_run=True); tx=base["transaction_id"]
            if any(r.get("transaction_id")==tx for r in read_all(self._sleep_journal_path()).records): return {**base,"status":"already_applied"}
            self.compile_truth(False)
            append(self._sleep_journal_path(),{"action":"sleep","source":"sleep","strategy":strategy,"transaction_id":tx,"record_count":base["record_count"],"audit_seq":time.time_ns()})
            return {**base,"applied":True,"status":"applied"}
        finally:_unlock(fd); os.close(fd)

    def do_not_remember(self,subject=None,key=None,pattern=None,dry_run=True):
        _bool(dry_run,"dry_run")
        if pattern is not None:
            if subject is not None or key is not None: raise ValueError("pattern is mutually exclusive with subject/key")
            pattern=_text(pattern,"pattern")
            if any(c in pattern for c in "[]\\"): raise ValueError("pattern supports only minimal glob '*' and '?'")
            policy={"pattern":pattern}
        else:
            if subject is None: raise ValueError("subject or pattern is required")
            subject=_text(subject,"subject"); policy={"subject":subject}
            if key is not None: policy["key"]=_text(key,"key")
        policy["id"]=_digest(policy); plan={"schema_version":1,"dry_run":dry_run,"policy":policy,"status":"planned"}
        if dry_run:return plan
        fd=self._governance_lock()
        try:
            exists=any(p.get("id")==policy["id"] for p in self._policies())
            if not exists: append(self._policies_path(),policy)
            audit={"action":"do_not_remember","source":"governance","operation_id":policy["id"],**{k:v for k,v in policy.items() if k!="id"}}
            self._append_audit(audit)
            if exists: return {**plan,"status":"already_applied"}
            if self._compiled_truth_path().exists(): self.compile_truth(False)
            return {**plan,"status":"applied"}
        finally:_unlock(fd); os.close(fd)

    def forget(self,target,dry_run=True):
        _bool(dry_run,"dry_run")
        rows=read_all(self._canonical_events_path()).records
        subject=key=None
        if isinstance(target,str):
            target=_text(target,"target"); ids=[r["id"] for r in rows if r.get("id")==target]; shown=target
        elif isinstance(target,dict):
            subject=target.get("subject",target.get("subject_id")); key=target.get("key")
            if subject is None: raise ValueError("target requires subject")
            subject=_text(subject,"subject"); key=_text(key,"key") if key is not None else None
            ids=[r["id"] for r in rows if r.get("subject_id")==subject and (key is None or r.get("key")==key)]; shown={"subject":subject,**({"key":key} if key else {})}
        else: raise TypeError("target must be a record id or selector dict")
        plan={"schema_version":1,"dry_run":dry_run,"target":shown,"matched":len(ids),"record_ids":ids,"status":"planned"}
        if dry_run:return plan
        fd=self._governance_lock()
        try:
            current=read_all(self._canonical_events_path()).records
            allrows=read_all(self._canonical_events_path(),True).records
            tombstoned={r.get("tombstone_of") for r in allrows if r.get("tombstone") is True}
            if isinstance(target,dict):
                # A selector operation is defined by its complete source-record
                # set, including records tombstoned by an interrupted attempt.
                # Build that set under the governance lock so every retry gets
                # the same operation id and one complete audit record.
                ids=[r["id"] for r in allrows if not r.get("tombstone") and r.get("subject_id")==subject and (key is None or r.get("key")==key)]
                live_ids=[i for i in ids if i not in tombstoned]
            else:
                live={r.get("id") for r in current}; ids=[i for i in ids if i in live]
                live_ids=ids
                if not ids and target in tombstoned: ids=[target]
            if not ids:
                return {**plan,"matched":0,"record_ids":[],"status":"not_found"}
            for i in live_ids: mark_tombstone(self._canonical_events_path(),i)
            op=_digest({"action":"forget","record_ids":sorted(ids)})
            self._append_audit({"action":"forget","source":"governance","operation_id":op,"target":shown,"record_ids":ids})
            status="applied" if live_ids else "already_forgotten"
            if live_ids and self._compiled_truth_path().exists(): self.compile_truth(False)
            return {**plan,"matched":len(ids),"record_ids":ids,"status":status}
        finally:_unlock(fd); os.close(fd)

    def export_memory(self,include_tombstoned=False):
        _bool(include_tombstoned,"include_tombstoned"); rows=read_all(self._canonical_events_path(),include_tombstoned).records
        return [dict(r) for r in rows if include_tombstoned or (not r.get("tombstone") and not self._denied(r.get("subject_id"),r.get("key")))]
    def timeline(self,subject_id=None,include_tombstoned=False):
        _bool(include_tombstoned,"include_tombstoned")
        if subject_id is not None: subject_id=_text(subject_id,"subject_id")
        return [r for r in self.export_memory(include_tombstoned) if subject_id is None or r.get("subject_id")==subject_id]
    def audit_log(self,action=None,subject=None,limit=None):
        if action is not None: action=_text(action,"action")
        if subject is not None: subject=_text(subject,"subject")
        if limit is not None:
            if type(limit) is not int: raise TypeError("limit must be an int")
            if limit<=0: raise ValueError("limit must be positive")
        rows=read_all(self._audit_path()).records+read_all(self._sleep_journal_path()).records
        rows=[r for r in rows if (action is None or r.get("action")==action) and (subject is None or r.get("subject")==subject)]
        rows.sort(key=lambda r:(r.get("audit_seq",0),r.get("created_at","")),reverse=True)
        return rows[:limit] if limit else rows
    def current_truth(self,subject_id):
        subject_id=_text(subject_id,"subject_id")
        all_events=read_all(self._canonical_events_path(),True).records
        tombstoned={r.get("tombstone_of") for r in all_events if r.get("tombstone") is True}
        def safe(row):
            if self._denied(subject_id,row.get("key")): return False
            matches=[e for e in all_events if not e.get("tombstone") and e.get("subject_id")==subject_id and e.get("key")==row.get("key") and all(e.get(k)==row.get(k) for k in ("value","source","valid_from","provenance"))]
            # Standalone/legacy compiled files remain readable. If canonical
            # provenance exists, however, a tombstone of that exact compiled
            # winner fails closed rather than exposing stale sensitive data.
            return not matches or any(e.get("id") not in tombstoned for e in matches)
        return {r["key"]:r["value"] for r in read_all(self._compiled_truth_path()).records if r.get("subject_id")==subject_id and "key" in r and "value" in r and safe(r)}
