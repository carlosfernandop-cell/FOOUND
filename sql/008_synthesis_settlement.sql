-- ============================================================================
-- FOOUND — Migration 008: synthesis settlement (Synthesis Runner v1, Amendment A.1)
--
-- ONE new service-only function. Zero table changes, zero column changes,
-- zero policy changes, zero index changes, zero edits to any 005/006/007 object.
--
--   settle_synthesis_results(p_job, p_results, p_policy)
--
-- The atomic successful ending of a synthesis run. In ONE transaction it:
--   · verifies and locks the running synthesize job and its agent;
--   · validates the sufficiency policy strictly (exact keys, types, ranges);
--   · applies per-item verdicts: parsed items reading→read, parse failures
--     reading→failed with copy from a CLOSED taxonomy (codes, never prose);
--   · lets withdrawal win: an item deleted by the client before settlement
--     never becomes read, never creates memory, never reinforces memory —
--     and never counts against the client;
--   · validates and writes memory: canonical [{"item":"<uuid>"}] provenance,
--     citations only to items read IN THIS TRANSACTION, for active rows AND
--     tension rows alike (the 007 trigger skips non-active rows by design,
--     so this door enforces tension provenance itself);
--   · forces can_affect_search=false and can_appear_publicly=false, never
--     sets supersedes/expires, refuses layer 'behavior' from this producer;
--   · merges reinforcement citations and bumps last_reinforced;
--   · computes the sufficiency outcome from what ACTUALLY committed,
--     against the validated policy — never from model confidence;
--   · closes through the standing door: finalize_synthesis() is CALLED
--     INTERNALLY (same transaction), so job terminal state, agent flip,
--     and epistemic truth commit together or not at all. 007's finalize
--     guard stays authoritative; no direct terminal jobs UPDATE exists here.
--
-- Any exception anywhere rolls back EVERYTHING: items stay 'reading',
-- no memory exists, the job stays 'running', and the janitor's standing
-- 007 story applies unchanged. There is no committed state in which
-- memory exists but its job is still running.
--
-- finalize_synthesis(p_job,'failed',err) remains the ABORT door, called
-- directly by the runner only when no validated per-item verdicts exist
-- (over-budget batch, storage down, model output invalid after retry,
-- engine error, janitor). Abort-door error text comes from the runner's
-- frozen constants module — the model never authors client-visible copy.
--
-- Paste-safe: no dollar-quoting. Idempotent: safe to re-run.
-- Run AFTER 007.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Preflight: 007's doors must exist. Assert, never silently adopt.
-- ---------------------------------------------------------------------------
do '
begin
  if to_regprocedure(''finalize_synthesis(uuid, text, text)'') is null then
    raise exception ''run_007_first: finalize_synthesis(uuid,text,text) not found'';
  end if;
  if to_regprocedure(''claim_synthesis_batch(uuid)'') is null then
    raise exception ''run_007_first: claim_synthesis_batch(uuid) not found'';
  end if;
end';

