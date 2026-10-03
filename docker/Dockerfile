# Paper 26.1+ needs Java 25; the temurin image is the least surprising choice.
FROM eclipse-temurin:25-jre

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates zip unzip gosu \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --system --create-home --home-dir /home/minecraft \
            --shell /usr/sbin/nologin minecraft

WORKDIR /srv/minecraft
COPY --chown=minecraft:minecraft . /srv/minecraft
RUN chmod +x /srv/minecraft/mc /srv/minecraft/docker/entrypoint.sh \
 && mkdir -p /srv/minecraft/server /srv/minecraft/backups \
 && chown minecraft:minecraft /srv/minecraft/server /srv/minecraft/backups

# The entrypoint runs as root for exactly one step: Docker creates missing bind
# mount sources (./server, ./backups) owned by root, so it has to fix their
# ownership before dropping to `minecraft` via gosu for everything else.
USER root

ENV MC_USER=minecraft \
    MC_SERVER_DIR=/srv/minecraft/server \
    MC_BACKUP_DIR=/srv/minecraft/backups

# 25565/tcp for Java clients, 19132/udp for Bedrock clients via Geyser.
EXPOSE 25565/tcp 19132/udp

ENTRYPOINT ["/srv/minecraft/docker/entrypoint.sh"]