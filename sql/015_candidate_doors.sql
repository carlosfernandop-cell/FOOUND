-- ============================================================================
-- FOOUND — Migration 015: the Candidate doors (Move 4 — the public face)
--
-- Decisions (Carlos, 2026-09-03): a Candidate page is FOOUND's most shared
-- artefact. FOOUND drafts it from confirmed Memory; the person confirms it;
-- nothing unconfirmed is ever public. A portrait is required, black and
-- white. The page says Based, never Open to: the Brief's WHERE is private.
--
-- Search result (do not invent what exists):
--   · candidates (005): versioned rows, draft -> approved -> published ->
--     unpublished, slug with a partial unique index on published slugs,
--     owner READ policy only, content is text. No door writes a published
--     row; no public read path exists anywhere.
--   · jobs.type (013): synthesize · compile_brief · first_edition ·
--     refresh_readiness · propose_brief. The client may express four.
--   · storage: the private 'feeds' bucket (007) with owner-prefix policies.
--
-- What this adds, and nothing else:
--   · candidates.page jsonb — the structured page (see PAGE SHAPE below);
--     content keeps its text column for prose, defaulting to ''.
--   · Owner may INSERT and UPDATE draft rows (page only): the person edits
--     what FOOUND drafted before confirming. Nothing else moves by row edit.
--   · publish_candidate(p_candidate uuid, p_page jsonb) — the confirmation
--     act. Validates ownership and shape (name, line, ≥1 chapter, portrait),
--     unpublishes the current published page, sets state = published,
--     slug = the agent's three-digit serial, approved_at = now(). Returns
--     'published:<slug>' or 'blocked:<reason>'. Never raises to the client.
--   · candidate_public(p_slug text) — the only public read. Returns the
--     published page, the serial and the date, for anon and authenticated.
--     Nothing else on the row, nothing from any other table.
--   · jobs.type gains 'draft_candidate' — the engine drafts a page from
--     confirmed Memory; the client may express it.
--   · storage bucket 'portraits' — public read (the page is public), owner
--     writes one object at <uid>/portrait.jpg. The app converts to black
--     and white before upload; the database cannot see pixels, so the
--     policy holds the path and the type, and the app holds the tone.
--
-- PAGE SHAPE (jsonb object; every key optional except where marked):
--   name: [text]                      required, one line per entry
--   line: text                        required, FOOUND's dossier sentence
--   now: text · based: text · since: text
--   portrait: text                    required, public URL in 'portraits'
--   links: {linkedin, portfolio, cv, email}
--   chapters: [{company, years, at_rest, narrative, meta}]   ≥1 required
--   trusted_with: [{word, line}]      at most 3
--   own_words: text
--   work: [{title, url, host}]
--   references: [{name, quote, who}]
--   languages: text                    e.g. 'EN / SV / DE'
--
-- Paste-safe: no dollar-quoting. Idempotent: safe to re-run.
-- Prove on disposable Postgres only — never the live FOOUND project.
-- ============================================================================

do '
begin
  if to_regclass(''public.candidates'') is null then
    raise exception ''run_005_first: candidates not found'';
  end if;
  if to_regclass(''public.jobs'') is null then
    raise exception ''run_006_first: jobs not found'';
  end if;
end';

-- ---- the page ---------------------------------------------------------------
alter table candidates add column if not exists page jsonb;
alter table candidates alter column content set default '';
alter table candidates add column if not exists published_at timestamptz;

-- ---- the owner edits drafts; nothing else by row --------------------------
drop policy if exists candidates_owner_draft on candidates;
create policy candidates_owner_draft on candidates
  for insert with check (state = 'draft'
    and exists (select 1 from agents a
                where a.id = candidates.agent_id and a.user_id = auth.uid()));
drop policy if exists candidates_owner_edit_draft on candidates;
create policy candidates_owner_edit_draft on candidates
  for update
  using (state = 'draft'
    and exists (select 1 from agents a
                where a.id = candidates.agent_id and a.user_id = auth.uid()))
  with check (state = 'draft');

-- ---- jobs.type: add draft_candidate ------------------------------------------
alter table jobs drop constraint if exists jobs_type_check;
alter table jobs add constraint jobs_type_check
  check (type in ('synthesize','compile_brief','first_edition',
                  'refresh_readiness','propose_brief','draft_candidate'));
drop policy if exists jobs_owner_insert on jobs;
create policy jobs_owner_insert on jobs
  for insert with check (
    status = 'queued'
    and started_at is null and completed_at is null and error is null
    and type in ('synthesize','compile_brief','refresh_readiness',
                 'propose_brief','draft_candidate')
    and exists (select 1 from agents a
                where a.id = jobs.agent_id and a.user_id = auth.uid()));

