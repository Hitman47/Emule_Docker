FROM debian:bookworm-slim

LABEL maintainer="custom-zimaboard"
LABEL description="aMule for ZimaOS - Dashboard, Search, Auto-Organize"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        amule-daemon \
        amule-utils \
        ca-certificates \
        cron \
        curl \
        gosu \
        inotify-tools \
        jq \
        procps \
        pwgen \
        python3 \
        netcat-openbsd \
        tzdata \
        unzip \
        wget \
    && rm -rf /var/lib/apt/lists/*

RUN AMULEWEBUI_RELOADED_COMMIT=704ae1c861561513c010353320bb1ca9f0f2b9fe \
    && cd /usr/share/amule/webserver \
    && wget -O AmuleWebUI-Reloaded.zip "https://github.com/MatteoRagni/AmuleWebUI-Reloaded/archive/${AMULEWEBUI_RELOADED_COMMIT}.zip" \
    && unzip -q AmuleWebUI-Reloaded.zip \
    && mv AmuleWebUI-Reloaded-* AmuleWebUI-Reloaded \
    && rm -rf AmuleWebUI-Reloaded.zip AmuleWebUI-Reloaded/doc-images AmuleWebUI-Reloaded/README.md

COPY dashboard/ /opt/dashboard/
COPY scripts/ /opt/scripts/
RUN chmod +x /opt/scripts/*.sh

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY scripts/healthcheck.sh /usr/local/bin/healthcheck.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh \
    && mkdir -p /home/amule /downloads /temp /backups /var/log/amule-diag \
    && rm -rf /downloads/incoming /downloads/temp

WORKDIR /home/amule

# aMule ports
EXPOSE 4711/tcp 4712/tcp 4662/tcp 4665/udp 4672/udp
# Dashboard
EXPOSE 8078/tcp

HEALTHCHECK --interval=120s --timeout=15s --start-period=60s --retries=3 \
    CMD /usr/local/bin/healthcheck.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
