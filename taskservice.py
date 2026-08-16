"""taskservice.py — لایهٔ متمرکز، تراکنشی و حسابرسی‌شدهٔ mutationهای تسک (فاز ۱: Operational Integrity).

هدف: idempotency، mutationِ اتمیک + audit در یک تراکنش، append-only audit، و enforceِ authorization در کد.
lifecycle همان open/done می‌مانَد (بدون state machine جدید). این ماژول تنها نویسندهٔ مجازِ wt_tasks است.

هیچ prompt/پاسخ/متن خامِ پیام/راز در audit یا inbound-events ذخیره نمی‌شود؛ فقط شناسه‌های حسابرسی + طول/هش.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field

import db

# ---------- allowlistها ----------
SOURCES = {"telegram", "system", "delivery"}   # delivery = ledgerِ ارسالِ خروجی (Workstream B)
ROLES = {"primary_admin", "admin", "ig_admin", "staff", "system"}
_ADMIN_ROLES = {"admin", "primary_admin", "system"}            # مجاز به create/update (2A: primary==admin)
OPERATIONS = {"task_create", "task_update", "task_mark_done",
              "crawl_refresh", "crawl_escalation_bump",         # + maintenanceِ خزش (Workstream E)
              "task_transition", "task_reassign", "task_set_priority",  # + چرخهٔ عملیاتیِ اصلی (Core Release)
              "task_set_deadline", "task_set_verification_mode"}
EVENT_TYPES = {"task_created", "task_updated", "task_marked_done", "task_noop",
               "crawl_task_refreshed", "crawl_task_escalation_reference_updated",
               "task_state_changed", "task_reassigned", "task_priority_set",
               "task_deadline_set", "task_verification_mode_set"}
TASK_KINDS = {"staff", "crawl", "ig_plan", "system"}           # Workstream G (allowlist در کد؛ LLM حق تولید ندارد)

# ---------- چرخهٔ زندگیِ سادهٔ تسکِ انسانی (Core Operational Release) ----------
LIFECYCLE_STATES = {"open", "in_progress", "blocked", "claimed_done", "verified_done", "reopened", "cancelled"}
TERMINAL_STATES = {"verified_done", "cancelled"}
# جدولِ گذارِ مجاز (source of truth؛ LLM حق ندارد). گذارِ نامعتبر fail-closed.
TRANSITIONS = {
    "open":          {"in_progress", "blocked", "claimed_done", "verified_done", "cancelled"},
    "in_progress":   {"blocked", "claimed_done", "verified_done", "cancelled"},
    "blocked":       {"in_progress", "cancelled"},
    "claimed_done":  {"verified_done", "reopened", "cancelled"},
    "reopened":      {"in_progress", "blocked", "claimed_done", "verified_done", "cancelled"},
    "verified_done": set(),
    "cancelled":     set(),
}
VERIFICATION_MODES = {"none", "manager", "automatic"}
PRIORITIES = {"normal", "high", "urgent"}
SOURCE_FEATURES = {"general", "website", "instagram"}
_STATE_TS = {  # ستونِ زمانی که با ورود به هر state پر می‌شود
    "in_progress": "started_ts", "claimed_done": "claimed_done_ts",
    "verified_done": "verified_ts", "cancelled": "cancelled_ts",
}


def lifecycle_of(lifecycle_state, status) -> str:
    """source of truth: اگر lifecycle_state پر بود همان؛ وگرنه projection از statusِ legacy (done→verified_done)."""
    if lifecycle_state:
        return lifecycle_state
    return "verified_done" if status == "done" else "open"


def _legacy_status_for(state) -> str:
    """همگام‌سازیِ statusِ legacy با state (backward-compat): terminalهای done → 'done'، بقیه → 'open'."""
    return "done" if state in TERMINAL_STATES else "open"
SYSTEM_ACTOR_ID = 0                                            # actorِ مشخصِ jobهای داخلی (نه خالی)
_LEASE_SEC = 120
_MAX_ATTEMPTS = 5
INBOUND_STATUSES = {"processing", "succeeded", "failed_retryable", "failed_permanent"}


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _iso(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat(timespec="milliseconds")


def _jdump(obj) -> str | None:
    """serializationِ پایدار/deterministic؛ None → None (نه رشتهٔ 'null')."""
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text_fingerprint(text) -> dict:
    """اثرِ غیرحساسِ متنِ تسک برای audit: طول + هشِ کوتاه (نه خودِ متن)."""
    s = text or ""
    return {"len": len(s), "sha8": hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]}


def _mut_log(rec: dict) -> None:
    try:  # فقط متریک؛ هیچ متنِ خام/محتوایی
        print("[mut] " + json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
    except Exception:  # noqa: BLE001
        pass


# ---------- context و نتیجه ----------
@dataclass
class MutationContext:
    actor_id: int
    actor_role: str
    source: str
    operation: str
    source_event_id: str = ""
    request_id: str = ""
    idempotency_key: str = ""
    reason_code: str = ""
    ts: str = field(default_factory=_utc_now)

    def error(self) -> str:
        """اگر context معتبر نبود، پیامِ خطا؛ وگرنه ''. authz را LLM تعیین نمی‌کند — actor/role از کد می‌آید."""
        if self.actor_id is None:
            return "actor_id missing"
        if self.actor_role not in ROLES:
            return f"actor_role not allowed: {self.actor_role!r}"
        if self.source not in SOURCES:
            return f"source not allowed: {self.source!r}"
        if self.operation not in OPERATIONS:
            return f"operation not allowed: {self.operation!r}"
        # staff/admin/ig_admin باید actorِ واقعی (id>0) داشته باشند؛ فقط system می‌تواند actor=0 باشد
        if self.actor_role != "system" and not (isinstance(self.actor_id, int) and self.actor_id > 0):
            return "non-system actor requires a real actor_id"
        return ""


def system_context(operation: str, source_event_id: str = "", request_id: str = "",
                   idempotency_key: str = "", reason_code: str = "system") -> MutationContext:
    return MutationContext(actor_id=SYSTEM_ACTOR_ID, actor_role="system", source="system",
                           operation=operation, source_event_id=source_event_id, request_id=request_id,
                           idempotency_key=idempotency_key, reason_code=reason_code)


@dataclass
class MutationResult:
    status: str                       # applied|duplicate|noop|not_found|unauthorized|invalid|conflict
    task_id: int | None = None
    event_type: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("applied", "duplicate", "noop")


# ---------- schema (additive؛ CREATE IF NOT EXISTS) ----------
def init_schema() -> None:
    """جدول‌های audit + inbound-events + triggerهای append-only + indexها. idempotent و additive."""
    with db._lock:
        c = db._conn
        c.execute(
            """CREATE TABLE IF NOT EXISTS wt_task_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id         INTEGER,
                event_type      TEXT NOT NULL,
                actor_id        INTEGER NOT NULL,
                actor_role      TEXT NOT NULL,
                source          TEXT NOT NULL,
                source_event_id TEXT,
                request_id      TEXT,
                idempotency_key TEXT,
                prev_json       TEXT,
                new_json        TEXT,
                reason_code     TEXT,
                metadata_json   TEXT,
                occurred_at     TEXT NOT NULL
            )""")
        c.execute(
            """CREATE TABLE IF NOT EXISTS wt_inbound_events (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                source            TEXT NOT NULL,
                external_event_id TEXT NOT NULL,
                idempotency_key   TEXT,
                status            TEXT NOT NULL,
                operation         TEXT,
                actor_id          INTEGER,
                request_id        TEXT,
                attempt_count     INTEGER NOT NULL DEFAULT 0,
                first_seen_at     TEXT,
                updated_at        TEXT,
                completed_at      TEXT,
                lease_expires_at  TEXT,
                result_code       TEXT,
                result_reference  TEXT,
                error_type        TEXT,
                UNIQUE(source, external_event_id)
            )""")
        # idempotencyِ سطحِ عملیات + جلوگیری از auditِ تکراری: یکتا روی idempotency_key (فقط کلیدهای ناخالی)
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_wt_event_idem ON wt_task_events(idempotency_key) "
                  "WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_event_task ON wt_task_events(task_id, id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_event_actor ON wt_task_events(actor_id, id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_inbound_idem ON wt_inbound_events(idempotency_key)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_inbound_recovery ON wt_inbound_events(status, lease_expires_at)")
        # append-only: UPDATE/DELETE روی audit مسدود (از هر connection)
        c.execute("CREATE TRIGGER IF NOT EXISTS wt_task_events_no_update BEFORE UPDATE ON wt_task_events "
                  "BEGIN SELECT RAISE(ABORT, 'wt_task_events is append-only'); END")
        c.execute("CREATE TRIGGER IF NOT EXISTS wt_task_events_no_delete BEFORE DELETE ON wt_task_events "
                  "BEGIN SELECT RAISE(ABORT, 'wt_task_events is append-only'); END")
        # Workstream G/D-04: ستون‌های additive روی wt_tasks (nullable؛ فقط task_kind فعال، escalation_ref_ts هم فعال).
        # ALTER افزودنی و idempotent؛ بدونِ rebuild/حذف/rename؛ روی رکوردهای legacy امن (NULL → fallback در خواندن).
        for _alter in ("ALTER TABLE wt_tasks ADD COLUMN task_kind TEXT",
                       "ALTER TABLE wt_tasks ADD COLUMN escalation_ref_ts REAL",
                       # Core Operational Release — ستون‌های چرخه (همه nullable؛ رکوردِ legacy با NULL projection می‌شود)
                       "ALTER TABLE wt_tasks ADD COLUMN lifecycle_state TEXT",
                       "ALTER TABLE wt_tasks ADD COLUMN verification_mode TEXT",
                       "ALTER TABLE wt_tasks ADD COLUMN priority TEXT",
                       "ALTER TABLE wt_tasks ADD COLUMN deadline_ts REAL",
                       "ALTER TABLE wt_tasks ADD COLUMN started_ts REAL",
                       "ALTER TABLE wt_tasks ADD COLUMN claimed_done_ts REAL",
                       "ALTER TABLE wt_tasks ADD COLUMN verified_ts REAL",
                       "ALTER TABLE wt_tasks ADD COLUMN cancelled_ts REAL",
                       "ALTER TABLE wt_tasks ADD COLUMN blocked_reason TEXT",
                       "ALTER TABLE wt_tasks ADD COLUMN completion_note TEXT",
                       "ALTER TABLE wt_tasks ADD COLUMN verification_source TEXT",
                       "ALTER TABLE wt_tasks ADD COLUMN verification_ref TEXT",
                       "ALTER TABLE wt_tasks ADD COLUMN reopened_count INTEGER",
                       "ALTER TABLE wt_tasks ADD COLUMN last_transition_ts REAL",
                       "ALTER TABLE wt_tasks ADD COLUMN source_feature TEXT",
                       "ALTER TABLE wt_tasks ADD COLUMN verify_rule_json TEXT"):
            try:
                c.execute(_alter)
            except sqlite3.OperationalError:
                pass  # ستون از قبل هست
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_tasks_kind_status ON wt_tasks(task_kind, status)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_tasks_lifecycle ON wt_tasks(lifecycle_state, status)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_wt_tasks_source_feature ON wt_tasks(source_feature, lifecycle_state)")
        db._conn.commit()


# ---------- idempotency سطحِ عملیات + audit (درونِ همان تراکنش) ----------
def _existing_by_idem(key):
    if not key:
        return None
    r = db._conn.execute("SELECT task_id, event_type FROM wt_task_events WHERE idempotency_key=? LIMIT 1",
                         (key,)).fetchone()
    return r if r else None


def _insert_audit(ctx: MutationContext, event_type: str, task_id, prev, new, metadata=None) -> None:
    """auditِ append-only را درونِ تراکنشِ جاری می‌نویسد (بدونِ commit). actor خالی پذیرفته نیست."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"bad event_type {event_type}")
    db._conn.execute(
        "INSERT INTO wt_task_events(task_id, event_type, actor_id, actor_role, source, source_event_id, "
        "request_id, idempotency_key, prev_json, new_json, reason_code, metadata_json, occurred_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, event_type, ctx.actor_id, ctx.actor_role, ctx.source, ctx.source_event_id or None,
         ctx.request_id or None, ctx.idempotency_key or None, _jdump(prev), _jdump(new),
         ctx.reason_code or None, _jdump(metadata), _utc_now()))