-- ---- shape check, shared by the door ----------------------------------------
create or replace function candidate_page_problem(p_page jsonb) returns text
language plpgsql immutable as '
begin
  if p_page is null or jsonb_typeof(p_page) <> ''object'' then return ''not_an_object''; end if;
  if jsonb_typeof(p_page->''name'') <> ''array'' or jsonb_array_length(p_page->''name'') = 0
     or length(trim(coalesce(p_page->''name''->>0, ''''))) = 0 then return ''no_name''; end if;
  if length(trim(coalesce(p_page->>''line'', ''''))) = 0 then return ''no_line''; end if;
  if jsonb_typeof(p_page->''chapters'') <> ''array'' or jsonb_array_length(p_page->''chapters'') = 0
     then return ''no_chapters''; end if;
  if length(trim(coalesce(p_page->>''portrait'', ''''))) = 0 then return ''no_portrait''; end if;
  if jsonb_typeof(p_page->''trusted_with'') = ''array'' and jsonb_array_length(p_page->''trusted_with'') > 3
     then return ''too_many_trusted_with''; end if;
  if p_page ? ''open_to'' then return ''open_to_is_private''; end if;
  return null;
end';

-- ---- publish_candidate: draft -> published, by the person, atomically -----
create or replace function publish_candidate(p_candidate uuid, p_page jsonb) returns text
language plpgsql security definer set search_path = public as '
declare
  uid uuid;
  c candidates%rowtype;
  a agents%rowtype;
  v_slug text;
  v_problem text;
begin
  uid := auth.uid();
  if uid is null then return ''no_session''; end if;
  if p_candidate is null then return ''blocked:no_candidate''; end if;

  select * into c from candidates where id = p_candidate for update;
  if c.id is null then return ''blocked:no_such_candidate''; end if;
  select * into a from agents where id = c.agent_id for update;
  if a.id is null or a.user_id is distinct from uid then return ''blocked:not_owned''; end if;
  if a.state = ''archived'' then return ''blocked:state_archived''; end if;
  if c.state = ''published'' then return ''published:'' || c.slug; end if;
  if c.state <> ''draft'' then return ''blocked:state_'' || c.state; end if;

  v_problem := candidate_page_problem(coalesce(p_page, c.page));
  if v_problem is not null then return ''blocked:'' || v_problem; end if;

  v_slug := lpad(a.agent_no::text, 3, ''0'');

  -- the previous public page steps aside; its row keeps its page
  update candidates set state = ''unpublished''
    where agent_id = c.agent_id and state = ''published'' and id <> c.id;

  update candidates
     set page = coalesce(p_page, c.page),
         state = ''published'',
         slug = v_slug,
         approved_at = now(),
         published_at = now()
   where id = c.id;

  return ''published:'' || v_slug;
end';
revoke execute on function publish_candidate(uuid, jsonb) from public, anon;
grant execute on function publish_candidate(uuid, jsonb) to authenticated;

-- ---- unpublish_candidate: the person takes the page down ---------------------
create or replace function unpublish_candidate() returns text
language plpgsql security definer set search_path = public as '
declare uid uuid; n int;
begin
  uid := auth.uid();
  if uid is null then return ''no_session''; end if;
  update candidates c set state = ''unpublished''
    from agents a
   where a.id = c.agent_id and a.user_id = uid and c.state = ''published'';
  get diagnostics n = row_count;
  return case when n > 0 then ''unpublished'' else ''nothing_published'' end;
end';
revoke execute on function unpublish_candidate() from public, anon;
grant execute on function unpublish_candidate() to authenticated;

-- ---- candidate_public: the only public read ---------------------------------
create or replace function candidate_public(p_slug text) returns jsonb
language sql stable security definer set search_path = public as '
  select jsonb_build_object(
           ''serial'', c.slug,
           ''published_at'', c.published_at,
           ''page'', c.page)
    from candidates c
   where c.state = ''published'' and c.slug = p_slug
   limit 1
';
revoke execute on function candidate_public(text) from public;
grant execute on function candidate_public(text) to anon, authenticated;

-- ---- next_candidate_version: helper for drafts --------------------------------
create or replace function next_candidate_version(p_agent uuid) returns int
language sql stable security definer set search_path = public as '
  select coalesce(max(version), 0) + 1 from candidates where agent_id = p_agent
';
revoke execute on function next_candidate_version(uuid) from public, anon;
grant execute on function next_candidate_version(uuid) to authenticated;

-- ---- the portrait: one object per person, public read ------------------------
do '
declare b record;
begin
  select * into b from storage.buckets where id = ''portraits'';
  if found then
    if not b.public
       or b.file_size_limit is distinct from 5242880
       or b.allowed_mime_types is distinct from array[''image/jpeg'']::text[] then
      raise exception ''portraits_bucket_misconfigured'';
    end if;
  end if;
end';
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('portraits', 'portraits', true, 5242880, array['image/jpeg'])
on conflict (id) do nothing;

drop policy if exists portraits_owner_write on storage.objects;
create policy portraits_owner_write on storage.objects
  for insert with check (
    bucket_id = 'portraits'
    and (storage.foldername(name))[1] = auth.uid()::text
    and array_length(storage.foldername(name), 1) = 1
    and storage.filename(name) = 'portrait.jpg');
drop policy if exists portraits_owner_replace on storage.objects;
create policy portraits_owner_replace on storage.objects
  for update
  using (bucket_id = 'portraits' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'portraits' and (storage.foldername(name))[1] = auth.uid()::text
    and storage.filename(name) = 'portrait.jpg');
drop policy if exists portraits_public_read on storage.objects;
create policy portraits_public_read on storage.objects
  for select using (bucket_id = 'portraits');
