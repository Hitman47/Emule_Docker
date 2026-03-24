FROM alpine:edge

LABEL maintainer="custom-zimaboard"
LABEL description="aMule for ZimaOS - Dashboard, Search, Auto-Organize"

WORKDIR /home/amule

# Use edge main/community plus tagged edge/testing because aMule is packaged in testing.
RUN printf '%s\n' \
    'https://dl-cdn.alpinelinux.org/alpine/edge/main' \
    'https://dl-cdn.alpinelinux.org/alpine/edge/community' \
    '@testing https://dl-cdn.alpinelinux.org/alpine/edge/testing' \
    > /etc/apk/repositories \
    && apk add --no-cache \
        ca-certificates \
        curl \
        inotify-tools \
        jq \
        mandoc \
        pwgen \
        python3 \
        py3-pip \
        tzdata \
        unzip \
        wget \
        amule@testing \
    && update-ca-certificates

# Install AmuleWebUI-Reloaded
RUN AMULEWEBUI_RELOADED_COMMIT=704ae1c861561513c010353320bb1ca9f0f2b9fe && \
    cd /usr/share/amule/webserver && \
    wget -O AmuleWebUI-Reloaded.zip "https://github.com/MatteoRagni/AmuleWebUI-Reloaded/archive/${AMULEWEBUI_RELOADED_COMMIT}.zip" && \
    unzip -q AmuleWebUI-Reloaded.zip && \
    mv AmuleWebUI-Reloaded-* AmuleWebUI-Reloaded && \
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
