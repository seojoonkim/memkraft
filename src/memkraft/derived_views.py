"""Canonical events, sleep lifecycle, governance, and rebuildable views."""
from __future__ import annotations
import fnmatch, hashlib, json, os, tempfile, time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from .store_core import _lock_current_inode, _lock_shared, _unlock, append, compact, read_all, mark_tombstone, mark_tombstones

# Preserve the historical fault-injection seam used by downstream tests and
# plugins. Normal execution uses the O(n) batch path; an explicit monkeypatch
# of ``mark_tombstone`` opts into the legacy per-record path so interrupted
# attempts can still be simulated and repaired idempotently.
_ORIGINAL_MARK_TOMBSTONE = mark_tombstone


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

_MATCH_FIELDS=("key","value","source","valid_from","provenance")
_UNCANONICAL=object()

def _canonical(value: Any):
    """Hashable stand-in for a JSON-like value; equal values canonicalise equal."""
    if isinstance(value,list):
        parts=[]
        for item in value:
            c=_canonical(item)
            if c is _UNCANONICAL: return _UNCANONICAL
            parts.append(c)
        return ("l",tuple(parts))
    if isinstance(value,dict):
        parts=[]
        for k,v in value.items():
            ck=_canonical(k); cv=_canonical(v)
            if ck is _UNCANONICAL or cv is _UNCANONICAL: return _UNCANONICAL
            parts.append((ck,cv))
        return ("d",frozenset(parts))
    try: hash(value)
    except TypeError: return _UNCANONICAL
    return ("s",value)

def _match_signature(row):
    """Canonical signature over the fields old equality compared, or _UNCANONICAL."""
    parts=[]
    for field in _MATCH_FIELDS:
        c=_canonical(row.get(field))
        if c is _UNCANONICAL: return _UNCANONICAL
        parts.append(c)
    return tuple(parts)

def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()