# ---------- عملیاتِ mutation (تنها نقطهٔ نوشتنِ wt_tasks) ----------
def create_task(ctx: MutationContext, assignee_id, assignee_name, assigner_name, text,
                source_key=None, metric=None, task_kind="staff",
                lifecycle_state=None, verification_mode=None, priority=None,
                deadline_ts=None, source_feature=None, verify_rule_json=None) -> MutationResult:
    """ساختِ تسک با audit + idempotency + task_kind. dedupِ خزش (source_key) حفظ می‌شود.

    فیلدهای چرخه اختیاری‌اند و همه allowlist می‌شوند (LLM حق تعیین ندارد). اگر lifecycle_state=None بماند،
    رکورد دقیقاً مثلِ قبل با projection از status کار می‌کند (backward-compatible)."""
    err = ctx.error()
    if err or ctx.operation != "task_create":
        return MutationResult("invalid", detail=err or "operation mismatch")
    if task_kind not in TASK_KINDS:                          # allowlist در کد (نه LLM)
        return MutationResult("invalid", detail=f"bad task_kind {task_kind!r}")
    if lifecycle_state is not None and lifecycle_state not in LIFECYCLE_STATES:
        return MutationResult("invalid", detail=f"bad lifecycle_state {lifecycle_state!r}")
    if verification_mode is not None and verification_mode not in VERIFICATION_MODES:
        return MutationResult("invalid", detail=f"bad verification_mode {verification_mode!r}")
    if priority is not None and priority not in PRIORITIES:
        return MutationResult("invalid", detail=f"bad priority {priority!r}")
    if source_feature is not None and source_feature not in SOURCE_FEATURES:
        return MutationResult("invalid", detail=f"bad source_feature {source_feature!r}")
    if ctx.actor_role not in _ADMIN_ROLES:                  # ساختِ تسک فقط مدیر/سیستم (primary_admin==admin)
        _mut_log({"op": "task_create", "result": "unauthorized", "role": ctx.actor_role})
        return MutationResult("unauthorized", detail="create requires admin/system")
    now = time.time()
    with db._lock:
        try:
            dup = _existing_by_idem(ctx.idempotency_key)
            if dup:
                return MutationResult("duplicate", task_id=dup[0], event_type=dup[1], detail="idempotent_replay")
            init_status = _legacy_status_for(lifecycle_state) if lifecycle_state else "open"
            cur = db._conn.execute(
                "INSERT INTO wt_tasks(assignee_id, assignee_name, assigner_id, assigner_name, text, status, "
                "created_ts, source_key, metric, task_kind, escalation_ref_ts, lifecycle_state, verification_mode, "
                "priority, deadline_ts, source_feature, verify_rule_json, reopened_count, last_transition_ts) "
                "VALUES (?,?,?,?,?,?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (assignee_id, assignee_name, ctx.actor_id, assigner_name, text, init_status, now, source_key, metric,
                 task_kind, now, lifecycle_state, verification_mode, priority, deadline_ts, source_feature,
                 verify_rule_json, now))
            tid = cur.lastrowid
            _insert_audit(ctx, "task_created", tid, prev=None,
                          new={"assignee_id": assignee_id, "status": init_status, "task_kind": task_kind,
                               "lifecycle_state": lifecycle_state, "verification_mode": verification_mode,
                               "priority": priority, "source_feature": source_feature,
                               "text": _text_fingerprint(text)})
            db._conn.commit()
            _mut_log({"op": "task_create", "result": "applied", "task_id": tid, "source": ctx.source,
                      "role": ctx.actor_role, "req": ctx.request_id or None, "dup": False})
            return MutationResult("applied", task_id=tid, event_type="task_created")
        except sqlite3.IntegrityError:
            db._conn.rollback()
            dup = _existing_by_idem(ctx.idempotency_key)             # رقابتِ کلیدِ idempotency
            if dup:
                return MutationResult("duplicate", task_id=dup[0], event_type=dup[1], detail="idempotent_race")
            _mut_log({"op": "task_create", "result": "noop", "detail": "source_key_dup", "dup": True})
            return MutationResult("noop", task_id=-1, detail="source_key duplicate open task")  # قرارداد -1 حفظ می‌شود
        except Exception:
            db._conn.rollback()
            raise


