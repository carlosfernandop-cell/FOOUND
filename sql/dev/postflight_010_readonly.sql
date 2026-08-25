-- READ-ONLY production post-flight for Migration 010 (memory handles).
-- Stage-5 production verification. Contains ONLY read-only assertions —
-- no fixtures, no inserts, no writes of any kind. The mutation batteries
-- (test_migration_009/010) are harness/CI evidence and are NEVER run in
-- production. Any assertion failure raises; a clean run ends with the
-- summary row POSTFLIGHT_010_OK.
--
-- Expected post-010 canonical state (computed on the harness after a
-- byte-exact 010 apply over canonical 009):
--   confirm_memory           md5(prosrc) = 99a97fde725916fe384894b175c01e98
--   settle_synthesis_results md5(prosrc) = 2967d3c717b5165405455862a49f3a64
--   retract_memory           UNCHANGED from 009 (fingerprint asserted stable
--                            against itself via signature/secdef/grants only;
--                            010 must not have touched it)

do $$
declare f record; v text; n int;
begin
  -- 1 · memory.handle exists, is TEXT, and is NULLABLE
  select data_type || '/' || is_nullable into v
    from information_schema.columns
    where table_name = 'memory' and column_name = 'handle';
  if v is distinct from 'text/YES' then
    raise exception 'POSTFLIGHT_FAIL: memory.handle missing or wrong shape (%)', coalesce(v,'absent');
  end if;

  -- 2 · exact CHECK definition
  select pg_get_constraintdef(oid) into v from pg_constraint
    where conname = 'memory_handle_len' and conrelid = 'memory'::regclass;
  if v is distinct from
     'CHECK (((handle IS NULL) OR ((char_length(handle) >= 1) AND (char_length(handle) <= 40))))' then
    raise exception 'POSTFLIGHT_FAIL: memory_handle_len definition drift (%)', coalesce(v,'absent');
  end if;

  -- 3 · post-010 function fingerprints + signatures + secdef + search_path
  for f in
    select p.proname,
           md5(p.prosrc)                             as body_md5,
           pg_get_function_identity_arguments(p.oid) as ident,
           p.prosecdef                               as secdef,
           coalesce(array_to_string(p.proconfig, ';'), '') as config
    from pg_proc p join pg_namespace n2 on n2.oid = p.pronamespace
    where n2.nspname = 'public'
      and p.proname in ('confirm_memory','settle_synthesis_results','retract_memory')
  loop
    if not f.secdef or f.config <> 'search_path=public' then
      raise exception 'POSTFLIGHT_FAIL: % secdef/search_path drift (secdef=% config=%)',
        f.proname, f.secdef, f.config;
    end if;
    if f.proname = 'confirm_memory' then
      if f.body_md5 <> '99a97fde725916fe384894b175c01e98' or f.ident <> 'p_rows uuid[]' then
        raise exception 'POSTFLIGHT_FAIL: confirm_memory fingerprint % ident %', f.body_md5, f.ident;
      end if;
    elsif f.proname = 'settle_synthesis_results' then
      if f.body_md5 <> '2967d3c717b5165405455862a49f3a64'
         or f.ident <> 'p_job uuid, p_results jsonb, p_policy jsonb' then
        raise exception 'POSTFLIGHT_FAIL: settle fingerprint % ident %', f.body_md5, f.ident;
      end if;
    elsif f.proname = 'retract_memory' then
      if f.ident <> 'p_rows uuid[]' then
        raise exception 'POSTFLIGHT_FAIL: retract_memory signature drift (%)', f.ident;
      end if;
    end if;
  end loop;

  -- 4 · exact execute-grant boundary (unchanged by 010)
  if not has_function_privilege('authenticated', 'confirm_memory(uuid[])', 'execute')
     or has_function_privilege('anon', 'confirm_memory(uuid[])', 'execute') then
    raise exception 'POSTFLIGHT_FAIL: confirm_memory grants drift';
  end if;
  if not has_function_privilege('authenticated', 'retract_memory(uuid[])', 'execute')
     or has_function_privilege('anon', 'retract_memory(uuid[])', 'execute') then
    raise exception 'POSTFLIGHT_FAIL: retract_memory grants drift';
  end if;
  if not has_function_privilege('service_role',
        'settle_synthesis_results(uuid, jsonb, jsonb)', 'execute')
     or has_function_privilege('authenticated',
        'settle_synthesis_results(uuid, jsonb, jsonb)', 'execute')
     or has_function_privilege('anon',
        'settle_synthesis_results(uuid, jsonb, jsonb)', 'execute') then
    raise exception 'POSTFLIGHT_FAIL: settle grants drift';
  end if;

  -- 5 · absence of unintended schema changes on memory:
  --     exactly the 14 canonical 005 columns + handle = 15, no extras
  select count(*) into n from information_schema.columns where table_name = 'memory';
  if n <> 15 then
    raise exception 'POSTFLIGHT_FAIL: memory has % columns (want 15)', n;
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_name = 'memory'
      and column_name not in
        ('id','agent_id','layer','statement','provenance','evidence','source',
         'status','supersedes','expires','can_affect_search',
         'can_appear_publicly','created_at','last_reinforced','handle')
  ) then
    raise exception 'POSTFLIGHT_FAIL: unexpected column on memory';
  end if;
  -- constraint census: no unexpected new CHECK constraints beyond the known set
  if exists (
    select 1 from pg_constraint
    where conrelid = 'memory'::regclass and contype = 'c'
      and conname not in
        ('memory_layer_check','memory_statement_check','memory_provenance_check',
         'memory_source_check','memory_status_check','memory_handle_len')
  ) then
    raise exception 'POSTFLIGHT_FAIL: unexpected CHECK constraint on memory';
  end if;
end $$;

-- Ownership report (informational — owner names legitimately differ between
-- environments; review, do not gate):
select p.proname, pg_get_userbyid(p.proowner) as owner
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('confirm_memory','settle_synthesis_results','retract_memory')
order by 1;

select 'POSTFLIGHT_010_OK' as result;
