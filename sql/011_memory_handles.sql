-- ============================================================================
-- FOOUND — Migration 011: memory handles (Memory Handle Contract Plan v2)
--
-- Adds ONE nullable, engine-owned column and threads it through the two doors
-- that insert memory rows. Nothing else changes: RLS untouched, client doors
-- accept no new arguments, retract/claim/finalize untouched.
--
--   memory.handle              additive display metadata: the engine's 1-3
--                              word compression of the statement. CHECK caps
--                              at 40 chars (defensive ceiling; runner targets
--                              <=24). NULL is always a safe fallback.
--   confirm_memory             REPLACED: successor insert carries m.handle
--                              (marked "-- 011:"); otherwise byte-identical
--                              to 009 (generated from the 009 source text).
--   settle_synthesis_results   REPLACED: accepts an OPTIONAL ''handle'' key
--                              per result item; malformed/empty/oversized
--                              degrades to NULL and never blocks settlement
--                              (marked "-- 011:"); otherwise byte-identical
--                              to 009 (generated from the 009 source text).
--
-- SEMANTIC SAFEGUARD (canonical, per plan): the handle is NOT evidence and
-- NOT truth-bearing. It never enters synthesis as a fact, never counts
-- toward sufficiency, never participates in tension detection or the
-- suppression/duplicate norms (which remain statement-based), is never
-- citable, and never influences the Working Brief. It summarizes the
-- statement''s existing meaning and may never introduce a new claim.
-- Migration numbering: 010 remains reserved for First Read (roadmap).
--
-- Paste-safe: no dollar-quoting. Idempotent: safe to re-run. Run AFTER 009.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Preflight: 009 must be live. Assert, never silently adopt.
-- ---------------------------------------------------------------------------
do '
begin
  if to_regprocedure(''confirm_memory(uuid[])'') is null then
    raise exception ''run_009_first: confirm_memory not found'';
  end if;
  if to_regprocedure(''retract_memory(uuid[])'') is null then
    raise exception ''run_009_first: retract_memory not found'';
  end if;
  if to_regprocedure(''settle_synthesis_results(uuid, jsonb, jsonb)'') is null then
    raise exception ''run_008_first: settle_synthesis_results not found'';
  end if;
end';

-- ---------------------------------------------------------------------------
-- 1 · The column. Nullable, additive; CHECK is the defensive ceiling.
-- ---------------------------------------------------------------------------
alter table memory add column if not exists handle text;

do '
begin
  if not exists (
    select 1 from pg_constraint
    where conname = ''memory_handle_len'' and conrelid = ''memory''::regclass
  ) then
    alter table memory add constraint memory_handle_len
      check (handle is null or char_length(handle) between 1 and 40);
  end if;
end';

-- ---------------------------------------------------------------------------
-- 2 · CONFIRM: byte-identical to 009 except the successor now inherits the
--     handle (lines marked "-- 011:").
-- ---------------------------------------------------------------------------
create or replace function confirm_memory(p_rows uuid[]) returns jsonb
language plpgsql security definer set search_path = public as '
declare
  uid uuid; m memory%rowtype; rid uuid; i int; n int := 0;
  seen uuid[] := ''{}'';