def update_task(ctx: MutationContext, task_id, new_text) -> MutationResult:
    """اصلاحِ متنِ یک تسکِ باز (تنها فیلدِ مجازِ فعلی) + audit."""
    err = ctx.error()
    if err or ctx.operation != "task_update":
        return MutationResult("invalid", detail=err or "operation mismatch")
    if ctx.actor_role not in _ADMIN_ROLES:
        return MutationResult("unauthorized", detail="update requires admin/system")
    nt = (new_text or "").strip()
    if not nt:
        return MutationResult("invalid", detail="empty new_text")
    with db._lock:
        try:
            dup = _existing_by_idem(ctx.idempotency_key)
            if dup:
                return MutationResult("duplicate", task_id=dup[0], event_type=dup[1], detail="idempotent_replay")
            row = db._conn.execute("SELECT text, status FROM wt_tasks WHERE id=? AND status='open'",
                                   (int(task_id),)).fetchone()
            if not row:
                db._conn.rollback()
                return MutationResult("not_found", task_id=task_id, detail="no open task")
            old_text = row[0]
            if (old_text or "") == nt:                               # بدونِ تغییرِ واقعی → no-op
                _insert_audit(ctx, "task_noop", task_id, prev={"text": _text_fingerprint(old_text)},
                              new={"text": _text_fingerprint(nt)}, metadata={"reason": "no_change"})
                db._conn.commit()
                return MutationResult("noop", task_id=task_id, event_type="task_noop", detail="no_change")
            db._conn.execute("UPDATE wt_tasks SET text=? WHERE id=?", (nt, int(task_id)))
            _insert_audit(ctx, "task_updated", task_id, prev={"text": _text_fingerprint(old_text)},
                          new={"text": _text_fingerprint(nt)})
            db._conn.commit()
            _mut_log({"op": "task_update", "result": "applied", "task_id": task_id, "role": ctx.actor_role})
            return MutationResult("applied", task_id=task_id, event_type="task_updated")
        except sqlite3.IntegrityError:
            db._conn.rollback()
            dup = _existing_by_idem(ctx.idempotency_key)
            return MutationResult("duplicate", task_id=dup[0] if dup else task_id, event_type="task_updated")
        except Exception:
            db._conn.rollback()
            raise


