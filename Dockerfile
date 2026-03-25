FROM alpine:edge

LABEL maintainer="custom-zimaboard"
LABEL description="aMule for ZimaOS - Dashboard, Search, Auto-Organize"

WORKDIR /home/amule

# Install base runtime tools from the default edge repos first.
# Then install aMule explicitly from edge/testing with direct repository flags.
# This is more robust than rewriting /etc/apk/repositories and avoids the
# stable/edge mixing problem from the original Dockerfile.
RUN set -eux; \
    apk add --no-cache \
        ca-certificates \
        curl \
        inotify-tools \
        jq \
        pwgen \
        python3 \
        tzdata \
        unzip \
        wget; \
    update-ca-certificates; \
    apk add --no-cache \
        --repository=http://dl-cdn.alpinelinux.org/alpine/edge/main \
        --repository=http://dl-cdn.alpinelinux.org/alpine/edge/community \
        --repository=http://dl-cdn.alpinelinux.org/alpine/edge/testing \
        amule

# Install AmuleWebUI-Reloaded
RUN set -eux; \
    AMULEWEBUI_RELOADED_COMMIT=704ae1c861561513c010353320bb1ca9f0f2b9fe; \
    cd /usr/share/amule/webserver; \
    wget -O AmuleWebUI-Reloaded.zip "https://github.com/MatteoRagni/AmuleWebUI-Reloaded/archive/${AMULEWEBUI_RELOADED_COMMIT}.zip"; \
    unzip -q AmuleWebUI-Reloaded.zip; \
    mv AmuleWebUI-Reloaded-* AmuleWebUI-Reloaded; \
    rm -rf AmuleWebUI-Reloaded.zip AmuleWebUI-Reloaded/doc-images AmuleWebUI-Reloaded/README.md

# Copy application files
COPY dashboard/ /opt/dashboard/
COPY scripts/ /opt/scripts/
RUN chmod +x /opt/scripts/*.sh

COPY entrypoint.sh /home/amule/entrypoint.sh
COPY scripts/healthcheck.sh /home/amule/healthcheck.sh
RUN chmod +x /home/amule/entrypoint.sh /home/amule/healthcheck.sh

# aMule ports
EXPOSE 4711/tcp 4712/tcp 4662/tcp 4665/udp 4672/udp
# Dashboard
EXPOSE 8078/tcp

HEALTHCHECK --interval=120s --timeout=15s --start-period=60s --retries=3 \
    CMD /home/amule/healthcheck.sh

ENTRYPOINT ["/home/amule/entrypoint.sh"]