class DerivedViewsMixin:
    def _meta(self,name): return Path(self.base_dir)/".memkraft"/name
    def _canonical_events_path(self): return self._meta("events.jsonl")
    def _compiled_truth_path(self): return self._meta("compiled_truth.jsonl")
    def _policies_path(self): return self._meta("deny_policies.jsonl")
    def _audit_path(self): return self._meta("audit.jsonl")
    def _sleep_journal_path(self): return self._meta("sleep_journal.jsonl")
    def _governance_lock(self,timeout_s=None):
        """Take the governance lock, blocking by default.

        ``timeout_s`` bounds the wait and raises ``StoreBusy`` instead (§7.4):
        a MemKraft write inside a fail-closed hook must not turn a lock wait
        into a denied user tool call. The default keeps every existing caller
        byte-for-byte unchanged.
        """
        p=self._meta("governance.lock"); p.parent.mkdir(parents=True,exist_ok=True)
        return _lock_current_inode(str(p),os.O_RDWR|os.O_CREAT,timeout_s)
    def _governance_read_lock(self):
        """Lock an existing governance inode without creating any path."""
        p=self._meta("governance.lock")
        while True:
            try: fd=os.open(str(p),os.O_RDONLY)
            except FileNotFoundError: return None
            try:
                _lock_shared(fd); opened=os.fstat(fd)
                try: current=os.stat(str(p))
                except FileNotFoundError:
                    _unlock(fd); os.close(fd); return None
                if (opened.st_dev,opened.st_ino)==(current.st_dev,current.st_ino): return fd
                _unlock(fd); os.close(fd)
            except BaseException:
                os.close(fd); raise
    def _policies(self): return read_all(self._policies_path()).records
    @staticmethod
    def _denied_by(policies,subject,key):
        token=f"{subject}:{key}"
        return any((p.get("subject")==subject and (p.get("key") is None or p.get("key")==key)) or (p.get("pattern") and (fnmatch.fnmatchcase(token,p["pattern"]) or fnmatch.fnmatchcase(key,p["pattern"]))) for p in policies)
    def _denied(self,subject,key):
        return self._denied_by(self._policies(),subject,key)
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
        policies=self._policies()
        for order,event in enumerate(result.records):
            if "value" not in event: invalid+=1; continue
            try:
                n=dict(event); n["subject_id"]=_text(event.get("subject_id"),"subject_id"); n["key"]=_text(event.get("key"),"key"); n["source"]=_text(event.get("source"),"source")
                if "provenance" in n: n["provenance"]=_text(n["provenance"],"provenance")
                rank=datetime.min
                if "valid_from" in n: n["valid_from"],rank=_iso(n["valid_from"])
            except (TypeError,ValueError): invalid+=1; continue
            if self._denied_by(policies,n["subject_id"],n["key"]): continue
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
            sleeps=[r for r in read_all(self._sleep_journal_path()).records if r.get("action")=="sleep"]
            if sleeps and sleeps[-1].get("transaction_id")==tx: return {**base,"status":"already_applied"}
            self.compile_truth(False)
            event_ids=sorted(r["id"] for r in read_all(self._canonical_events_path(),True).records if not r.get("tombstone") and isinstance(r.get("id"),str))
            append(self._sleep_journal_path(),{"action":"sleep","source":"sleep","strategy":strategy,"transaction_id":tx,"event_count":base["event_count"],"event_ids":event_ids,"record_count":base["record_count"],"audit_seq":time.time_ns()})
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
            return {**plan,"status":"applied"}
        finally:_unlock(fd); os.close(fd)

    def forget(self,target,dry_run=True):
        _bool(dry_run,"dry_run")
        # A goal lives in the execution log, not in the memory store (§14.4).
        if isinstance(target,dict) and "goal_id" in target:
            return self._execution_forget_goal(target["goal_id"],dry_run)
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
            if mark_tombstone is _ORIGINAL_MARK_TOMBSTONE:
                mark_tombstones(self._canonical_events_path(),live_ids)
            else:
                for record_id in live_ids:
                    mark_tombstone(self._canonical_events_path(),record_id)
            op=_digest({"action":"forget","record_ids":sorted(ids)})
            self._append_audit({"action":"forget","source":"governance","operation_id":op,"target":shown,"record_ids":ids})
            status="applied" if live_ids else "already_forgotten"
            return {**plan,"matched":len(ids),"record_ids":ids,"status":status}
        finally:_unlock(fd); os.close(fd)

    def forget_candidates(self, *, candidate_id=None, session_id=None, dry_run=True):
        """Preview: tombstone candidates selected by id or by their session."""
        _bool(dry_run,"dry_run")
        if (candidate_id is None) == (session_id is None):
            raise ValueError("exactly one of candidate_id or session_id is required")
        selector = ({"candidate_id":_text(candidate_id,"candidate_id")} if candidate_id is not None
                    else {"session_id":_text(session_id,"session_id")})
        path=self._meta("candidates.jsonl")
        def selected(row):
            return row.get("kind")=="candidate_memory" and all(row.get(k)==v for k,v in selector.items())
        ids=[str(r.get("candidate_id",r.get("id"))) for r in read_all(path).records if selected(r)]
        plan={"schema_version":1,"dry_run":dry_run,"selector":selector,"matched":len(ids),"candidate_ids":ids,"status":"planned"}
        if dry_run:return plan
        fd=self._governance_lock()
        try:
            allrows=read_all(path,True).records
            source=[r for r in allrows if not r.get("tombstone") and selected(r)]
            ids=[str(r.get("candidate_id",r.get("id"))) for r in source]
            tombstoned={r.get("tombstone_of") for r in allrows if r.get("tombstone") is True}
            live_ids=[i for i in ids if i not in tombstoned]
            if not ids:return {**plan,"matched":0,"candidate_ids":[],"status":"not_found"}
            for candidate in live_ids:mark_tombstone(path,candidate)
            op=_digest({"action":"forget_candidates","selector":selector,"candidate_ids":sorted(ids)})
            self._append_audit({"action":"forget_candidates","source":"governance","operation_id":op,"selector":selector,"candidate_ids":ids})
            return {**plan,"matched":len(ids),"candidate_ids":ids,"status":"applied" if live_ids else "already_forgotten"}
        finally:_unlock(fd); os.close(fd)

    def compact_memory(self,dry_run=True):
        """Preview: compact only the active local event and candidate sidecars."""
        _bool(dry_run,"dry_run")
        paths={"events.jsonl":self._canonical_events_path(),"candidates.jsonl":self._meta("candidates.jsonl")}
        if dry_run:
            stores={name:_compaction_counts(path) for name,path in paths.items()}
            return {"schema_version":1,"dry_run":True,"stores":stores,"status":"planned"}
        if not any(path.exists() for path in paths.values()):
            stores={name:_compaction_counts(path) for name,path in paths.items()}
            return {"schema_version":1,"dry_run":False,"stores":stores,"status":"applied"}
        fd=self._governance_lock()
        try:
            compiled=self._compiled_truth_path(); filtered=None
            if paths["events.jsonl"].exists() and compiled.exists():
                canonical=read_all(paths["events.jsonl"],True); policies=read_all(self._policies_path()); prior=read_all(compiled)
                if canonical.skipped or policies.skipped or prior.skipped: filtered=[]
                else:
                    tombstoned={r.get("tombstone_of") for r in canonical.records if r.get("tombstone") is True}
                    sources=[r for r in canonical.records if not r.get("tombstone")]
                    def keep(row):
                        subject=row.get("subject_id"); key=row.get("key")
                        if self._denied_by(policies.records,subject,key): return False
                        matches=[e for e in sources if e.get("subject_id")==subject and e.get("key")==key and all(e.get(k)==row.get(k) for k in ("value","source","valid_from","provenance"))]
                        return not matches or any(e.get("id") not in tombstoned for e in matches)
                    filtered=[r for r in prior.records if keep(r)]
            stores={name:_compact_dict(compact(path)) for name,path in paths.items()}
            if filtered is not None:_atomic_records(compiled,filtered)
            return {"schema_version":1,"dry_run":False,"stores":stores,"status":"applied"}
        finally:_unlock(fd); os.close(fd)

    def export_memory(self,include_tombstoned=False):
        _bool(include_tombstoned,"include_tombstoned"); rows=read_all(self._canonical_events_path(),include_tombstoned).records
        return [dict(r) for r in rows if (include_tombstoned or not r.get("tombstone")) and not self._denied(r.get("subject_id"),r.get("key"))]
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
    def truth_status(self):
        fd=self._governance_read_lock()
        try:
            raw=read_all(self._canonical_events_path(),True).records
            policies=read_all(self._policies_path()).records; strategy="default"
            live=_digest({"events":raw,"policies":policies,"strategy":strategy})
            sleeps=[r for r in read_all(self._sleep_journal_path()).records if r.get("action")=="sleep"]
            sleeps.sort(key=lambda r:(r.get("audit_seq",0),r.get("created_at","")),reverse=True)
            latest=sleeps[0] if sleeps else None; applied=latest.get("transaction_id") if latest else None
            stale=applied!=live
            current_ids={r["id"] for r in raw if not r.get("tombstone") and isinstance(r.get("id"),str)}
            if not stale: pending=0
            elif latest and isinstance(latest.get("event_ids"),list): pending=len(current_ids-set(latest["event_ids"]))
            else:
                # Old journals have only a scalar count, which cannot identify
                # events after compaction. Deterministically treat every current
                # raw source event as pending rather than under-reporting.
                pending=len(current_ids)
            return {"schema_version":1,"stale":stale,"live_transaction_id":live,"applied_transaction_id":applied,"pending_event_count":pending}
        finally:
            if fd is not None: _unlock(fd); os.close(fd)
    def current_truth(self,subject_id):
        subject_id=_text(subject_id,"subject_id")
        fd=self._governance_read_lock()
        try:
            policies=read_all(self._policies_path())
            if policies.skipped:return {}
            canonical=self._cached_read(self._canonical_events_path(),"_event_snapshot_cache",True)
            if canonical.skipped:return {}
            all_events=canonical.records
            tombstoned={r.get("tombstone_of") for r in all_events if r.get("tombstone") is True}
            # One pass over the events: signature -> "some matching source is still live".
            # Falls back to the literal scan if any value resists canonicalisation.
            live={}; indexed=True
            for e in all_events:
                if e.get("tombstone") or e.get("subject_id")!=subject_id: continue
                sig=_match_signature(e)
                if sig is _UNCANONICAL: indexed=False; break
                live[sig]=live.get(sig,False) or e.get("id") not in tombstoned
            def scan(row):
                matches=[e for e in all_events if not e.get("tombstone") and e.get("subject_id")==subject_id and e.get("key")==row.get("key") and all(e.get(k)==row.get(k) for k in ("value","source","valid_from","provenance"))]
                return not matches or any(e.get("id") not in tombstoned for e in matches)
            def safe(row):
                if self._denied_by(policies.records,subject_id,row.get("key")):return False
                if not indexed: return scan(row)
                sig=_match_signature(row)
                if sig is _UNCANONICAL: return scan(row)
                return live.get(sig,True)
            result=self._cached_read(self._compiled_truth_path(),"_truth_snapshot_cache")
            records=[] if result.skipped else result.records
            return {r["key"]:r["value"] for r in records if r.get("subject_id")==subject_id and "key" in r and "value" in r and safe(r)}
        finally:
            if fd is not None:_unlock(fd); os.close(fd)

    def _cached_read(self,path,attribute,include_tombstoned=False):
        path=Path(path)
        try:
            st=path.stat(); stamp=(st.st_mtime_ns,st.st_size); identity=(st.st_dev,st.st_ino)
        except OSError:
            return read_all(path,include_tombstoned)
        cache=getattr(self,attribute,None)
        if cache is not None and cache[0]==stamp and cache[1]==identity:return cache[2]
        try:result=read_all(path,include_tombstoned)
        except (OSError,UnicodeError,ValueError):
            from .store_core import ReadResult
            result=ReadResult([],1)
        try:
            after=path.stat(); current=((after.st_mtime_ns,after.st_size),(after.st_dev,after.st_ino))
        except OSError:return read_all(path,include_tombstoned)
        if current!=(stamp,identity):return self._cached_read(path,attribute,include_tombstoned)
        setattr(self,attribute,(stamp,identity,result)); return result