begin
  uid := auth.uid();
  if uid is null then raise exception ''not_authenticated''; end if;
  if p_rows is null or coalesce(array_length(p_rows, 1), 0) = 0 then
    raise exception ''empty_input''; end if;
  if array_length(p_rows, 1) > 200 then
    raise exception ''too_many_targets''; end if;

  -- ---- pass 1 · validate the COMPLETE set; no mutation may precede this
  for i in 1..array_length(p_rows, 1) loop
    rid := p_rows[i];
    if rid = any(seen) then raise exception ''duplicate_target''; end if;
    seen := seen || rid;
    select * into m from memory where id = rid for update;
    if m.id is null then raise exception ''no_such_memory''; end if;
    if not exists (select 1 from agents a
                   where a.id = m.agent_id and a.user_id = uid) then
      raise exception ''memory_not_owned''; end if;
    if m.status = ''tension'' then raise exception ''tension_not_actionable''; end if;
    if m.status <> ''active'' then raise exception ''memory_not_active''; end if;
    if m.provenance = ''confirmed'' then raise exception ''already_confirmed''; end if;
    if m.provenance not in (''stated'',''extracted'',''inferred'') then
      raise exception ''provenance_not_confirmable''; end if;
  end loop;

  -- ---- pass 2 · mutate: supersession insert + status flip, per row
  for i in 1..array_length(p_rows, 1) loop
    select * into m from memory where id = p_rows[i];
    insert into memory
      (agent_id, layer, statement, provenance, evidence, source, handle,   -- 011: handle
       status, supersedes, can_affect_search, can_appear_publicly)
    values
      (m.agent_id, m.layer, m.statement, ''confirmed'', m.evidence, m.source,
       m.handle,                          -- 011: successor inherits the handle
       ''active'', m.id, false, false);   -- flags forced by the door, always
    update memory set status = ''superseded'' where id = m.id;
    n := n + 1;
  end loop;

  return jsonb_build_object(''status'',''confirmed'', ''count'', n);
end';
revoke execute on function confirm_memory(uuid[]) from public, anon;
grant execute on function confirm_memory(uuid[]) to authenticated;

-- ---------------------------------------------------------------------------
-- 3 · SETTLE: byte-identical to 009 except (a) the per-item key whitelist
--     accepts an OPTIONAL ''handle'', (b) fail-soft parse to hnd, and
--     (c) the insert writes it (all marked "-- 011:"). The suppression
--     guard, duplicate norms, sufficiency, and tension logic are untouched
--     and remain statement-based - the handle is invisible to all of them.
-- ---------------------------------------------------------------------------
create or replace function settle_synthesis_results(
  p_job uuid, p_results jsonb, p_policy jsonb)
returns jsonb
language plpgsql security definer set search_path = public as '
declare
  j jobs%rowtype;
  k text; e jsonb; r jsonb; c jsonb;
  v text;
  min_grounded int; max_failed numeric;
  req_record boolean; req_self boolean; req_direction boolean;
  ids_seen uuid[] := ''{}'';
  read_ids uuid[] := ''{}'';
  withdrawn_ids uuid[] := ''{}'';
  row_cites uuid[];
  n_read int := 0; n_failed int := 0; n_withdrawn int := 0;
  n_inserted int := 0; n_tension int := 0; n_reinforced int := 0;
  n_discarded int := 0; n_dropped_reinf int := 0;
  n_suppressed int := 0;                                -- 009: guard counter
  grounded int := 0;
  has_record boolean := false; has_self boolean := false;
  has_direction boolean := false;
  cur_status text; cur_job uuid;
  item_id uuid; code text; reason text;
  lay text; prov text; stmt text; src text;
  norm text; stmts_seen text[] := ''{}'';
  is_tension boolean; is_dir boolean;
  hnd text;                                       -- 011
  cites_withdrawn boolean;
  canon jsonb;
  mem_id uuid; m_agent uuid; m_status text; m_prov text;
  m_layer text; m_ev jsonb;
  add_refs jsonb;
  reinf_seen uuid[] := ''{}'';
  n_left int;
  outcome text; err text := null;
  fin jsonb;