def mark_done(ctx: MutationContext, task_id) -> MutationResult:
    """بستنِ تسک (open→done) + audit. staff فقط تسکِ خودش؛ admin/system هر تسکِ باز."""
    err = ctx.error()
    if err or ctx.operation != "task_mark_done":
        return MutationResult("invalid", detail=err or "operation mismatch")
    with db._lock:
        try:
            dup = _existing_by_idem(ctx.idempotency_key)
            if dup:
                return MutationResult("duplicate", task_id=dup[0], event_type=dup[1], detail="idempotent_replay")
            row = db._conn.execute("SELECT assignee_id, status FROM wt_tasks WHERE id=?", (int(task_id),)).fetchone()
            if not row:
                db._conn.rollback()
                return MutationResult("not_found", task_id=task_id)
            assignee_id, status = row
            if ctx.actor_role == "staff" and assignee_id != ctx.actor_id:   # مالکیت‌محور (رفتارِ فعلی)
                db._conn.rollback()
                _mut_log({"op": "task_mark_done", "result": "unauthorized", "task_id": task_id, "role": "staff"})
                return MutationResult("unauthorized", task_id=task_id, detail="not owner")
            if status != "open":                                            # قبلاً بسته → no-op (idempotent)
                _insert_audit(ctx, "task_noop", task_id, prev={"status": status}, new={"status": status},
                              metadata={"reason": "already_done"})
                db._conn.commit()
                return MutationResult("noop", task_id=task_id, event_type="task_noop", detail="already_done")
            _now = time.time()
            # اگر تسک چرخه دارد (lifecycle_state غیرِ NULL)، آن را هم به verified_done همگام کن تا با status متناقض نشود.
            db._conn.execute(
                "UPDATE wt_tasks SET status='done', done_ts=?, "
                "lifecycle_state=CASE WHEN lifecycle_state IS NOT NULL THEN 'verified_done' ELSE lifecycle_state END, "
                "verified_ts=CASE WHEN lifecycle_state IS NOT NULL THEN COALESCE(verified_ts, ?) ELSE verified_ts END, "
                "last_transition_ts=CASE WHEN lifecycle_state IS NOT NULL THEN ? ELSE last_transition_ts END "
                "WHERE id=? AND status='open'", (_now, _now, _now, int(task_id)))
            _insert_audit(ctx, "task_marked_done", task_id, prev={"status": "open"}, new={"status": "done"})
            db._conn.commit()
            _mut_log({"op": "task_mark_done", "result": "applied", "task_id": task_id, "role": ctx.actor_role})
            return MutationResult("applied", task_id=task_id, event_type="task_marked_done")
        except sqlite3.IntegrityError:
            db._conn.rollback()
            dup = _existing_by_idem(ctx.idempotency_key)
            return MutationResult("duplicate", task_id=dup[0] if dup else task_id, event_type="task_marked_done")
        except Exception:
            db._conn.rollback()
            raise


