-- 0005_rls.sql
--
-- Policies that depend on the columns added in 0002 and 0003, plus the two
-- things the earlier "owners manage" policies cannot express.
--
-- Two design points worth stating, because they are the whole security model:

--  1. Every table that reaches a Minecraft machine carries `owner_id`, and every
--     policy resolves it through `(select auth.uid())`. RLS is the only thing
--     between "signed in" and "controls a server". The Edge Function's
--     SECURITY DEFINER functions bypass RLS, so they re-check identity inside --
--     see 0004.

--  2. The panel may UPDATE a server but may not DELETE one, and may not UPDATE
--     `agents.token_hash`. Both are deliberate:
--       * deleting a server record is a two-step (delete, then confirm) server
--         action so a stray click cannot orphan a world on someone's disk;
--       * a token can only be minted by the action that also returns it once.

-- ---------------------------------------------------------------------------
-- agents
-- ---------------------------------------------------------------------------
drop policy if exists "owners manage agents" on public.agents;

create policy "owners read agents" on public.agents
  for select to authenticated using ((select auth.uid()) = owner_id);

-- There is deliberately no INSERT/UPDATE/DELETE policy for agents. The column
-- GRANT in 0004 is SELECT only, so the browser cannot mint, rotate or revoke a
-- token even if it wanted to; those three operations are the panel's server
-- actions, which hold the service_role key and apply their own ownership check
-- before touching the row. Keeping the browser out of the credential lifecycle
-- entirely is simpler to reason about than layering policies over it.

-- ---------------------------------------------------------------------------
-- servers
-- ---------------------------------------------------------------------------
drop policy if exists "owners manage servers" on public.servers;

create policy "owners read servers" on public.servers
  for select to authenticated using ((select auth.uid()) = owner_id);

create policy "owners create servers" on public.servers
  for insert to authenticated with check (
    (select auth.uid()) = owner_id
    -- A server cannot be attached to somebody else's agent, even with a valid
    -- session. Without this, ownership of the machine would be borrowable.
    and exists (
      select 1
        from public.agents a
       where a.id = public.servers.agent_id
         and a.owner_id = (select auth.uid())
         and a.revoked_at is null
    )
  );

create policy "owners update servers" on public.servers
  for update to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

-- There is no DELETE policy for servers: removal is a two-step server action
-- that also asks the agent to drop the server directory. A single stray click
-- should not orphan a world on someone's disk.

-- `status`, `runtime` and `last_seen_at` are written by the agent through the
-- Edge Function. Block the browser from forging them.
--
-- The guard is keyed on the JWT role rather than on table ownership, because
-- both the Edge Function and the panel's server actions reach these tables with
-- the service_role key: the agent's status legitimately has to be written from
-- there, and minting or rotating an agent token has to bypass RLS so it can be
-- audited in one place. A request signed in as a user (`authenticated`) can
-- never write these columns.
create or replace function public.writable_by_backend()
returns boolean
language sql
stable
set search_path = ''
as $$
  select coalesce(current_setting('request.jwt.claim.role', true), '') = 'service_role';
$$;

create or replace function public.reject_agent_owned_columns()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if public.writable_by_backend() then
    return new;
  end if;

  if new.status is distinct from old.status
     or new.runtime is distinct from old.runtime
     or new.last_seen_at is distinct from old.last_seen_at then
    raise exception 'status, runtime and last_seen_at are written by the agent'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

drop trigger if exists servers_agent_owned_columns on public.servers;
create trigger servers_agent_owned_columns before update on public.servers
  for each row execute function public.reject_agent_owned_columns();

-- Same guard for agents: the browser may rename a device but cannot mark it as
-- having just checked in, which is how "online" would become forgeable.
create or replace function public.reject_agent_status_columns()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if public.writable_by_backend() then
    return new;
  end if;

  if new.status is distinct from old.status
     or new.last_seen_at is distinct from old.last_seen_at
     or new.last_error is distinct from old.last_error
     or new.capabilities is distinct from old.capabilities
     or new.token_hash is distinct from old.token_hash
     or new.revoked_at is distinct from old.revoked_at then
    raise exception 'agent status and credentials are written by the backend'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

drop trigger if exists agents_agent_owned_columns on public.agents;
create trigger agents_agent_owned_columns before update on public.agents
  for each row execute function public.reject_agent_status_columns();

-- ---------------------------------------------------------------------------
-- profiles: auto-create on first sign-in so the panel can show a name without a
-- separate onboarding step.
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', split_part(new.email, '@', 1)))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- Retention
--
-- agent_events and rate buckets grow on their own. Trimming them in the same
-- function the Edge Function already calls means there is no cron dependency.
-- ---------------------------------------------------------------------------
create or replace function public.prune_transient_data()
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from public.agent_events where created_at < now() - interval '14 days';
  delete from public.agent_rate_buckets where window_start < now() - interval '1 hour';
  delete from public.server_commands
   where finished_at is not null
     and finished_at < now() - interval '14 days';
end;
$$;

revoke all on function public.prune_transient_data() from public, anon, authenticated;
grant execute on function public.prune_transient_data() to service_role;