FROM alpine:3.22.2 AS builder

WORKDIR /tmp

# Install aMule from edge/testing
RUN apk add --no-cache --repository=http://dl-cdn.alpinelinux.org/alpine/edge/testing amule amule-doc

# Install AmuleWebUI-Reloaded
RUN AMULEWEBUI_RELOADED_COMMIT=704ae1c861561513c010353320bb1ca9f0f2b9fe && \
    cd /usr/share/amule/webserver && \
    wget -O AmuleWebUI-Reloaded.zip https://github.com/MatteoRagni/AmuleWebUI-Reloaded/archive/${AMULEWEBUI_RELOADED_COMMIT}.zip && \
    unzip AmuleWebUI-Reloaded.zip && \
    mv AmuleWebUI-Reloaded-* AmuleWebUI-Reloaded && \
    rm -rf AmuleWebUI-Reloaded.zip AmuleWebUI-Reloaded/doc-images AmuleWebUI-Reloaded/README.md

# ─── Final image ───
FROM alpine:3.22.2

LABEL maintainer="custom-zimaboard"
LABEL description="aMule for ZimaOS - Dashboard, Search, Auto-Organize"

# Install runtime deps + Python for dashboard
RUN apk add --no-cache \
    libedit libgcc libintl libpng libstdc++ libupnp musl wxwidgets zlib \
    tzdata pwgen mandoc curl \
    python3 py3-pip py3-flask \
    inotify-tools jq \
    && apk add --no-cache --repository=http://dl-cdn.alpinelinux.org/alpine/edge/testing crypto++

# Copy aMule binaries from builder
COPY --from=builder /usr/bin/alcc /usr/bin/amulecmd /usr/bin/amuled /usr/bin/amuleweb /usr/bin/ed2k /usr/bin/
COPY --from=builder /usr/share/amule /usr/share/amule
COPY --from=builder /usr/share/man/man1/alcc.1.gz /usr/share/man/man1/amulecmd.1.gz \
    /usr/share/man/man1/amuled.1.gz /usr/share/man/man1/amuleweb.1.gz \
    /usr/share/man/man1/ed2k.1.gz /usr/share/man/man1/

# Verify binaries link correctly
RUN ldd /usr/bin/amuled && ldd /usr/bin/amulecmd && ldd /usr/bin/amuleweb

# Install Python dashboard deps
COPY dashboard/requirements.txt /opt/dashboard/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /opt/dashboard/requirements.txt

# Copy application files
COPY dashboard/ /opt/dashboard/
COPY scripts/ /opt/scripts/
RUN chmod +x /opt/scripts/*.sh

COPY entrypoint.sh /home/amule/entrypoint.sh
COPY healthcheck.sh /home/amule/healthcheck.sh
RUN chmod +x /home/amule/entrypoint.sh /home/amule/healthcheck.sh

WORKDIR /home/amule

# aMule ports
EXPOSE 4711/tcp 4712/tcp 4662/tcp 4665/udp 4672/udp
# Dashboard
EXPOSE 8078/tcp

HEALTHCHECK --interval=120s --timeout=15s --start-period=60s --retries=3 \
    CMD /home/amule/healthcheck.sh

ENTRYPOINT ["/home/amule/entrypoint.sh"]
