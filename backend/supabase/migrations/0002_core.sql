-- 0002_core.sql
--
-- Extends the two legacy tables into the managed-server model without dropping
-- anything, so a project that already ran 0001 keeps its rows.
--
-- The ownership invariant: every table that hangs off `auth.users` carries
-- `owner_id` and every policy compares `(select auth.uid()) = owner_id`. The
-- agent identity (`agents.id`) is never sufficient on its own -- it is always
-- paired with an owner check in the RPCs that the Edge Function calls.

-- ---------------------------------------------------------------------------
-- profiles: a display name per account. Kept deliberately thin; Supabase Auth
-- owns the credentials and we never duplicate a password or an email secret.
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;
create policy "profiles are self readable" on public.profiles
  for select to authenticated using ((select auth.uid()) = id);
create policy "profiles are self writable" on public.profiles
  for all to authenticated using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);

-- ---------------------------------------------------------------------------
-- agents: one row per user's computer.
-- ---------------------------------------------------------------------------
alter table public.agents
  -- 'macos' arrives with a future desktop client; 'android' is reserved so the
  -- check constraint never has to be loosened to add it.
  drop constraint if exists agents_platform_check;

alter table public.agents
  add column if not exists token_prefix text,
  add column if not exists agent_version text,
  add column if not exists platform text default 'linux',
  add column if not exists capabilities jsonb not null default '{}'::jsonb,
  add column if not exists last_error text,
  add column if not exists revoked_at timestamptz,
  add column if not exists updated_at timestamptz not null default now();

update public.agents
   set token_prefix = 'legacy'
 where token_prefix is null;

alter table public.agents
  alter column token_prefix set default 'legacy',
  alter column platform drop not null,
  add constraint agents_platform_check
    check (platform is null or platform in ('linux', 'windows', 'macos', 'android'));

-- The token itself is never stored. `token_hash` is the SHA-256 of the token
-- the agent holds; `token_prefix` is a short non-secret fragment so a user can
-- tell two agents apart without either token being readable.
create index if not exists agents_owner_idx on public.agents (owner_id);
create unique index if not exists agents_token_hash_key on public.agents (token_hash);

-- ---------------------------------------------------------------------------
-- servers: one Minecraft server managed by one agent.
--
-- `status` is never written by the web panel. Only the agent writes it, via the
-- Edge Function. The panel reads it and nothing else, which is what keeps
-- "offline" honest: if the agent stops reporting, `status` simply goes stale and
-- the UI says unavailable rather than inventing an online state.
-- ---------------------------------------------------------------------------
alter table public.servers
  add column if not exists java_version integer not null default 21,
  add column if not exists min_memory text not null default '2G',
  add column if not exists max_memory text not null default '4G',
  add column if not exists port integer not null default 25565,
  add column if not exists crossplay boolean not null default true,
  add column if not exists status text not null default 'unavailable',
  add column if not exists runtime jsonb not null default '{}'::jsonb,
  add column if not exists last_seen_at timestamptz,
  add column if not exists updated_at timestamptz not null default now();

alter table public.servers
  add constraint servers_java_version_check check (java_version between 8 and 99),
  add constraint servers_port_check check (port between 1024 and 65535),
  add constraint servers_status_check check (
    status in ('online', 'offline', 'starting', 'stopping', 'unavailable', 'unknown', 'error')
  ),
  -- Memory is passed straight to -Xms/-Xmx, so constrain the format rather than
  -- trusting the browser to send something the JVM will accept.
  add constraint servers_memory_check check (
    min_memory ~ '^[0-9]{1,4}[MG]$' and max_memory ~ '^[0-9]{1,4}[MG]$'
  );

create index if not exists servers_owner_idx on public.servers (owner_id);
create index if not exists servers_agent_idx on public.servers (agent_id);

-- ---------------------------------------------------------------------------
-- managed settings
--
-- `server_properties` is a small allowlist of server.properties keys the panel
-- may change. Keeping it in the database (rather than a JSON blob per server)
-- means the agent can be handed exactly the keys it is allowed to apply, and
-- `apply` is regenerated server side -- an unknown key never reaches the agent.
-- ---------------------------------------------------------------------------
create table if not exists public.server_settings (
  key text primary key,
  label text not null,
  description text not null default '',
  default_value text,
  value_type text not null check (value_type in ('boolean', 'integer', 'string', 'enum')),
  choices text[],
  apply text not null,
  min_value integer,
  max_value integer,
  category text not null default 'general',
  restart_required boolean not null default false,
  sort_order integer not null default 0,
  updated_at timestamptz not null default now()
);