-- ---------------------------------------------------------------------------
-- SETTLE: the only successful ending of a synthesis run. Service-only.
--
-- p_results shape (exact keys, all four required, arrays may be empty):
--   {
--     "read":      ["<uuid>", ...],
--     "failed":    [{"item":"<uuid>","code":"<taxonomy code>"}, ...],
--     "memory":    [{"layer":"record|self|model","statement":"...",
--                    "provenance":"stated|extracted|inferred",
--                    "evidence":["<uuid>",...],
--                    "tension":bool?,"is_direction":bool?}, ...],
--     "reinforce": [{"memory":"<uuid>","evidence":["<uuid>",...],
--                    "is_direction":bool?}, ...]
--   }
--
-- memory.source is DERIVED BY THIS DOOR (first surviving cited item's
-- label, truncated to 60) — never accepted from the payload.
-- Every item with submitted_in = p_job needs an explicit verdict,
-- including items the client already deleted (withdrawal is recorded,
-- never silently omitted). Exact normalized duplicate SURVIVING active
-- statements within the payload are refused; a surviving active statement
-- that already exists in the agent's active memory must arrive through
-- "reinforce". A row discarded for withdrawn evidence contributes nothing —
-- no memory, no reinforcement, and no validation side effects.
--
-- p_policy shape (exact keys, no others):
--   {"min_grounded":int 1..100, "require_record":bool, "require_self":bool,
--    "require_direction":bool, "max_failed_ratio":number 0..1}
--
-- Failure-code taxonomy (closed; unknown codes are an exception):
--   unreadable   → "FOOUND couldn't read this file. Remove it and try again."
--   no_text_pdf  → "FOOUND couldn't find readable text in this PDF.
--                   Add a text-based PDF, DOCX, TXT or MD instead."
--   too_large    → "This file is too large for FOOUND to read.
--                   Add a shorter document instead."
-- Whole-batch failure copy (owned by this function, used only when zero
-- items were readable and at least one failed):
--                  "FOOUND couldn't read what you added.
--                   Remove the failed items and try again."
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
  grounded int := 0;
  has_record boolean := false; has_self boolean := false;
  has_direction boolean := false;
  cur_status text; cur_job uuid;
  item_id uuid; code text; reason text;
  lay text; prov text; stmt text; src text;
  norm text; stmts_seen text[] := ''{}'';
  is_tension boolean; is_dir boolean;
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

  -- ---- 4 · read verdicts: reading→read, or withdrawn wins
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
  -- claim. Withdrawal is recorded, never silently omitted. After this
  -- check, finalize''s sweep must find nothing (asserted in tests).
  select count(*) into n_left from evidence_items
    where submitted_in = p_job and not (id = any(ids_seen));
  if n_left > 0 then raise exception ''verdict_missing''; end if;

  -- ---- 7 · memory rows: identical validation for active AND tension rows.
  -- The 007 trigger skips non-active rows by design; THIS door holds the
  -- provenance line for tension rows itself. Citations must be items read
  -- in THIS transaction; withdrawn citations discard the row (withdrawal
  -- wins); anything else is an engine bug and rolls everything back.
  for r in select * from jsonb_array_elements(p_results->''memory'') loop
    if jsonb_typeof(r) <> ''object'' then raise exception ''invalid_results''; end if;
    for k in select jsonb_object_keys(r) loop
      if k not in (''layer'',''statement'',''provenance'',''evidence'',
                   ''tension'',''is_direction'') then
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
      -- Withdrawal wins COMPLETELY: the row never lands, and it leaves no
      -- validation side effects — it is never added to stmts_seen and can
      -- never cause duplicate_statement or reinforce_required for others.
      n_discarded := n_discarded + 1;
    else
      -- Only SURVIVING active rows face the duplicate/reinforcement rules:
      -- exact normalized duplicates among surviving payload rows are
      -- refused, and a surviving active statement already present in the
      -- agent''s active memory must arrive through "reinforce" instead.
      if not is_tension then
        norm := btrim(lower(regexp_replace(stmt, ''\s+'', '' '', ''g'')));
        if norm = any(stmts_seen) then
          raise exception ''duplicate_statement''; end if;
        perform 1 from memory
          where agent_id = j.agent_id and status = ''active''
            and btrim(lower(regexp_replace(statement, ''\s+'', '' '', ''g''))) = norm;
        if found then raise exception ''reinforce_required''; end if;
        stmts_seen := stmts_seen || norm;
      end if;
      -- source is derived HERE, never accepted from the payload:
      -- the first surviving cited item''s label, truncated to 60.
      select left(ei.label, 60) into src from evidence_items ei
        where ei.id = (canon->0->>''item'')::uuid;
      insert into memory
        (agent_id, layer, statement, provenance, evidence, source,
         status, can_affect_search, can_appear_publicly)
      values
        (j.agent_id, lay, stmt, prov, canon, src,
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

  -- ---- 8 · reinforcements: same-agent active rows only; citations merged
  -- canonically; withdrawn-only support drops the reinforcement.
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
      n_dropped_reinf := n_dropped_reinf + 1;   -- only withdrawn support left
    else
      update memory
         set evidence = m_ev || add_refs,
             last_reinforced = now()
       where id = mem_id;
      n_reinforced := n_reinforced + 1;
      grounded := grounded + 1;
      if m_layer = ''record'' then has_record := true; end if;
      if m_layer = ''self''   then has_self   := true; end if;
      if is_dir and m_prov in (''stated'',''extracted'') then
        has_direction := true; end if;
    end if;
  end loop;

  -- ---- 9 · outcome from COMMITTED truth. Withdrawn items are excluded
  -- from the failure denominator: removal is a client decision, not a
  -- processing failure. The model had no vote at any point above.
  if n_read = 0 and n_failed > 0 then
    outcome := ''failed'';
    err := ''FOOUND couldn''''t read what you added. Remove the failed items and try again.'';
  elsif n_read = 0 then
    outcome := ''needs_more_evidence'';   -- everything withdrawn: their right
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
  -- finalize_synthesis sets the 007 guard marker itself; job terminal
  -- state and the agent flip commit atomically with everything above.
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
    ''grounded_total'', grounded,
    ''finalize'', fin);
end';

revoke execute on function settle_synthesis_results(uuid, jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function settle_synthesis_results(uuid, jsonb, jsonb)
  to service_role;
