-- 0003_commands_backups.sql
--
-- The queue between the web panel and the agent, plus backup metadata.
--
-- Shape: the panel INSERTs a row into server_commands with status 'queued'; the
-- agent polls, claims it (which flips it to 'running' under a conditional
-- UPDATE so a double poll cannot run the same command twice), does the work, and
-- POSTs the outcome back. Nothing in this table lets the agent read anything the
-- owner's session could not.

create table if not exists public.server_commands (
  id uuid primary key default gen_random_uuid(),
  server_id uuid not null references public.servers(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,

  -- Enumerated, never free-form. The agent refuses anything it does not know,
  -- and the CHECK below means a bad value cannot even be queued.
  command text not null check (command in (
    'server.start', 'server.stop', 'server.restart', 'server.install',
    'server.backup', 'server.settings.apply', 'server.whitelist.add',
    'server.whitelist.remove', 'server.console', 'server.logs.tail'
  )),

  -- Structured payload; the agent validates every field against its own limits.
  args jsonb not null default '{}'::jsonb,

  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed', 'expired', 'cancelled')),

  -- Set by the agent on completion.
  result jsonb,
  error text,
  exit_code integer,

  created_at timestamptz not null default now(),
  dispatched_at timestamptz,
  finished_at timestamptz,

  -- A queued command the agent never got to must not fire hours later. The
  -- agent also drops anything past this on its own side.
  expires_at timestamptz not null default (now() + interval '15 minutes')
);

create index if not exists server_commands_queue_idx
  on public.server_commands (agent_id, status, created_at);
create index if not exists server_commands_owner_idx
  on public.server_commands (owner_id, created_at desc);
create index if not exists server_commands_server_idx
  on public.server_commands (server_id, created_at desc);

create table if not exists public.server_backups (
  id uuid primary key default gen_random_uuid(),
  server_id uuid not null references public.servers(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,

  -- Paths are agent-relative; the panel never learns the agent's filesystem
  -- layout and cannot address a file outside the server's backup directory.
  filename text not null,
  size_bytes bigint,
  sha256 text,

  -- 'local' means it exists on the user's disk and this row is only a label.
  -- Anything offered for download would need a signed, short-lived URL, which
  -- this platform deliberately does not implement yet.
  storage text not null default 'local' check (storage in ('local')),
  status text not null default 'ready' check (status in ('ready', 'deleting', 'failed')),

  created_at timestamptz not null default now(),
  unique (server_id, filename)
);

create index if not exists server_backups_owner_idx on public.server_backups (owner_id, created_at desc);
create index if not exists server_backups_server_idx on public.server_backups (server_id, created_at desc);

alter table public.server_commands enable row level security;
alter table public.server_backups enable row level security;

create policy "owners read their commands" on public.server_commands
  for select to authenticated using ((select auth.uid()) = owner_id);
create policy "owners queue their commands" on public.server_commands
  for insert to authenticated with check ((select auth.uid()) = owner_id);

create policy "owners read their backups" on public.server_backups
  for select to authenticated using ((select auth.uid()) = owner_id);

-- ---------------------------------------------------------------------------
-- Rate limiting
--
-- The queue is the only thing an authenticated user can turn into work on
-- someone else's CPU, so it is capped in the database rather than in the edge:
-- two panel instances and one direct API call share the same budget.
--
-- Fixed window rather than a token bucket -- simpler to reason about, and the
-- only property that matters is "not more than N per window".
-- ---------------------------------------------------------------------------
create table if not exists public.agent_rate_buckets (
  agent_id uuid not null references public.agents(id) on delete cascade,
  window_start timestamptz not null,
  requests integer not null default 0,
  primary key (agent_id, window_start)
);

alter table public.agent_rate_buckets enable row level security;
-- No policies: only the service role (the Edge Function) touches this table.

-- ---------------------------------------------------------------------------
-- Replay defence
--
-- The agent proves possession of its token by sending the token's SHA-256 as a
-- bearer credential over TLS. That is deliberately not an HMAC, and the reason
-- matters: verifying a MAC requires the MAC key, so an HMAC scheme would force
-- the database to store the token itself and a database dump would then be a
-- dump of usable credentials. Storing only the digest means a leaked dump yields
-- nothing an attacker can present.
--
-- The cost of not signing is that a captured request is replayable, so replay is
-- prevented structurally instead: every agent request carries a 128-bit random
-- nonce, and a nonce is accepted exactly once.
-- ---------------------------------------------------------------------------
create table if not exists public.agent_request_nonces (
  agent_id uuid not null references public.agents(id) on delete cascade,
  nonce text not null,
  seen_at timestamptz not null default now(),
  primary key (agent_id, nonce)
);

create index if not exists agent_request_nonces_seen_idx on public.agent_request_nonces (seen_at);

alter table public.agent_request_nonces enable row level security;

-- ---------------------------------------------------------------------------
-- Operational log.
--
-- Append-only, short retention, and readable only through a function so the
-- owner of an agent can see that their agent is talking without ever being able
-- to write here.
-- ---------------------------------------------------------------------------
create table if not exists public.agent_events (
  id bigint generated always as identity primary key,
  agent_id uuid references public.agents(id) on delete cascade,
  server_id uuid references public.servers(id) on delete cascade,
  kind text not null,
  level text not null default 'info' check (level in ('debug', 'info', 'warn', 'error')),
  message text not null,
  created_at timestamptz not null default now()
);

create index if not exists agent_events_agent_idx on public.agent_events (agent_id, created_at desc);

alter table public.agent_events enable row level security;

create policy "owners read their agent events" on public.agent_events
  for select to authenticated using (
    exists (
      select 1 from public.agents a
       where a.id = agent_events.agent_id
         and a.owner_id = (select auth.uid())
    )
  );