# ---------- maintenanceِ خزش از طریقِ سرویس (Workstream E/D-05) ----------
def refresh_crawl_task(ctx: MutationContext, task_id, new_text, new_metric) -> MutationResult:
    """متن/متریکِ یک تسکِ بازِ خزش را (با audit) به‌روز می‌کند. تغییرِ بی‌اثر → no-op بدونِ event."""
    if ctx.error() or ctx.operation != "crawl_refresh":
        return MutationResult("invalid")
    nt = (new_text or "").strip()
    with db._lock:
        try:
            row = db._conn.execute("SELECT text, metric FROM wt_tasks WHERE id=? AND status='open'",
                                   (int(task_id),)).fetchone()
            if not row:
                db._conn.rollback()
                return MutationResult("not_found", task_id=task_id)
            old_text, old_metric = row
            if (old_text or "") == nt and old_metric == new_metric:   # تغییرِ واقعی نیست → بدونِ audit
                db._conn.rollback()
                return MutationResult("noop", task_id=task_id, detail="no_change")
            db._conn.execute("UPDATE wt_tasks SET text=?, metric=? WHERE id=? AND status='open'",
                             (nt, new_metric, int(task_id)))
            _insert_audit(ctx, "crawl_task_refreshed", task_id,
                          prev={"text": _text_fingerprint(old_text), "metric": old_metric},
                          new={"text": _text_fingerprint(nt), "metric": new_metric})
            db._conn.commit()
            return MutationResult("applied", task_id=task_id, event_type="crawl_task_refreshed")
        except Exception:
            db._conn.rollback()
            raise


def bump_crawl_escalation(ctx: MutationContext, task_id) -> MutationResult:
    """فاصله‌گذاریِ تشدید: escalation_ref_ts را به now می‌برد (created_ts دیگر تغییر نمی‌کند). با audit."""
    if ctx.error() or ctx.operation != "crawl_escalation_bump":
        return MutationResult("invalid")
    with db._lock:
        try:
            row = db._conn.execute("SELECT escalation_ref_ts, created_ts FROM wt_tasks WHERE id=?",
                                   (int(task_id),)).fetchone()
            if not row:
                db._conn.rollback()
                return MutationResult("not_found", task_id=task_id)
            prev_ref = row[0] if row[0] is not None else row[1]   # legacy fallback: created_ts
            now = time.time()
            db._conn.execute("UPDATE wt_tasks SET escalation_ref_ts=? WHERE id=?", (now, int(task_id)))
            _insert_audit(ctx, "crawl_task_escalation_reference_updated", task_id,
                          prev={"escalation_ref_ts": prev_ref}, new={"escalation_ref_ts": now})
            db._conn.commit()
            return MutationResult("applied", task_id=task_id, event_type="crawl_task_escalation_reference_updated")
        except Exception:
            db._conn.rollback()
            raise


# ---------- ledgerِ ارسالِ خروجی (Workstream B/D-02) — روی همان زیرساختِ inbound-events ----------
def delivery_claim(key, operation="", actor_id=SYSTEM_ACTOR_ID, lease_sec=_LEASE_SEC, max_attempts=_MAX_ATTEMPTS):
    """یک پیامِ منطقی را برای ارسال claim می‌کند. خروجی: claimed/recovered → بفرست؛ duplicate/in_progress/skip → نفرست."""
    return claim_inbound("delivery", key, operation=operation, actor_id=actor_id,
                         lease_sec=lease_sec, max_attempts=max_attempts)


def delivery_complete(key, message_id=""):
    """پس از ارسالِ موفق: ثبتِ delivery + شناسهٔ پیامِ تلگرام (شواهدِ تحویل). هیچ متنِ خام ذخیره نمی‌شود."""
    complete_inbound("delivery", key, result_code="sent", result_reference=str(message_id or ""))


def delivery_fail(key, error_type=""):
    fail_inbound("delivery", key, error_type=error_type, permanent=False)


