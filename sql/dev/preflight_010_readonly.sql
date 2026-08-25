-- READ-ONLY production preflight for Migration 010 (memory handles).
-- Run in the Supabase SQL editor BEFORE any mutation. Reports the exact
-- baseline the migration's guard will assert. Expected canonical values:
--   confirm_memory           md5(prosrc)=575219f904761dcc2a0bf5ac9874e9c5
--   settle_synthesis_results md5(prosrc)=96442d384c0fd11cbdc5dad50e1ea992
--   both: SECURITY DEFINER, config search_path=public
--   grants: confirm -> authenticated only; settle -> service_role only
--   memory.handle: must NOT exist yet
select jsonb_pretty(jsonb_build_object(
  'functions', (
    select jsonb_agg(jsonb_build_object(
      'name', p.proname,
      'identity_args', pg_get_function_identity_arguments(p.oid),
      'body_md5', md5(p.prosrc),
      'security_definer', p.prosecdef,
      'config', coalesce(array_to_string(p.proconfig, ';'), ''),
      'owner', pg_get_userbyid(p.proowner),
      'exec_authenticated', has_function_privilege('authenticated', p.oid, 'execute'),
      'exec_anon',          has_function_privilege('anon', p.oid, 'execute'),
      'exec_service_role',  has_function_privilege('service_role', p.oid, 'execute')
    ) order by p.proname)
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in ('confirm_memory','settle_synthesis_results','retract_memory')
  ),
  'memory_handle_column_exists', exists (
    select 1 from information_schema.columns
    where table_name = 'memory' and column_name = 'handle'),
  'memory_handle_constraint_exists', exists (
    select 1 from pg_constraint
    where conname = 'memory_handle_len' and conrelid = 'memory'::regclass)
)) as preflight_010;
