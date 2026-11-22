#!/bin/sh
# Fetch the vault (briefings + notes served by /briefings and the MCP vault
# tools) from R2 at boot. Tarball layout: `tar czf sm-vault.tgz sm-vault` on the
# Mac → strip the top-level dir into /vault. A failed fetch degrades gracefully:
# the app already handles a missing /vault (briefings report unavailable).
if [ -n "$VAULT_SEED_URL" ]; then
    echo "[entrypoint] fetching vault from $VAULT_SEED_URL"
    mkdir -p /vault
    if curl -fsSL "$VAULT_SEED_URL" | tar xz -C /vault --strip-components=1; then
        echo "[entrypoint] vault ready ($(find /vault -name '*.md' | wc -l) notes)"
    else
        echo "[entrypoint] WARNING: vault fetch failed — briefings unavailable"
    fi
fi
exec "$@"