# ---------- idempotencyِ ورودیِ Telegram (claim / complete / fail + recovery) ----------
def claim_inbound(source, external_event_id, operation="", actor_id=SYSTEM_ACTOR_ID, request_id="",
                  idempotency_key="", lease_sec=_LEASE_SEC, max_attempts=_MAX_ATTEMPTS):
    """رویدادِ ورودی را claim می‌کند. خروجی: (decision, row). decisionها:
    claimed/recovered → پردازش کن؛ duplicate/in_progress/skip_permanent → پردازش نکن.
    """
    if source not in SOURCES:
        return ("invalid", None)
    ext = str(external_event_id)
    now = _utc_now()
    lease = _iso(time.time() + lease_sec)
    with db._lock:
        try:
            db._conn.execute(
                "INSERT INTO wt_inbound_events(source, external_event_id, idempotency_key, status, operation, "
                "actor_id, request_id, attempt_count, first_seen_at, updated_at, lease_expires_at) "
                "VALUES (?,?,?,?,?,?,?,1,?,?,?)",
                (source, ext, idempotency_key or None, "processing", operation, actor_id, request_id or None,
                 now, now, lease))
            db._conn.commit()
            _mut_log({"ev": "inbound_claimed", "source": source, "op": operation, "attempt": 1, "dup": False})
            return ("claimed", None)
        except sqlite3.IntegrityError:
            db._conn.rollback()
        row = db._conn.execute(
            "SELECT status, attempt_count, lease_expires_at FROM wt_inbound_events "
            "WHERE source=? AND external_event_id=?", (source, ext)).fetchone()
        if not row:
            return ("claimed", None)
        status, attempts, lease_exp = row
        if status == "succeeded":
            _mut_log({"ev": "inbound_duplicate", "source": source, "op": operation, "dup": True})
            return ("duplicate", row)
        if status == "failed_permanent":
            return ("skip_permanent", row)
        if status == "processing" and lease_exp and lease_exp > now:   # lease معتبر → دیگری در حالِ پردازش
            _mut_log({"ev": "inbound_in_progress", "source": source, "op": operation, "dup": True})
            return ("in_progress", row)
        if (attempts or 0) >= max_attempts:                            # سقفِ retry → دائمی
            db._conn.execute("UPDATE wt_inbound_events SET status='failed_permanent', updated_at=? "
                             "WHERE source=? AND external_event_id=?", (now, source, ext))
            db._conn.commit()
            return ("skip_permanent", row)
        db._conn.execute(                                              # lease منقضی یا retryable → recovery
            "UPDATE wt_inbound_events SET status='processing', attempt_count=attempt_count+1, updated_at=?, "
            "lease_expires_at=? WHERE source=? AND external_event_id=?", (now, lease, source, ext))
        db._conn.commit()
        _mut_log({"ev": "inbound_recovered", "source": source, "op": operation,
                  "attempt": (attempts or 0) + 1, "dup": False})
        return ("recovered", row)


def complete_inbound(source, external_event_id, result_code="", result_reference=""):
    now = _utc_now()
    with db._lock:
        db._conn.execute(
            "UPDATE wt_inbound_events SET status='succeeded', result_code=?, result_reference=?, "
            "completed_at=?, updated_at=?, lease_expires_at=NULL WHERE source=? AND external_event_id=?",
            (str(result_code), str(result_reference), now, now, source, str(external_event_id)))
        db._conn.commit()


def fail_inbound(source, external_event_id, error_type="", permanent=False):
    now = _utc_now()
    st = "failed_permanent" if permanent else "failed_retryable"
    with db._lock:
        db._conn.execute(
            "UPDATE wt_inbound_events SET status=?, error_type=?, updated_at=?, lease_expires_at=NULL "
            "WHERE source=? AND external_event_id=?", (st, str(error_type)[:60], now, source, str(external_event_id)))
        db._conn.commit()


# ============================================================
# چرخهٔ زندگیِ تسکِ انسانی — سرویسِ گذار (Core Operational Release)
# taskservice تنها نویسندهٔ wt_tasks می‌ماند؛ همهٔ گذارها از اینجا عبور می‌کنند.
# ============================================================
def get_task(task_id) -> dict | None:
    """خواندنِ فیلدهای چرخهٔ یک تسک (برای orchestration/گزارش). state از projection حساب می‌شود."""
    with db._lock:
        r = db._conn.execute(
            "SELECT id, assignee_id, assignee_name, status, lifecycle_state, verification_mode, priority, "
            "deadline_ts, task_kind, source_feature, verify_rule_json, reopened_count, blocked_reason, "
            "completion_note, verification_source, verification_ref, started_ts, claimed_done_ts, verified_ts "
            "FROM wt_tasks WHERE id=?", (int(task_id),)).fetchone()
    if not r:
        return None
    return {"id": r[0], "assignee_id": r[1], "assignee_name": r[2], "status": r[3],
            "lifecycle_state": r[4], "state": lifecycle_of(r[4], r[3]), "verification_mode": r[5] or "none",
            "priority": r[6] or "normal", "deadline_ts": r[7], "task_kind": r[8],
            "source_feature": r[9] or "general", "verify_rule_json": r[10], "reopened_count": r[11] or 0,
            "blocked_reason": r[12], "completion_note": r[13], "verification_source": r[14],
            "verification_ref": r[15], "started_ts": r[16], "claimed_done_ts": r[17], "verified_ts": r[18]}


