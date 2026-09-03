-- READ-ONLY postflight for migration 013 (Working Brief doors). Safe on
-- production: SELECTs only. Expect every row to read 'ok'.
select 'activate_brief exists' as check_,
       case when to_regprocedure('activate_brief(uuid)') is not null then 'ok' else 'MISSING' end as result
union all
select 'next_brief_version exists',
       case when to_regprocedure('next_brief_version(uuid)') is not null then 'ok' else 'MISSING' end
union all
select 'jobs.type allows propose_brief',
       case when exists (
         select 1 from pg_constraint
          where conrelid = 'public.jobs'::regclass and conname = 'jobs_type_check'
            and pg_get_constraintdef(oid) like '%propose_brief%') then 'ok' else 'MISSING' end
union all
select 'client may express propose_brief',
       case when exists (
         select 1 from pg_policies
          where tablename = 'jobs' and policyname = 'jobs_owner_insert'
            and coalesce(with_check, '') like '%propose_brief%') then 'ok' else 'MISSING' end
union all
select 'one active brief per agent (005) still enforced',
       case when exists (select 1 from pg_indexes where tablename = 'briefs' and indexname = 'one_active_brief_per_agent') then 'ok' else 'MISSING' end
union all
select 'activate_brief not callable by anon',
       case when not has_function_privilege('anon', 'activate_brief(uuid)', 'execute') then 'ok' else 'EXPOSED' end
union all
select '№001 active brief untouched (still exactly one, confirmed)',
       case when (select count(*) from briefs b join agents a on a.id = b.agent_id
                   where a.agent_no = 1 and b.state = 'active' and b.confirmed_at is not null) = 1 then 'ok' else 'CHECK' end;
