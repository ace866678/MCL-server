"""MCL Agent — the local half of the platform.

The agent runs on the machine that actually holds the Minecraft server. It makes
outbound HTTPS calls to the Supabase Edge Function and nothing else: it never
listens on a port, so hosting a server here needs no port forwarding and no
inbound firewall rule for management.

Layout:

    config        agent.json + env, and the per-server directory layout
    backend       signed HTTP client for the Edge Function
    server        one managed server: paths, and the only way `mc` is invoked
    rcon          Minecraft RCON, for player lists the console cannot give
    serverconfig  server.properties, read and edited in place
    logs          latest.log tailing with offset tracking
    backups       world backup + prune, delegating the zip to `mc backup`
    status        running state, players, CPU, RAM, disk
    commands      the allowlist; every command from the network lands here
    tunnel        pluggable public-address providers
    service       the poll loop, retry, and graceful shutdown
"""

__all__ = ["__version__"]

__version__ = "1.0.0"