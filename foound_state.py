"""
FOOUND — private state for the daily pipeline.

Responsibilities, in order of importance:

  1. Load an agent's private decisions (PASS / APPLIED) reliably, and NEVER
     report "no decisions" merely because the database was unreachable.
     Infrastructure failure must not equal cognitive amnesia.

  2. Never let "stale but valid" become invisible. Stale state is loud in the
     operator log and bounded by a freshness policy — last-known-valid does
     not mean arbitrarily old forever.

  3. Provide THE single choke point where private exclusions are applied,
     called before any stage that can produce public output.

  4. Provide the bounded RECENT DECISIONS context — evidence about specific
     roles, never durable preferences.

Naming note (deliberate, do not collapse these):
    user_pass / user_passes    — the PERSON rejected this role.
    foound_rejects / near_miss — FOOUND read the role and declined it.
                                 Rendered publicly as "NEARLY FOOUND".
Two different actors. The interface may keep NEARLY FOOUND; the code may not
use "passed" for both.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import requests

SNAPSHOT_VERSION = 1
RECENT_DECISIONS_LIMIT = 10
RECENT_DECISIONS_DAYS = 14
HTTP_TIMEOUT = 15

# Freshness policy for last-known-valid state.
#
# The snapshot is only USED when a live read fails, so its age equals days
# since the last successful read. Normal worst case is a Monday run failing
# against a snapshot last refreshed on Friday — 3 days. The default therefore
# tolerates a holiday weekend plus one outage day, and refuses beyond that:
# past ~5 days, a person's decisions have likely moved and quietly trusting
# old exclusions is worse than failing one edition loudly.
MAX_SNAPSHOT_AGE_DAYS = 5.0


class PrivateStateUnavailable(RuntimeError):
    """No live state, and no snapshot that is trustworthy enough to use.

    The caller MUST fail this agent's edition rather than build one that
    contradicts decisions the person already made and saw confirmed.
    """


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
@dataclass
class PrivateState:
    agent_id: str
    agent_no: int | None = None
    user_passes: dict[str, dict] = field(default_factory=dict)   # role_key -> signal
    applied: dict[str, dict] = field(default_factory=dict)       # role_key -> signal
    fetched_at: str = ""
    source: str = "live"                 # "live" | "snapshot"
    stale: bool = False
    snapshot_age_days: float | None = None
    last_live_read: str | None = None

    @property
    def excluded_keys(self) -> set[str]:
        """Every role the person removed from active recommendation.

        PASS    — out of consideration.
        APPLIED — moved forward; retained and openable, but no longer
                  competing among today's new recommendations.
        """
        return set(self.user_passes) | set(self.applied)

    def recent_decisions(
        self,
        limit: int = RECENT_DECISIONS_LIMIT,
        days: int = RECENT_DECISIONS_DAYS,
        today: date | None = None,
    ) -> list[dict]:
        """Bounded, recent, PASS-only. APPLIED is structurally absent.

        No aggregation, no counting, no scoring — a count is the first step
        toward a rule, and V1 does not make rules from signals.
        """
        cutoff = (today or date.today()) - timedelta(days=days)
        rows = []
        for sig in self.user_passes.values():
            if not is_learning_bearing(sig.get("reason")):
                continue          # situational: removes the role, teaches nothing
            when = _parse_date(sig.get("created_at"))
            if when and when >= cutoff:
                rows.append(sig)
        rows.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        out = rows[:limit]
        assert all(s.get("kind") == "pass" for s in out), \
            "RECENT DECISIONS must contain only user passes; applied never informs ranking"
        return out


# ---------------------------------------------------------------------------
# Operator visibility: STATE / ENGINE / EDITION per agent
# ---------------------------------------------------------------------------
@dataclass
class AgentRunReport:
    """One line per agent per run. Logs are the operator surface for the first
    cohort; a `runs` table is the natural next step once there are 5-10 people.
    """
    agent_no: int | None
    agent_id: str
    state: str = "unknown"       # live | stale | unavailable
    engine: str = "unknown"      # ai | heuristic
    edition: str = "pending"     # built | failed | delivered | skipped
    delivered: bool = False
    snapshot_age_days: float | None = None
    last_live_read: str | None = None
    last_good: str | None = None      # last edition that was built AND delivered
    market_fetched: int | None = None  # roles seen across the market
    foound: int | None = None          # roles that reached this agent's edition
    detail: str = ""

    @classmethod
    def from_state(cls, st: PrivateState) -> "AgentRunReport":
        label = ("unconfigured" if st.source == "unconfigured"
                 else "stale" if st.stale else "live")
        return cls(agent_no=st.agent_no, agent_id=st.agent_id, state=label,
                   snapshot_age_days=st.snapshot_age_days,
                   last_live_read=st.last_live_read)

    def line(self) -> str:
        """One line, scannable down a column when ten agents run.

        №007 · live · ai · built+delivered · last_good 08:03 · 6,204/7
        №004 · stale · ai · built · last_good Aug 11 · state_age 3.2d
        """
        who = f"№{self.agent_no:03d}" if self.agent_no else self.agent_id[:8]
        edition = self.edition + ("+delivered" if self.delivered else "")
        parts = [who, self.state, self.engine, edition]
        if self.last_good:
            parts.append(f"last_good {self.last_good}")
        if self.state == "stale" and self.last_live_read:
            parts.append(f"last_live {self.last_live_read}")
        if self.snapshot_age_days is not None:
            parts.append(f"state_age {self.snapshot_age_days:.1f}d")
        if self.market_fetched is not None and self.foound is not None:
            parts.append(f"{self.market_fetched:,}/{self.foound}")
        if self.detail:
            parts.append(repr(self.detail))
        return "[operator] " + " · ".join(parts)

    # Kept for machine consumption; the same fields insert straight into a
    # `runs` table when logs stop being enough.
    def as_row(self) -> dict:
        return {"agent_no": self.agent_no, "agent_id": self.agent_id,
                "state": self.state, "engine": self.engine,
                "edition": self.edition, "delivered": self.delivered,
                "snapshot_age_days": self.snapshot_age_days,
                "last_live_read": self.last_live_read, "last_good": self.last_good,
                "market_fetched": self.market_fetched, "foound": self.foound,
                "detail": self.detail}

    def emit(self) -> str:
        line = self.line()
        print(line)
        return line


# ---------------------------------------------------------------------------
# Loading: live → last-known-valid (bounded) → fail
# ---------------------------------------------------------------------------
def load_private_state(
    agent_id: str,
    snapshot_dir: str,
    agent_no: int | None = None,
    supabase_url: str | None = None,
    service_key: str | None = None,
    published_dir: str = "docs",
    max_age_days: float = MAX_SNAPSHOT_AGE_DAYS,
    now: datetime | None = None,
) -> PrivateState:
    """live -> validate -> refresh snapshot -> return
       down -> last-known-valid snapshot, if fresh enough (marked stale)
       none / too old -> raise PrivateStateUnavailable
    """
    _assert_snapshot_path_is_private(snapshot_dir, published_dir)
    now = now or datetime.now(timezone.utc)

    supabase_url = supabase_url or os.environ.get("SUPABASE_URL", "")
    service_key = service_key or os.environ.get("SUPABASE_SERVICE_KEY", "")

    # NOT CONFIGURED is not the same as UNREACHABLE, and the difference matters.
    #
    # "Unreachable" means decisions exist and we cannot see them — forgetting
    # them would be amnesia, so we fail closed. "Not configured" means the
    # verdict loop has never been switched on for this agent, so there are no
    # decisions to forget and an empty state is the TRUTH, not a guess.
    #
    # This is what makes the pipeline safe to deploy before Supabase exists:
    # the edition builds exactly as it always has, and starts honouring
    # decisions the moment the two secrets are added. Order of operations
    # becomes the operator's choice rather than a trap.
    if not supabase_url or not service_key:
        st = _build_state(agent_id, [], source="unconfigured",
                          agent_no=agent_no, now=now)
        st.source = "unconfigured"
        print(f"[state] verdict loop not configured for agent {agent_id} "
              "(no SUPABASE_URL / SUPABASE_SERVICE_KEY) — proceeding with no "
              "private decisions, which is correct: none can exist yet.")
        return st

    try:
        rows = _fetch_live_with_retry(agent_id, supabase_url, service_key,
                                      agent_no=agent_no)
        st = _build_state(agent_id, rows, source="live", agent_no=agent_no, now=now)
        st.last_live_read = st.fetched_at
        _write_snapshot(snapshot_dir, st, rows)
        return st
    except Exception as exc:
        print(f"[state] live read failed for agent {agent_id}: {exc}")

    snap = _read_snapshot(snapshot_dir, agent_id)
    if snap is None:
        raise PrivateStateUnavailable(
            f"agent {agent_id}: no live state and no snapshot; refusing to build "
            "an edition that could contradict stored decisions"
        )

    fetched_at = snap.get("fetched_at", "")
    age = _age_days(fetched_at, now)

    if age is None:
        raise PrivateStateUnavailable(
            f"agent {agent_id}: snapshot has no usable timestamp; refusing to trust it"
        )

    if age > max_age_days:
        raise PrivateStateUnavailable(
            f"agent {agent_id}: snapshot is {age:.1f}d old (limit {max_age_days}d). "
            "Last-known-valid does not mean arbitrarily old — failing this edition "
            "rather than trusting stale decisions."
        )

    st = _build_state(agent_id, snap["rows"], source="snapshot",
                      agent_no=agent_no, now=now)
    st.stale = True
    st.fetched_at = fetched_at
    st.last_live_read = fetched_at
    st.snapshot_age_days = age

    # Loud by design: a person quietly running on cached private state for two
    # days is exactly what the operator needs to see immediately.
    print(f"[state] STALE — agent {'№%03d' % agent_no if agent_no else agent_id} "
          f"running on cached private state: snapshot_age={age:.1f}d "
          f"last_live_read={fetched_at} exclusions={len(st.excluded_keys)}")
    return st


def _headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def resolve_agent_uuid(agent_no: int, url: str, key: str) -> str:
    """Look up an agent's database UUID by its human number (№001 -> uuid).

    The config layer knows the agent as "001"; the database keys everything by
    UUID. Resolving here — rather than hardcoding a UUID into config — means a
    new agent is still just a row, and re-creating the database never requires
    editing code.
    """
    r = requests.get(
        f"{url.rstrip('/')}/rest/v1/agents",
        params={"agent_no": f"eq.{int(agent_no)}", "select": "id", "limit": "1"},
        headers=_headers(key), timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError(
            f"no agents row with agent_no={agent_no}. Create it before the "
            "verdict loop can store decisions.")
    return rows[0]["id"]


def _fetch_live(agent_id: str, url: str, key: str, agent_no: int | None = None) -> list[dict]:
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not configured")

    # AgentConfig identifies the agent as "001"; signals.agent_id is a UUID.
    # Resolve the display number to the real key rather than asking a person to
    # copy a UUID into a config file.
    if agent_no is not None and not _looks_like_uuid(agent_id):
        agent_id = resolve_agent_uuid(agent_no, url, key)

    r = requests.get(
        f"{url.rstrip('/')}/rest/v1/signals",
        params={"agent_id": f"eq.{agent_id}", "state": "eq.active", "select": "*"},
        headers=_headers(key), timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected response shape: {type(rows).__name__}")
    return [row for row in rows if _valid_row(row)]


def _looks_like_uuid(value: str) -> bool:
    v = str(value)
    return len(v) == 36 and v.count("-") == 4


LIVE_READ_ATTEMPTS = 3
LIVE_READ_BACKOFF = 2.0        # seconds; 2s, 4s


def _fetch_live_with_retry(agent_id: str, url: str, key: str,
                           agent_no: int | None = None,
                           attempts: int = LIVE_READ_ATTEMPTS,
                           sleep=None) -> list[dict]:
    """Retry a failed live read before giving up on it.

    Failing closed is correct when the database is genuinely unreachable — but
    a momentary blip should not cost someone their morning edition, especially
    on a stateless runner where no snapshot survives between runs. A few
    seconds of patience removes almost all false aborts.

    Configuration errors are not retried: a bad key or a missing agent row will
    fail identically three times, so we surface it immediately.
    """
    import time
    sleeper = sleep or time.sleep
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return _fetch_live(agent_id, url, key, agent_no=agent_no)
        except Exception as exc:
            last = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403, 404) or "no agents row" in str(exc):
                raise                              # configuration, not weather
            if attempt < attempts:
                wait = LIVE_READ_BACKOFF * attempt
                print(f"[state] live read attempt {attempt}/{attempts} failed "
                      f"({exc}); retrying in {wait:.0f}s")
                sleeper(wait)
    raise last if last else RuntimeError("live read failed")


def _valid_row(row: dict) -> bool:
    """Reject anything that would poison state. Validation is code, not a model."""
    if row.get("kind") not in ("pass", "applied"):
        return False
    if not row.get("role_key"):
        return False
    snap = row.get("snapshot") or {}
    if str(snap.get("version")) != str(SNAPSHOT_VERSION):
        return False
    if row["kind"] == "applied" and (row.get("reason") or row.get("note")):
        return False          # class violation; should be impossible via the DB
    return True


def _build_state(agent_id: str, rows: Iterable[dict], source: str,
                 agent_no: int | None = None,
                 now: datetime | None = None) -> PrivateState:
    st = PrivateState(agent_id=agent_id, agent_no=agent_no, source=source,
                      fetched_at=(now or datetime.now(timezone.utc)).isoformat())
    for row in rows:
        if row.get("state", "active") != "active":
            continue
        if row["kind"] == "pass":
            st.user_passes[row["role_key"]] = row
        elif row["kind"] == "applied":
            st.applied[row["role_key"]] = row
    return st


# ---------------------------------------------------------------------------
# Snapshot: last-known-valid state, private by construction
# ---------------------------------------------------------------------------
def _assert_snapshot_path_is_private(snapshot_dir: str, published_dir: str) -> None:
    """Private state must never be written where public rendering can reach it."""
    snap = os.path.abspath(snapshot_dir)
    pub = os.path.abspath(published_dir)
    if snap == pub or snap.startswith(pub + os.sep):
        raise RuntimeError(
            f"refusing to write private state into the published directory: {snap}"
        )


def _snapshot_path(snapshot_dir: str, agent_id: str) -> str:
    return os.path.join(snapshot_dir, f"agent_{agent_id}.json")


def _write_snapshot(snapshot_dir: str, state: PrivateState, rows: list[dict]) -> None:
    try:
        os.makedirs(snapshot_dir, exist_ok=True)
        final = _snapshot_path(snapshot_dir, state.agent_id)
        tmp = final + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"snapshot_format": 1,
                       "agent_id": state.agent_id,
                       "fetched_at": state.fetched_at,
                       "rows": rows}, f, indent=1, sort_keys=True)
        os.replace(tmp, final)          # atomic: never a half-written snapshot
    except Exception as exc:
        print(f"[state] snapshot write failed (non-fatal): {exc}")


def _read_snapshot(snapshot_dir: str, agent_id: str) -> dict | None:
    try:
        with open(_snapshot_path(snapshot_dir, agent_id)) as f:
            data = json.load(f)
        if data.get("agent_id") != agent_id or not isinstance(data.get("rows"), list):
            return None
        return data
    except Exception:
        return None


def _age_days(iso: str, now: datetime) -> float | None:
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (now - when).total_seconds() / 86400.0)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# THE choke point
# ---------------------------------------------------------------------------
def apply_private_exclusions(jobs: list[dict], state: PrivateState, key_fn) -> list[dict]:
    """Remove the person's private exclusions from the job set.

    INVARIANT — call this immediately after market collection and BEFORE any
    stage that can produce public output (ranking, scoring, near-miss
    rendering, the edition, the archive). Private state must never enter a
    public-rendering path and rely on downstream filtering for safety.

    This is the general form of the "NEARLY FOOUND" leak fix: a user-passed
    role that reaches rank_with_fit lands in the reject set and is published
    with FOOUND's written reason.
    """
    excluded = state.excluded_keys
    kept = [j for j in jobs if key_fn(j) not in excluded]
    dropped = len(jobs) - len(kept)
    if dropped:
        print(f"[state] excluded {dropped} role(s) on the person's decisions "
              f"({len(state.user_passes)} passed, {len(state.applied)} applied)")
    return kept


def assert_no_private_leak(near_misses: list[dict], state: PrivateState, key_fn) -> None:
    """Belt and braces. Runs immediately before public rendering."""
    leaked = [key_fn(j) for j in near_misses if key_fn(j) in state.excluded_keys]
    if leaked:
        raise RuntimeError(
            "PRIVACY: user-excluded roles reached the public near-miss set: "
            f"{leaked[:5]} — exclusions must be applied before ranking"
        )


# ---------------------------------------------------------------------------
# Bounded ranking context
# ---------------------------------------------------------------------------
# Reason semantics.
#
# Some passes carry information about FIT ("too junior", "wrong craft") and are
# worth showing the ranker as evidence. Others are purely SITUATIONAL — a good
# role at the wrong moment — and should remove the role while teaching nothing.
#
# V1 ships only learning-bearing reasons; the split exists so that adding a
# decision-only reason later (`timing` is the obvious first) is a one-line
# change here rather than a redesign. A decision-only pass still excludes the
# role, still appears in PASSED THIS WEEK, and is still fully reversible — it
# simply never reaches a prompt.
LEARNING_BEARING_REASONS = {
    "seniority", "function", "compensation", "location", "company", "scope",
}
DECISION_ONLY_REASONS: set[str] = set()      # e.g. {"timing"} when it lands


def is_learning_bearing(reason: str | None) -> bool:
    """A pass with no reason still informs (the decision itself is evidence);
    a pass with an explicitly decision-only reason does not."""
    if reason is None:
        return True
    return reason not in DECISION_ONLY_REASONS


RECENT_DECISIONS_INSTRUCTION = (
    "These are judgments about specific opportunities, not durable rules about "
    "the person. Use them only to (a) avoid re-surfacing near-identical roles "
    "and (b) interrogate similar roles more carefully on the dimension named, "
    "sharpening WHY and PAUSE. Do NOT infer general rules. Do NOT lower an "
    "entire category, company, or function. Do NOT rewrite CANDIDATE or MEMORY. "
    "Durable preferences come only from the profile."
)


def recent_decisions_block(state: PrivateState, name: str = "The candidate",
                           today: date | None = None) -> str:
    """Render the bounded context block, or '' when there is nothing to say."""
    rows = state.recent_decisions(today=today)
    if not rows:
        return ""
    lines = [f"RECENT DECISIONS — {name}'s verdicts on specific roles "
             f"in the last {RECENT_DECISIONS_DAYS} days:"]
    for sig in rows:
        snap = sig.get("snapshot") or {}
        bit = f"· {snap.get('company','?')} — {snap.get('title','?')} — passed"
        if sig.get("reason"):
            bit += f" ({sig['reason']})"
        if sig.get("note"):
            bit += f" — \"{sig['note']}\""
        lines.append(bit)
    return "\n".join(lines) + "\n\n" + RECENT_DECISIONS_INSTRUCTION


# ---------------------------------------------------------------------------
# APPLIED source enrichment — OPTIONAL, never a prerequisite
# ---------------------------------------------------------------------------
MAX_SOURCE_ATTEMPTS = 3


def applied_rows_needing_source(state: PrivateState) -> list[dict]:
    """APPLIED rows whose snapshot has no `source` yet.

    Keyed on MISSING SOURCE, never on "an applied row exists". Once a posting
    is captured it is never fetched again — the snapshot is a point-in-time
    artifact, and re-fetching later would quietly rewrite what the company
    asked for on the day the person applied. A refresh, if ever needed, must
    be an explicit request in the snapshot, not a side effect of running.

    Dead URLs back off after MAX_SOURCE_ATTEMPTS so a pulled posting does not
    cost a fetch every weekday forever.
    """
    out = []
    for sig in state.applied.values():
        snap = sig.get("snapshot") or {}
        if snap.get("source") and not snap.get("source_refresh_requested"):
            continue                                  # captured: never again
        if int(snap.get("source_attempts") or 0) >= MAX_SOURCE_ATTEMPTS:
            continue                                  # backed off
        out.append(sig)
    return out


def enrich_applied_source(state: PrivateState, fetch_jd, patch_row) -> int:
    """Fetch each applied role's posting once and patch it into the snapshot.

    HARD RULE — enrichment can only ever ADD. A failed fetch, a dead URL, or a
    patch error must never undo or invalidate APPLIED state:
      · APPLIED remains valid
      · TRACKED remains valid
      · the stored recommendation snapshot remains usable
      · the failure is logged and retried next run

    Every failure mode here is swallowed deliberately. Returns the number of
    rows successfully enriched.
    """
    enriched = 0
    for sig in applied_rows_needing_source(state):
        role_key = sig.get("role_key", "?")
        try:
            snap = dict(sig.get("snapshot") or {})
            url = snap.get("url")
            if not url:
                print(f"[enrich] {role_key}: no url on snapshot, skipping")
                continue
            text = fetch_jd(url)
            if not text:
                _note_attempt(sig, snap, patch_row)
                print(f"[enrich] {role_key}: posting unavailable "
                      "(likely pulled) — APPLIED state unaffected")
                continue
            snap["source"] = {
                "jd_text": text,
                "jd_chars": len(text),
                "jd_fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            snap.pop("source_refresh_requested", None)
            patch_row(sig["id"], {"snapshot": snap})
            enriched += 1
            print(f"[enrich] {role_key}: source captured ({len(text):,} chars)")
        except Exception as exc:
            try:
                _note_attempt(sig, dict(sig.get("snapshot") or {}), patch_row)
            except Exception:
                pass
            print(f"[enrich] {role_key}: enrichment failed ({exc}) — "
                  "APPLIED state unaffected, will retry next run")
    return enriched


def _note_attempt(sig: dict, snap: dict, patch_row) -> None:
    """Record a failed capture so a dead posting backs off instead of costing
    a fetch every weekday. Never touches any guaranteed key."""
    snap["source_attempts"] = int(snap.get("source_attempts") or 0) + 1
    patch_row(sig["id"], {"snapshot": snap})


# ---------------------------------------------------------------------------
# Engine health — heuristic is a DEGRADED success, never a normal one
# ---------------------------------------------------------------------------
ENGINE_DEGRADED_LIMIT = 3      # consecutive heuristic runs before it is a fault


def record_engine_run(snapshot_dir: str, agent_id: str, engine: str) -> dict:
    """Track consecutive heuristic runs for one agent.

    Policy, deliberately not a hard failure on the first miss:
      · ai         -> healthy, counter resets
      · heuristic  -> DEGRADED. The edition still builds and still sends —
                      a temporary model outage should not cost the person
                      their morning — but it is never recorded as a normal
                      success.
      · heuristic x ENGINE_DEGRADED_LIMIT -> FAULT. For a product whose value
                      is judgment, "heuristic for five days" must not read as
                      healthy. The caller decides whether to keep sending;
                      what matters is that it can no longer be silent.
    """
    path = os.path.join(snapshot_dir, f"health_{agent_id}.json")
    health = {"consecutive_heuristic": 0, "last_ai_run": None}
    try:
        with open(path) as f:
            health.update(json.load(f))
    except Exception:
        pass

    now_iso = datetime.now(timezone.utc).isoformat()
    if engine == "ai":
        health["consecutive_heuristic"] = 0
        health["last_ai_run"] = now_iso
        health["status"] = "healthy"
    else:
        health["consecutive_heuristic"] = int(health.get("consecutive_heuristic", 0)) + 1
        health["status"] = ("fault"
                            if health["consecutive_heuristic"] >= ENGINE_DEGRADED_LIMIT
                            else "degraded")

    try:
        os.makedirs(snapshot_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(health, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except Exception as exc:
        print(f"[health] could not persist engine health (non-fatal): {exc}")

    if health["status"] == "fault":
        print(f"[health] FAULT — agent {agent_id} has run on heuristic ranking "
              f"{health['consecutive_heuristic']} times in a row "
              f"(last model-backed run: {health.get('last_ai_run') or 'never'}). "
              "Judgment is the product; this is not a healthy edition.")
    elif health["status"] == "degraded":
        print(f"[health] DEGRADED — agent {agent_id} ran on heuristic ranking "
              f"({health['consecutive_heuristic']}/{ENGINE_DEGRADED_LIMIT} "
              "consecutive). Edition still sent.")
    return health


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except Exception:
        return None
