-- 0004_agent_rpc.sql
--
-- Every function here is SECURITY DEFINER, which means RLS does *not* apply
-- inside them. That is why each one re-checks ownership or agent identity
-- explicitly. They are revoked from anon/authenticated and granted only to
-- service_role, which is the Edge Function's key -- the browser never calls
-- them, so a stolen anon key cannot drive an agent.
--
-- `search_path = ''` plus fully-qualified names is the standard hardening for
-- SECURITY DEFINER: without it, a caller could shadow a function or table name
-- by creating one earlier in the search path.

revoke all on function public.touch_updated_at() from public;

-- ---------------------------------------------------------------------------
-- Agent authentication
--
-- The panel generates a 32-byte token and stores only its SHA-256. The agent
-- sends the token; the Edge Function hashes it with WebCrypto and passes the
-- resulting hex here to be compared against the stored hash.
--
-- Comparing digests rather than tokens means a database dump alone does not let
-- anyone impersonate an agent, which is the property that matters for a
-- credential that grants control of someone's machine.
-- ---------------------------------------------------------------------------
create or replace function public.agent_authenticate(p_agent_id uuid, p_token_hash text)
returns table (
  agent_id uuid,
  owner_id uuid,
  name text,
  platform text,
  agent_version text,
  capabilities jsonb,
  status jsonb,
  last_seen_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select a.id, a.owner_id, a.name, a.platform, a.agent_version,
         a.capabilities, a.status, a.last_seen_at
    from public.agents a
   where a.id = p_agent_id
     and a.token_hash = p_token_hash
     and a.revoked_at is null;
$$;

-- ---------------------------------------------------------------------------
-- Replay defence
--
-- The primary key does the work: an INSERT that collides aborts, so a nonce can
-- only ever be spent once. Combined with the timestamp window checked in the
-- Edge Function, a captured request is unusable: immediately because the nonce
-- is already spent, and after five minutes because the timestamp is too old.
-- ---------------------------------------------------------------------------
create or replace function public.agent_spend_nonce(
  p_agent_id uuid,
  p_nonce text,
  p_max_age_seconds integer
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_nonce !~ '^[0-9a-f]{32}$' then
    return false;
  end if;

  -- Opportunistic prune. Keeping this inline removes the need for a cron to
  -- stop the table growing, and the write path already touches it on every
  -- request.
  delete from public.agent_request_nonces
   where seen_at < now() - make_interval(secs => p_max_age_seconds * 3);

  insert into public.agent_request_nonces (agent_id, nonce)
  values (p_agent_id, p_nonce)
  on conflict do nothing;

  return not found;
end;
$$;

-- ---------------------------------------------------------------------------
-- Rate limiting
-- ---------------------------------------------------------------------------
create or replace function public.consume_agent_rate(
  p_agent_id uuid,
  p_limit integer,
  p_window_seconds integer
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_window timestamptz := to_timestamp(floor(extract(epoch from now()) / p_window_seconds) * p_window_seconds);
  v_count integer;
begin
  insert into public.agent_rate_buckets as b (agent_id, window_start, requests)
  values (p_agent_id, v_window, 1)
  on conflict (agent_id, window_start)
    do update set requests = b.requests + 1
  returning requests into v_count;

  -- Rows older than the window can never be read again; drop them lazily rather
  -- than scheduling a job nobody is running.
  delete from public.agent_rate_buckets
   where agent_id = p_agent_id
     and window_start < v_window;

  return v_count <= p_limit;
end;
$$;

-- ---------------------------------------------------------------------------
-- Heartbeat and status
-- ---------------------------------------------------------------------------
create or replace function public.agent_heartbeat(
  p_agent_id uuid,
  p_status jsonb,
  p_agent_version text,
  p_capabilities jsonb
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.agents
     set last_seen_at = now(),
         status = coalesce(p_status, '{}'::jsonb),
         agent_version = nullif(p_agent_version, ''),
         capabilities = coalesce(p_capabilities, '{}'::jsonb),
         last_error = null
   where id = p_agent_id
     and revoked_at is null;

  if not found then
    raise exception 'unknown or revoked agent %', p_agent_id using errcode = 'no_data_found';
  end if;
end;
$$;

-- A server's status is written by its agent and only by its agent.
create or replace function public.agent_report_server_status(
  p_server_id uuid,
  p_agent_id uuid,
  p_status text,
  p_runtime jsonb
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.servers s
     set status = p_status,
         runtime = coalesce(p_runtime, '{}'::jsonb),
         last_seen_at = now()
   where s.id = p_server_id
     and s.agent_id = p_agent_id;

  if not found then
    raise exception 'server % does not belong to agent %', p_server_id, p_agent_id
      using errcode = 'insufficient_privilege';
  end if;
end;
$$;

create or replace function public.agent_record_backup(
  p_server_id uuid,
  p_agent_id uuid,
  p_filename text,
  p_size_bytes bigint,
  p_sha256 text,
  p_status text default 'ready'
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_backup_id uuid;
begin
  insert into public.server_backups
    (server_id, agent_id, owner_id, filename, size_bytes, sha256, status)
  select s.id, p_agent_id, s.owner_id, p_filename, p_size_bytes, p_sha256, p_status
    from public.servers s
   where s.id = p_server_id
     and s.agent_id = p_agent_id
  on conflict (server_id, filename)
    do update set size_bytes = excluded.size_bytes,
                  sha256 = excluded.sha256,
                  status = excluded.status
  returning id into v_backup_id;

  if v_backup_id is null then
    raise exception 'server % does not belong to agent %', p_server_id, p_agent_id
      using errcode = 'insufficient_privilege';
  end if;

  return v_backup_id;
end;
$$;

create or replace function public.agent_log_event(
  p_agent_id uuid,
  p_server_id uuid,
  p_kind text,
  p_level text,
  p_message text
)
returns void
language sql
security definer
set search_path = ''
as $$
  insert into public.agent_events (agent_id, server_id, kind, level, message)
  values (p_agent_id, p_server_id, p_kind, coalesce(p_level, 'info'), left(p_message, 2000));
$$;

-- ---------------------------------------------------------------------------
-- Command queue
-- ---------------------------------------------------------------------------

-- Called by the web panel. SECURITY DEFINER, so the ownership check here is the
-- only thing standing between a signed-in user and another user's server.
create or replace function public.enqueue_command(
  p_server_id uuid,
  p_command text,
  p_args jsonb default '{}'::jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := auth.uid();
  v_owner uuid;
  v_agent uuid;
  v_pending integer;
  v_id uuid;
begin
  if v_uid is null then
    raise exception 'not signed in' using errcode = 'insufficient_privilege';
  end if;

  select s.owner_id, s.agent_id into v_owner, v_agent
    from public.servers s
   where s.id = p_server_id;

  if v_owner is null or v_owner <> v_uid then
    raise exception 'server % not found' using p_server_id using errcode = 'no_data_found';
  end if;

  if not exists (
    select 1 from public.agents a
     where a.id = v_agent
       and a.owner_id = v_uid
       and a.revoked_at is null
  ) then
    raise exception 'agent is not available' using errcode = 'insufficient_privilege';
  end if;

  -- An offline agent must not accumulate a backlog that all fires at once when
  -- the user's laptop rejoins the network. One in-flight command per server is
  -- enough to drive the whole state machine.
  select count(*) into v_pending
    from public.server_commands
   where server_id = p_server_id
     and status in ('queued', 'running');

  if v_pending >= 1 then
    raise exception 'a command is already pending for this server'
      using errcode = 'too_many_requests';
  end;

  insert into public.server_commands (server_id, agent_id, owner_id, command, args)
  values (p_server_id, v_agent, v_uid, p_command, coalesce(p_args, '{}'::jsonb))
  returning id into v_id;

  insert into public.agent_events (agent_id, server_id, kind, level, message)
  values (v_agent, p_server_id, 'command.queued', 'info', p_command);

  return v_id;
end;
$$;

-- Called by the agent. `for update skip locked` plus the conditional update is
-- what stops two concurrent polls (a restarted agent, an overlapping request)
-- from running the same command twice.
create or replace function public.agent_claim_commands(p_agent_id uuid, p_limit integer default 5)
returns setof public.server_commands
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_claimed uuid[] := '{}';
begin
  select coalesce(array_agg(c.id), '{}')
    into v_claimed
    from (
      select c0.id
        from public.server_commands c0
       where c0.agent_id = p_agent_id
         and c0.status = 'queued'
         and c0.created_at > now() - interval '15 minutes'
       order by c0.created_at
       limit greatest(1, least(coalesce(p_limit, 5), 20))
         for update skip locked
    ) c;

  if array_length(v_claimed, 1) is null then
    return;
  end if;

  update public.server_commands
     set status = 'running',
         dispatched_at = now()
   where id = any(v_claimed)
     and agent_id = p_agent_id
     and status = 'queued';

  return query
    select * from public.server_commands where id = any(v_claimed) and status = 'running';
end;
$$;

create or replace function public.agent_complete_command(
  p_command_id uuid,
  p_agent_id uuid,
  p_status text,
  p_result jsonb default '{}'::jsonb,
  p_error text default null,
  p_exit_code integer default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.server_commands
     set status = p_status,
         result = coalesce(p_result, '{}'::jsonb),
         error = left(p_error, 2000),
         exit_code = p_exit_code,
         finished_at = now()
   where id = p_command_id
     and agent_id = p_agent_id
     and status = 'running';

  if not found then
    raise exception 'command % is not running for agent %', p_command_id, p_agent_id
      using errcode = 'insufficient_privilege';
  end if;
end;
$$;

-- Anything still queued when the window closes is abandoned rather than run
-- later against a server whose state has moved on.
create or replace function public.expire_stale_commands()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  update public.server_commands
     set status = 'expired',
         finished_at = now(),
         error = 'agent did not pick this up in time'
   where status = 'queued'
     and created_at < now() - interval '15 minutes';

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

-- ---------------------------------------------------------------------------
-- Grants
--
-- Nothing above is reachable with a browser-visible key. The Edge Function runs
-- as service_role; the panel reads tables directly under RLS and calls only
-- enqueue_command.
--
-- Agent rows are read-only for the browser on purpose. Registration, renaming,
-- token rotation and removal all go through the panel's server actions, which
-- hold SUPABASE_SERVICE_ROLE_KEY, so the entire credential lifecycle lives in
-- one audited place instead of being split between SQL grants and React.
-- ---------------------------------------------------------------------------
revoke all on function public.agent_authenticate(uuid, text) from public, anon, authenticated;
revoke all on function public.agent_spend_nonce(uuid, text, integer) from public, anon, authenticated;
revoke all on function public.consume_agent_rate(uuid, integer, integer) from public, anon, authenticated;
revoke all on function public.agent_heartbeat(uuid, jsonb, text, jsonb) from public, anon, authenticated;
revoke all on function public.agent_report_server_status(uuid, uuid, text, jsonb) from public, anon, authenticated;
revoke all on function public.agent_record_backup(uuid, uuid, text, bigint, text, text) from public, anon, authenticated;
revoke all on function public.agent_log_event(uuid, uuid, text, text, text) from public, anon, authenticated;
revoke all on function public.agent_claim_commands(uuid, integer) from public, anon, authenticated;
revoke all on function public.agent_complete_command(uuid, uuid, text, jsonb, text, integer) from public, anon, authenticated;
revoke all on function public.expire_stale_commands() from public, anon, authenticated;

grant execute on function public.enqueue_command(uuid, text, jsonb) to authenticated;

grant execute on function public.agent_authenticate(uuid, text) to service_role;
grant execute on function public.agent_spend_nonce(uuid, text, integer) to service_role;
grant execute on function public.consume_agent_rate(uuid, integer, integer) to service_role;
grant execute on function public.agent_heartbeat(uuid, jsonb, text, jsonb) to service_role;
grant execute on function public.agent_report_server_status(uuid, uuid, text, jsonb) to service_role;
grant execute on function public.agent_record_backup(uuid, uuid, text, bigint, text, text) to service_role;
grant execute on function public.agent_log_event(uuid, uuid, text, text, text) to service_role;
grant execute on function public.agent_claim_commands(uuid, integer) to service_role;
grant execute on function public.agent_complete_command(uuid, uuid, text, jsonb, text, integer) to service_role;
grant execute on function public.expire_stale_commands() to service_role;

grant usage on schema public to anon, authenticated, service_role;

-- Read-only where the browser genuinely only reads.
grant select on public.agents to authenticated;
grant select on public.server_settings to authenticated;
grant select on public.server_backups to authenticated;
grant select on public.agent_events to authenticated;

-- Writable, but scoped by RLS and by the column guards in 0005.
grant select, insert, update on public.servers to authenticated;
grant select, insert on public.server_commands to authenticated;
grant select, insert, update on public.profiles to authenticated;

-- service_role already bypasses RLS and holds full privileges by default in a
-- Supabase project; listed here so the intent is explicit and reviewable.