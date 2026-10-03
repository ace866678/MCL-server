-- 0001_legacy_schema.sql
--
-- The original two-table schema from the single-VM project, kept verbatim as a
-- marker so `supabase db push` on an already-provisioned project reproduces the
-- history rather than silently diverging from it. Every column here is
-- preserved (and ALTERed, never dropped) by 0002 and 0003.
--
-- Nothing in the application reads this file directly; see 0002_core.sql.

create table if not exists public.agents (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  platform text not null check (platform in ('windows', 'linux')),
  token_hash text not null,
  last_seen_at timestamptz,
  status jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.servers (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  name text not null,
  minecraft_version text not null,
  server_software text not null default 'paper',
  config jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.agents enable row level security;
alter table public.servers enable row level security;
create policy "owners manage agents" on public.agents for all to authenticated using ((select auth.uid()) = owner_id) with check ((select auth.uid()) = owner_id);
create policy "owners manage servers" on public.servers for all to authenticated using ((select auth.uid()) = owner_id) with check ((select auth.uid()) = owner_id);