insert into public.server_settings
  (key, label, description, default_value, value_type, choices, apply, min_value, max_value, category, restart_required, sort_order)
values
  ('motd', 'MOTD', 'Shown in the server list.', 'A Minecraft Server', 'string', null, 'server.properties', null, null, 'general', true, 10),
  ('max-players', 'Max players', 'Server list slot count.', '20', 'integer', null, 'server.properties', 1, 500, 'general', true, 20),
  ('difficulty', 'Difficulty', 'Survival difficulty.', 'normal', 'enum', array['peaceful','easy','normal','hard'], 'server.properties', null, null, 'gameplay', true, 30),
  ('gamemode', 'Default gamemode', 'Mode new players join in.', 'survival', 'enum', array['survival','creative','adventure','spectator'], 'server.properties', null, null, 'gameplay', true, 40),
  ('view-distance', 'View distance', 'Chunks sent to each client.', '10', 'integer', null, 'server.properties', 2, 32, 'performance', true, 50),
  ('simulation-distance', 'Simulation distance', 'Chunks ticked per player.', '10', 'integer', null, 'server.properties', 2, 32, 'performance', true, 60),
  ('online-mode', 'Online mode', 'Mojang authentication. Required for one Bedrock inventory across Java and Bedrock.', 'true', 'boolean', null, 'server.properties', null, null, 'security', true, 70),
  ('white-list', 'Whitelist', 'Only listed players may join.', 'false', 'boolean', null, 'server.properties', null, null, 'security', true, 80),
  ('allow-flight', 'Allow flight', 'Lets players fly in survival.', 'false', 'boolean', null, 'server.properties', null, null, 'gameplay', true, 90),
  ('pvp', 'PvP', 'Player versus player damage.', 'true', 'boolean', null, 'server.properties', null, null, 'gameplay', 100),
  ('spawn-protection', 'Spawn protection', 'Radius in blocks protected at spawn.', '16', 'integer', null, 'server.properties', 0, 128, 'gameplay', true, 110),
  ('enable-status', 'Status ping', 'Answer server list pings.', 'true', 'boolean', null, 'server.properties', null, null, 'general', true, 120),
  ('sync-chunk-writes', 'Sync chunk writes', 'Flush chunks to disk immediately. Safer, slower.', 'false', 'boolean', null, 'server.properties', null, null, 'performance', true, 130),
  ('allow-nether', 'Nether', 'Generate the nether dimension.', 'true', 'boolean', null, 'server.properties', null, null, 'dimensions', true, 140),
  ('allow-end', 'End', 'Generate the end dimension.', 'true', 'boolean', null, 'server.properties', null, null, 'dimensions', true, 150),
  ('level-name', 'World name', 'Folder name of the main world.', 'world', 'string', null, 'server.properties', null, null, 'dimensions', true, 160),
  ('enable-rcon', 'RCON', 'Remote console. Only the local agent uses it; keep it on loopback.', 'false', 'boolean', null, 'server.properties', null, null, 'security', true, 170)
on conflict (key) do update set
  label = excluded.label,
  description = excluded.description,
  default_value = excluded.default_value,
  value_type = excluded.value_type,
  choices = excluded.choices,
  apply = excluded.apply,
  min_value = excluded.min_value,
  max_value = excluded.max_value,
  category = excluded.category,
  restart_required = excluded.restart_required,
  sort_order = excluded.sort_order;

alter table public.server_settings enable row level security;
create policy "settings are readable by signed in users" on public.server_settings
  for select to authenticated using (true);
-- Deliberately no INSERT/UPDATE/DELETE policy: settings are changed by a
-- migration or by an admin, never by a user request.

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists agents_touch on public.agents;
create trigger agents_touch before update on public.agents
  for each row execute function public.touch_updated_at();

drop trigger if exists servers_touch on public.servers;
create trigger servers_touch before update on public.servers
  for each row execute function public.touch_updated_at();

drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at();

drop trigger if exists server_settings_touch on public.server_settings;
create trigger server_settings_touch before update on public.server_settings
  for each row execute function public.touch_updated_at();