# Render variant of infra/caddy/Dockerfile: same custom build (stock caddy:2 has
# no rate limiter, mholt/caddy-ratelimit is compiled in) but the Caddyfile is
# COPIED into the image — Render has no bind mounts.
FROM caddy:2-builder AS builder
RUN xcaddy build \
    --with github.com/mholt/caddy-ratelimit

FROM caddy:2
# Render's container runtime refuses to exec binaries carrying file
# capabilities ("exec /usr/bin/caddy: operation not permitted") and the xcaddy
# builder setcap's cap_net_bind_service onto the binary. `cat` strips the
# xattr; the capability isn't needed anyway because Caddy listens on the
# unprivileged :8080 here (SITE_HOST in render.yaml).
COPY --from=builder /usr/bin/caddy /tmp/caddy-built
RUN cat /tmp/caddy-built > /usr/bin/caddy \
    && chmod 755 /usr/bin/caddy \
    && rm /tmp/caddy-built
COPY Caddyfile /etc/caddy/Caddyfile