begin
  -- ---- 1 · verify and lock: job then agent, same order as claim/finalize
  select * into j from jobs where id = p_job for update;
  if j.id is null then raise exception ''no_such_job''; end if;
  if j.type <> ''synthesize'' then raise exception ''not_synthesize''; end if;
  if j.status <> ''running'' then raise exception ''job_not_running''; end if;
  perform 1 from agents where id = j.agent_id for update;

  -- ---- 2 · policy: exact shape, or nothing commits
  if p_policy is null or jsonb_typeof(p_policy) <> ''object'' then
    raise exception ''invalid_policy''; end if;
  for k in select jsonb_object_keys(p_policy) loop
    if k not in (''min_grounded'',''require_record'',''require_self'',
                 ''require_direction'',''max_failed_ratio'') then
      raise exception ''invalid_policy''; end if;
  end loop;
  if jsonb_typeof(p_policy->''min_grounded'')     is distinct from ''number''
  or jsonb_typeof(p_policy->''require_record'')   is distinct from ''boolean''
  or jsonb_typeof(p_policy->''require_self'')     is distinct from ''boolean''
  or jsonb_typeof(p_policy->''require_direction'')is distinct from ''boolean''
  or jsonb_typeof(p_policy->''max_failed_ratio'') is distinct from ''number'' then
    raise exception ''invalid_policy''; end if;
  if (p_policy->>''min_grounded'')::numeric <> floor((p_policy->>''min_grounded'')::numeric)
  or (p_policy->>''min_grounded'')::numeric < 1
  or (p_policy->>''min_grounded'')::numeric > 100
  or (p_policy->>''max_failed_ratio'')::numeric < 0
  or (p_policy->>''max_failed_ratio'')::numeric > 1 then
    raise exception ''invalid_policy''; end if;
  min_grounded  := (p_policy->>''min_grounded'')::numeric::int;
  max_failed    := (p_policy->>''max_failed_ratio'')::numeric;
  req_record    := (p_policy->>''require_record'')::boolean;
  req_self      := (p_policy->>''require_self'')::boolean;
  req_direction := (p_policy->>''require_direction'')::boolean;

  -- ---- 3 · results envelope: exactly four keys, all arrays
  if p_results is null or jsonb_typeof(p_results) <> ''object'' then
    raise exception ''invalid_results''; end if;
  for k in select jsonb_object_keys(p_results) loop
    if k not in (''read'',''failed'',''memory'',''reinforce'') then
      raise exception ''invalid_results''; end if;
  end loop;
  if jsonb_typeof(p_results->''read'')      is distinct from ''array''
  or jsonb_typeof(p_results->''failed'')    is distinct from ''array''
  or jsonb_typeof(p_results->''memory'')    is distinct from ''array''
  or jsonb_typeof(p_results->''reinforce'') is distinct from ''array'' then
    raise exception ''invalid_results''; end if;

  -- ---- 4 · read verdicts: reading->read, or withdrawn wins
  for e in select * from jsonb_array_elements(p_results->''read'') loop
    if jsonb_typeof(e) <> ''string'' then raise exception ''invalid_results''; end if;
    v := lower(e #>> ''{}'');
    if v !~* ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'' then
      raise exception ''invalid_uuid''; end if;
    item_id := v::uuid;
    if item_id = any(ids_seen) then raise exception ''duplicate_verdict''; end if;
    ids_seen := ids_seen || item_id;
    select status, submitted_in into cur_status, cur_job
      from evidence_items where id = item_id for update;
    if not found or cur_job is distinct from p_job then
      raise exception ''item_not_in_batch''; end if;
    if cur_status = ''deleted'' then
      withdrawn_ids := withdrawn_ids || item_id;
      n_withdrawn := n_withdrawn + 1;
    elsif cur_status = ''reading'' then
      update evidence_items set status = ''read'', read_at = now()
        where id = item_id;
      read_ids := read_ids || item_id;
      n_read := n_read + 1;
    else
      raise exception ''item_state_conflict'';
    end if;
  end loop;

  -- ---- 5 · failure verdicts: closed taxonomy, or withdrawn wins silently
  for e in select * from jsonb_array_elements(p_results->''failed'') loop
    if jsonb_typeof(e) <> ''object''
       or jsonb_typeof(e->''item'') is distinct from ''string''
       or jsonb_typeof(e->''code'') is distinct from ''string'' then
      raise exception ''invalid_results''; end if;
    for k in select jsonb_object_keys(e) loop
      if k not in (''item'',''code'') then
        raise exception ''invalid_results''; end if;
    end loop;
    v := lower(e->>''item'');
    if v !~* ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'' then
      raise exception ''invalid_uuid''; end if;
    item_id := v::uuid;
    if item_id = any(ids_seen) then raise exception ''duplicate_verdict''; end if;
    ids_seen := ids_seen || item_id;
    code := e->>''code'';
    if code = ''unreadable'' then
      reason := ''FOOUND couldn''''t read this file. Remove it and try again.'';
    elsif code = ''no_text_pdf'' then
      reason := ''FOOUND couldn''''t find readable text in this PDF. Add a text-based PDF, DOCX, TXT or MD instead.'';
    elsif code = ''too_large'' then
      reason := ''This file is too large for FOOUND to read. Add a shorter document instead.'';
    else
      raise exception ''invalid_failure_code'';
    end if;
    select status, submitted_in into cur_status, cur_job
      from evidence_items where id = item_id for update;
    if not found or cur_job is distinct from p_job then
      raise exception ''item_not_in_batch''; end if;
    if cur_status = ''deleted'' then
      withdrawn_ids := withdrawn_ids || item_id;
      n_withdrawn := n_withdrawn + 1;
    elsif cur_status = ''reading'' then
      update evidence_items set status = ''failed'', failure_reason = reason
        where id = item_id;
      n_failed := n_failed + 1;
    else
      raise exception ''item_state_conflict'';
    end if;
  end loop;

  -- ---- 6 · verdict completeness: EVERY item with submitted_in = p_job
  -- needs an explicit verdict — including items the client deleted after
  -- claim. Withdrawal is recorded, never silently omitted.
  select count(*) into n_left from evidence_items
    where submitted_in = p_job and not (id = any(ids_seen));
  if n_left > 0 then raise exception ''verdict_missing''; end if;

  -- ---- 7 · memory rows: identical validation for active AND tension rows.
  for r in select * from jsonb_array_elements(p_results->''memory'') loop
    if jsonb_typeof(r) <> ''object'' then raise exception ''invalid_results''; end if;
    for k in select jsonb_object_keys(r) loop
      if k not in (''layer'',''statement'',''provenance'',''evidence'',
                   ''tension'',''is_direction'',''handle'') then   -- 011: optional handle
        raise exception ''invalid_results''; end if;
    end loop;
    if jsonb_typeof(r->''layer'')      is distinct from ''string''
    or jsonb_typeof(r->''statement'')  is distinct from ''string''
    or jsonb_typeof(r->''provenance'') is distinct from ''string'' then
      raise exception ''invalid_results''; end if;
    lay  := r->>''layer'';
    stmt := r->>''statement'';
    prov := r->>''provenance'';
    if lay = ''behavior'' then raise exception ''layer_not_allowed''; end if;
    if lay not in (''record'',''self'',''model'') then
      raise exception ''invalid_results''; end if;
    if prov not in (''stated'',''extracted'',''inferred'') then
      raise exception ''provenance_not_allowed''; end if;
    if length(stmt) < 1 or length(stmt) > 1000 then
      raise exception ''invalid_results''; end if;
    if r ? ''tension'' and jsonb_typeof(r->''tension'') <> ''boolean'' then
      raise exception ''invalid_results''; end if;
    if r ? ''is_direction'' and jsonb_typeof(r->''is_direction'') <> ''boolean'' then
      raise exception ''invalid_results''; end if;
    is_tension := coalesce((r->>''tension'')::boolean, false);
    is_dir     := coalesce((r->>''is_direction'')::boolean, false);

    -- 011: optional presentation handle. Fail-soft by contract: malformed,
    -- empty, or oversized handles degrade to NULL; settlement never blocks
    -- on a handle. The handle is presentation metadata; it is not evidence,
    -- not truth-bearing, and takes no part in suppression/duplicate norms.
    hnd := null;                                                     -- 011
    if r ? ''handle'' and jsonb_typeof(r->''handle'') = ''string'' then -- 011
      hnd := nullif(btrim(r->>''handle''), '''');                    -- 011
      if hnd is not null and char_length(hnd) > 40 then              -- 011
        hnd := null;                                                 -- 011
      end if;                                                        -- 011
    end if;                                                          -- 011

    if jsonb_typeof(r->''evidence'') is distinct from ''array''
       or jsonb_array_length(r->''evidence'') = 0 then
      raise exception ''invalid_results''; end if;
    canon := ''[]''::jsonb;
    row_cites := ''{}'';
    cites_withdrawn := false;
    for c in select * from jsonb_array_elements(r->''evidence'') loop
      if jsonb_typeof(c) <> ''string'' then raise exception ''invalid_results''; end if;
      v := lower(c #>> ''{}'');
      if v !~* ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'' then
        raise exception ''invalid_uuid''; end if;
      item_id := v::uuid;
      if item_id = any(row_cites) then raise exception ''duplicate_citation''; end if;
      row_cites := row_cites || item_id;
      if item_id = any(withdrawn_ids) then
        cites_withdrawn := true;
      elsif item_id = any(read_ids) then
        canon := canon || jsonb_build_array(jsonb_build_object(''item'', v));
      else
        raise exception ''evidence_not_committed'';
      end if;
    end loop;

    if cites_withdrawn then
      -- Withdrawal wins COMPLETELY: no memory, no side effects.
      n_discarded := n_discarded + 1;
    else
      if not is_tension then
        norm := btrim(lower(regexp_replace(stmt, ''\s+'', '' '', ''g'')));
        -- 009: RETRACTION SUPPRESSION GUARD (defense in depth behind the
        -- model fence). Exact-normalization match against a retracted row
        -- of this agent -> the statement is skipped with ZERO side effects:
        -- never inserted, never in stmts_seen, counted honestly. Semantic
        -- (paraphrase) equivalence is deliberately NOT judged here — that
        -- belongs exclusively to the model-level fence.
        perform 1 from memory
          where agent_id = j.agent_id and status = ''retracted''
            and btrim(lower(regexp_replace(statement, ''\s+'', '' '', ''g''))) = norm;
        if found then
          n_suppressed := n_suppressed + 1;           -- 009
          continue;                                    -- 009
        end if;                                        -- 009
        if norm = any(stmts_seen) then
          raise exception ''duplicate_statement''; end if;
        perform 1 from memory
          where agent_id = j.agent_id and status = ''active''
            and btrim(lower(regexp_replace(statement, ''\s+'', '' '', ''g''))) = norm;
        if found then raise exception ''reinforce_required''; end if;
        stmts_seen := stmts_seen || norm;
      end if;
      -- source is derived HERE, never accepted from the payload.
      select left(ei.label, 60) into src from evidence_items ei
        where ei.id = (canon->0->>''item'')::uuid;
      insert into memory
        (agent_id, layer, statement, provenance, evidence, source, handle,  -- 011
         status, can_affect_search, can_appear_publicly)
      values
        (j.agent_id, lay, stmt, prov, canon, src, hnd,                      -- 011
         case when is_tension then ''tension'' else ''active'' end,
         false, false);                  -- forced by the door, always
      if is_tension then
        n_tension := n_tension + 1;
      else
        n_inserted := n_inserted + 1;
        grounded := grounded + 1;
        if lay = ''record'' then has_record := true; end if;
        if lay = ''self''   then has_self   := true; end if;
        if is_dir and prov in (''stated'',''extracted'') then
          has_direction := true; end if;
      end if;
    end if;
  end loop;

  -- ---- 8 · reinforcements: same-agent ACTIVE rows only; citations merged
  for r in select * from jsonb_array_elements(p_results->''reinforce'') loop
    if jsonb_typeof(r) <> ''object''
       or jsonb_typeof(r->''memory'') is distinct from ''string'' then
      raise exception ''invalid_results''; end if;
    for k in select jsonb_object_keys(r) loop
      if k not in (''memory'',''evidence'',''is_direction'') then
        raise exception ''invalid_results''; end if;
    end loop;
    v := lower(r->>''memory'');
    if v !~* ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'' then
      raise exception ''invalid_uuid''; end if;
    mem_id := v::uuid;
    if mem_id = any(reinf_seen) then raise exception ''duplicate_reinforce''; end if;
    reinf_seen := reinf_seen || mem_id;
    if r ? ''is_direction'' and jsonb_typeof(r->''is_direction'') <> ''boolean'' then
      raise exception ''invalid_results''; end if;
    is_dir := coalesce((r->>''is_direction'')::boolean, false);

    select agent_id, status, provenance, layer, evidence
      into m_agent, m_status, m_prov, m_layer, m_ev
      from memory where id = mem_id for update;
    if not found or m_agent <> j.agent_id or m_status <> ''active'' then
      raise exception ''reinforce_target_invalid''; end if;

    if jsonb_typeof(r->''evidence'') is distinct from ''array''
       or jsonb_array_length(r->''evidence'') = 0 then
      raise exception ''invalid_results''; end if;
    add_refs := ''[]''::jsonb;
    row_cites := ''{}'';
    cites_withdrawn := false;
    for c in select * from jsonb_array_elements(r->''evidence'') loop
      if jsonb_typeof(c) <> ''string'' then raise exception ''invalid_results''; end if;
      v := lower(c #>> ''{}'');
      if v !~* ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'' then
        raise exception ''invalid_uuid''; end if;
      item_id := v::uuid;
      if item_id = any(row_cites) then raise exception ''duplicate_citation''; end if;
      row_cites := row_cites || item_id;
      if item_id = any(withdrawn_ids) then
        cites_withdrawn := true;
      elsif item_id = any(read_ids) then
        if not (m_ev @> jsonb_build_array(jsonb_build_object(''item'', v))) then
          add_refs := add_refs || jsonb_build_array(jsonb_build_object(''item'', v));
        end if;
      else
        raise exception ''evidence_not_committed'';
      end if;
    end loop;

    if jsonb_array_length(add_refs) = 0 and cites_withdrawn then
      n_dropped_reinf := n_dropped_reinf + 1;
    else
      update memory
         set evidence = m_ev || add_refs,
             last_reinforced = now()
       where id = mem_id;
      n_reinforced := n_reinforced + 1;
      grounded := grounded + 1;
      if m_layer = ''record'' then has_record := true; end if;
      if m_layer = ''self''   then has_self   := true; end if;
      -- 009: ''confirmed'' qualifies for the grounded-direction gate on
      -- reinforcement — a client-affirmed direction re-supported by new
      -- evidence is the STRONGEST direction signal the system holds.
      -- ''inferred'' remains excluded, as everywhere.
      if is_dir and m_prov in (''stated'',''extracted'',''confirmed'') then
        has_direction := true; end if;
    end if;
  end loop;

  -- ---- 9 · outcome from COMMITTED truth.
  if n_read = 0 and n_failed > 0 then
    outcome := ''failed'';
    err := ''FOOUND couldn''''t read what you added. Remove the failed items and try again.'';
  elsif n_read = 0 then
    outcome := ''needs_more_evidence'';
  elsif grounded >= min_grounded
        and (not req_record or has_record)
        and (not req_self or has_self)
        and (not req_direction or has_direction)
        and (n_failed::numeric / (n_read + n_failed)::numeric) < max_failed then
    outcome := ''mirror_ready'';
  else
    outcome := ''needs_more_evidence'';
  end if;

  -- ---- 10 · close through the standing door, SAME transaction.
  fin := finalize_synthesis(p_job, outcome, err);

  return jsonb_build_object(
    ''status'', ''settled'',
    ''outcome'', outcome,
    ''items_read'', n_read,
    ''items_failed'', n_failed,
    ''items_withdrawn'', n_withdrawn,
    ''memory_inserted'', n_inserted,
    ''tension_rows'', n_tension,
    ''reinforced'', n_reinforced,
    ''statements_discarded'', n_discarded,
    ''reinforcements_dropped'', n_dropped_reinf,
    ''suppressed_retracted'', n_suppressed,             -- 009
    ''grounded_total'', grounded,
    ''finalize'', fin);
end';

revoke execute on function settle_synthesis_results(uuid, jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function settle_synthesis_results(uuid, jsonb, jsonb)
  to service_role;

-- ---------------------------------------------------------------------------
-- Post-flight self-check
-- ---------------------------------------------------------------------------
do '
begin
  if not exists (select 1 from information_schema.columns
                 where table_name = ''memory'' and column_name = ''handle'') then
    raise exception ''011_failed: handle column missing'';
  end if;
end';