def resolve_done_target(mode) -> str:
    """اعلامِ «انجام شد» توسطِ پرسنل: mode=none → مستقیم verified_done؛ manager/automatic → claimed_done."""
    return "verified_done" if mode == "none" else "claimed_done"


def _can_transition(role, from_state, to_state, mode, is_owner) -> tuple[bool, str]:
    """authorization + قانونِ گذار در کد (نه LLM). خروجی (allowed, err)."""
    if to_state not in LIFECYCLE_STATES:
        return False, f"bad target {to_state!r}"
    if to_state not in TRANSITIONS.get(from_state, set()):
        return False, f"invalid transition {from_state}->{to_state}"
    if role in ("admin", "primary_admin"):
        return True, ""                                    # مدیر می‌تواند هر گذارِ معتبر را انجام دهد
    if role == "system":                                   # فقط نتیجهٔ automatic verificationِ allowlist‌شده
        if to_state in ("verified_done", "claimed_done"):
            return True, ""
        return False, "system limited to verification results"
    if role == "staff":
        if not is_owner:
            return False, "not owner"
        if to_state in ("in_progress", "blocked", "claimed_done"):
            return True, ""
        if to_state == "verified_done" and mode == "none":  # فقط تسکِ کم‌ریسک، تأییدِ مستقیمِ خودِ فرد
            return True, ""
        return False, "staff not allowed for this transition"
    return False, f"unknown role {role!r}"


def transition_task(ctx: MutationContext, task_id, target_state, reason=None, completion_note=None,
                    verification_source=None, verification_ref=None, expected_from=None) -> MutationResult:
    """گذارِ اتمیکِ چرخه + audit + idempotency. شبکه/LLM/notification داخلِ این تراکنش نیست.

    - reason برای blocked و cancelled الزامی است.
    - expected_from (اختیاری): اگر با stateِ فعلی نخواند → conflict (کنترلِ همزمانیِ خوش‌بینانه).
    - گذارِ نامعتبر/غیرمجاز fail-closed. retryِ همان idempotency-key → duplicate. رسیدن به همان state → noop.
    """
    err = ctx.error()
    if err or ctx.operation != "task_transition":
        return MutationResult("invalid", detail=err or "operation mismatch")
    if target_state not in LIFECYCLE_STATES:
        return MutationResult("invalid", detail=f"bad target {target_state!r}")
    if target_state in ("blocked", "cancelled") and not (reason or "").strip():
        return MutationResult("invalid", detail=f"{target_state} requires reason")
    now = time.time()
    with db._lock:
        try:
            dup = _existing_by_idem(ctx.idempotency_key)
            if dup:
                return MutationResult("duplicate", task_id=dup[0], event_type=dup[1], detail="idempotent_replay")
            row = db._conn.execute(
                "SELECT assignee_id, status, lifecycle_state, verification_mode, reopened_count "
                "FROM wt_tasks WHERE id=?", (int(task_id),)).fetchone()
            if not row:
                db._conn.rollback()
                return MutationResult("not_found", task_id=task_id)
            assignee_id, status, lifecycle_state, mode, reopened = row
            cur_state = lifecycle_of(lifecycle_state, status)
            mode = mode or "none"
            if expected_from is not None and cur_state != expected_from:      # کنترلِ همزمانی
                db._conn.rollback()
                return MutationResult("conflict", task_id=task_id, detail=f"expected {expected_from}, is {cur_state}")
            if cur_state == target_state:                                     # قبلاً در همان state → no-op امن
                db._conn.rollback()
                return MutationResult("noop", task_id=task_id, detail="already_in_state")
            is_owner = (assignee_id == ctx.actor_id)
            ok, aerr = _can_transition(ctx.actor_role, cur_state, target_state, mode, is_owner)
            if not ok:
                db._conn.rollback()
                _mut_log({"op": "task_transition", "result": "rejected", "role": ctx.actor_role,
                          "from": cur_state, "to": target_state, "why": aerr})
                # گذارِ ساختاراً نامعتبر → invalid؛ نبودِ مجوز → unauthorized
                bad_edge = target_state not in TRANSITIONS.get(cur_state, set())
                return MutationResult("invalid" if bad_edge else "unauthorized", task_id=task_id, detail=aerr)
            new_status = _legacy_status_for(target_state)
            sets = ["lifecycle_state=?", "status=?", "last_transition_ts=?"]
            vals = [target_state, new_status, now]
            ts_col = _STATE_TS.get(target_state)
            if ts_col:
                sets.append(f"{ts_col}=?"); vals.append(now)
            if new_status == "done":
                sets.append("done_ts=?"); vals.append(now)
            if target_state == "blocked":
                sets.append("blocked_reason=?"); vals.append((reason or "").strip()[:400])
            if target_state == "reopened":
                sets.append("reopened_count=?"); vals.append((reopened or 0) + 1)
            if completion_note is not None:
                sets.append("completion_note=?"); vals.append(str(completion_note).strip()[:800])
            if verification_source is not None:
                sets.append("verification_source=?"); vals.append(str(verification_source)[:40])
            if verification_ref is not None:
                sets.append("verification_ref=?"); vals.append(str(verification_ref)[:120])
            vals.append(int(task_id))
            db._conn.execute(f"UPDATE wt_tasks SET {', '.join(sets)} WHERE id=?", vals)
            _insert_audit(ctx, "task_state_changed", task_id,
                          prev={"state": cur_state}, new={"state": target_state},
                          metadata={"reason_present": bool((reason or "").strip()),
                                    "verification_source": verification_source})
            db._conn.commit()
            _mut_log({"op": "task_transition", "result": "applied", "task_id": task_id,
                      "from": cur_state, "to": target_state, "role": ctx.actor_role})
            return MutationResult("applied", task_id=task_id, event_type="task_state_changed", detail=target_state)
        except sqlite3.IntegrityError:
            db._conn.rollback()
            dup = _existing_by_idem(ctx.idempotency_key)
            return MutationResult("duplicate", task_id=dup[0] if dup else task_id, event_type="task_state_changed")
        except Exception:
            db._conn.rollback()
            raise


