-- 0006_agent_server_discovery.sql
--
-- An agent has to know which servers it is responsible for before it can report
-- anything about them. Without this, a freshly started agent learns a server's
-- id only when the first command is queued for it -- so a server that is simply
-- stopped would never report `offline`, and the panel would show nothing at all
-- until someone clicked a button. This function closes that gap.
--
-- It returns only what an agent needs to find the directory and report status:
-- the id, the port to bind, and whether the server should exist locally. No
-- owner identity, no token, no other server's data.
--
-- `p_agent_id` is checked against the caller's own identity by the Edge Function
-- before it gets here, and this function is granted to service_role only, so it
-- is reachable solely by a function that has already authenticated an agent.

create or replace function public.agent_list_servers(p_agent_id uuid)
returns table (
  server_id uuid,
  name text,
  port integer,
  status text
)
language sql
stable
security definer
set search_path = ''
as $$
  select s.id, s.name, coalesce(s.port, 25565), s.status
    from public.servers s
   where s.agent_id = p_agent_id
   order by s.created_at;
$$;

revoke all on function public.agent_list_servers(uuid) from public, anon, authenticated;
grant execute on function public.agent_list_servers(uuid) to service_role;