def _compact_dict(result):
    return {field:getattr(result,field) for field in ("kept","removed_tombstoned","removed_markers","removed_corrupt")}


def _atomic_records(target,records):
    """Atomically replace a snapshot with only filtered prior rows."""
    target=Path(target); tmp=None
    fd=_lock_current_inode(str(target)+".rebuild.lock",os.O_RDWR|os.O_CREAT)
    try:
        h=tempfile.NamedTemporaryFile(dir=str(target.parent),prefix=target.name+".",suffix=".tmp",delete=False,mode="w",encoding="utf-8")
        tmp=Path(h.name)
        with h:
            for row in records:h.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
        os.replace(tmp,target)
    finally:
        if tmp:tmp.unlink(missing_ok=True)
        _unlock(fd); os.close(fd)


def _compaction_counts(path):
    path=Path(path)
    try: lines=path.read_bytes().splitlines(keepends=True)
    except FileNotFoundError: lines=[]
    records=[]; corrupt=0
    for line in lines:
        text=line.decode("utf-8",errors="replace").strip()
        if not text: continue
        try: row=json.loads(text)
        except json.JSONDecodeError: row=None
        if not isinstance(row,dict): row=None
        if row is None:
            if line.strip(): corrupt+=1
        else: records.append(row)
    markers=[r for r in records if r.get("tombstone")]
    targets={r.get("tombstone_of") for r in markers if r.get("tombstone_of") is not None}
    removed=sum(1 for r in records if not r.get("tombstone") and r.get("id") in targets)
    kept=sum(1 for r in records if not r.get("tombstone") and r.get("id") not in targets)
    return {"kept":kept,"removed_tombstoned":removed,"removed_markers":len(markers),"removed_corrupt":corrupt}
