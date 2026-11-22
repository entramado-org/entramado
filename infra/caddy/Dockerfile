# Custom Caddy build: the stock `caddy:2` image has no rate limiter, so we
# compile in mholt/caddy-ratelimit. Used to throttle abusive clients now that the
# site is public (the home/web + /api; /mcp keeps its own app-level limiter too).
# Built automatically by `./prod-machine.sh up` (compose `--build`).
FROM caddy:2-builder AS builder
RUN xcaddy build \
    --with github.com/mholt/caddy-ratelimit

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