def reassign_task(ctx: MutationContext, task_id, new_assignee_id, new_assignee_name) -> MutationResult:
    """واگذاریِ مجددِ تسک به فردِ دیگر (فقط مدیر). تسکِ terminal واگذار نمی‌شود؛ state به open برمی‌گردد."""
    if ctx.error() or ctx.operation != "task_reassign":
        return MutationResult("invalid", detail="operation mismatch")
    if ctx.actor_role not in ("admin", "primary_admin"):
        return MutationResult("unauthorized", detail="reassign requires admin")
    now = time.time()
    with db._lock:
        try:
            dup = _existing_by_idem(ctx.idempotency_key)
            if dup:
                return MutationResult("duplicate", task_id=dup[0], event_type=dup[1])
            row = db._conn.execute("SELECT assignee_id, status, lifecycle_state FROM wt_tasks WHERE id=?",
                                   (int(task_id),)).fetchone()
            if not row:
                db._conn.rollback()
                return MutationResult("not_found", task_id=task_id)
            old_aid, status, lc = row
            if lifecycle_of(lc, status) in TERMINAL_STATES:
                db._conn.rollback()
                return MutationResult("invalid", task_id=task_id, detail="cannot reassign terminal task")
            new_lc = "open" if lc is not None else None      # اگر چرخه فعال بود، به open برگردد
            db._conn.execute(
                "UPDATE wt_tasks SET assignee_id=?, assignee_name=?, status='open', "
                "lifecycle_state=?, started_ts=NULL, last_transition_ts=? WHERE id=?",
                (int(new_assignee_id), new_assignee_name, new_lc, now, int(task_id)))
            _insert_audit(ctx, "task_reassigned", task_id, prev={"assignee_id": old_aid},
                          new={"assignee_id": int(new_assignee_id)})
            db._conn.commit()
            _mut_log({"op": "task_reassign", "result": "applied", "task_id": task_id, "role": ctx.actor_role})
            return MutationResult("applied", task_id=task_id, event_type="task_reassigned")
        except Exception:
            db._conn.rollback()
            raise


def _set_field(ctx, task_id, operation, event_type, column, value, allow=None, log_key=""):
    """کمکیِ مشترکِ set priority/deadline/verification_mode (فقط مدیر) + audit اتمیک."""
    if ctx.error() or ctx.operation != operation:
        return MutationResult("invalid", detail="operation mismatch")
    if ctx.actor_role not in ("admin", "primary_admin"):
        return MutationResult("unauthorized", detail=f"{operation} requires admin")
    if allow is not None and value not in allow:
        return MutationResult("invalid", detail=f"bad value {value!r}")
    with db._lock:
        try:
            dup = _existing_by_idem(ctx.idempotency_key)
            if dup:
                return MutationResult("duplicate", task_id=dup[0], event_type=dup[1])
            row = db._conn.execute(f"SELECT {column} FROM wt_tasks WHERE id=?", (int(task_id),)).fetchone()
            if not row:
                db._conn.rollback()
                return MutationResult("not_found", task_id=task_id)
            db._conn.execute(f"UPDATE wt_tasks SET {column}=? WHERE id=?", (value, int(task_id)))
            _insert_audit(ctx, event_type, task_id, prev={log_key: row[0]}, new={log_key: value})
            db._conn.commit()
            _mut_log({"op": operation, "result": "applied", "task_id": task_id, "role": ctx.actor_role})
            return MutationResult("applied", task_id=task_id, event_type=event_type)
        except Exception:
            db._conn.rollback()
            raise


def set_priority(ctx, task_id, priority):
    return _set_field(ctx, task_id, "task_set_priority", "task_priority_set", "priority", priority,
                      allow=PRIORITIES, log_key="priority")


def set_deadline(ctx, task_id, deadline_ts):
    dl = float(deadline_ts) if deadline_ts is not None else None
    return _set_field(ctx, task_id, "task_set_deadline", "task_deadline_set", "deadline_ts", dl, log_key="deadline_ts")


def set_verification_mode(ctx, task_id, mode):
    return _set_field(ctx, task_id, "task_set_verification_mode", "task_verification_mode_set",
                      "verification_mode", mode, allow=VERIFICATION_MODES, log_key="verification_mode")
