# Cloudflare Tunnel — `entramado.org` (spec 020)

Serves the public site under the owned domain **`entramado.org`** by running a local
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
(`cloudflared`) that connects this Mac to Cloudflare's edge. TLS terminates at the edge
with a real cert for `entramado.org`; `cloudflared` forwards to the **same loopback
forwarder the Tailscale Funnel uses** (`socat 127.0.0.1:8080 → sm-prod:80` Caddy).

This is a **second ingress alongside the Funnel**, which stays up as a fallback
(`<tailnet-host>.ts.net` keeps serving saved MCP connectors). `entramado.org` is the
canonical URL: it serves the web and `entramado.org/mcp`, and `/conectar` shows the
domain-based connector URL automatically (it derives it from the `Host` header).

Local (not token-managed) tunnel: [`config.yml`](./config.yml) is the versioned source of
truth. Credentials live in `~/.cloudflared` and are **never committed**.

## One-time setup

```sh
# 1. Authorize cloudflared against the entramado.org zone (opens a browser).
cloudflared tunnel login

# 2. Create the tunnel. Prints a UUID and writes ~/.cloudflared/<UUID>.json.
cloudflared tunnel create entramado

# 3. Paste that UUID into BOTH fields of infra/cloudflared/config.yml
#    (tunnel: and credentials-file:), replacing <UUID>.

# 4. Point the apex domain at the tunnel (creates a proxied CNAME).
cloudflared tunnel route dns entramado entramado.org

# 5. Install/refresh the launchd agent (copies config.yml + cloudflared.sh to the
#    TCC-safe Support dir and bootstraps com.entramado.cloudflared).
./infra/launchd/install.sh
```

After step 5, `https://entramado.org` serves the site (behind the same Basic Auth as the
Funnel; `/mcp` is exempt). The tunnel auto-starts on login via the launchd agent.

## Email — `contacto@entramado.org`

Not part of `cloudflared`. In the Cloudflare dashboard → **Email → Email Routing**:
enable it (Cloudflare adds the MX + SPF records automatically), add the custom address
`contacto@entramado.org` → destination the owner's Gmail, **verify** the destination
(click the confirmation email), and set the **catch-all action to Drop** (only
`contacto@` is accepted). No mail server, no outbound/SMTP.

## Operate

- **Status / logs:** `launchctl print gui/$(id -u)/com.entramado.cloudflared`;
  `tail -f /tmp/sm_cloudflared.launchd.log`. cloudflared reconnects to the edge on its
  own; launchd's KeepAlive restarts it if it dies.
- **Edit the ingress** (e.g. add `www.entramado.org`): edit `config.yml`, re-run
  `./infra/launchd/install.sh` (re-copies it to the Support dir and reloads the agent).
- **Rotate / recreate the tunnel:** `cloudflared tunnel delete entramado` then redo the
  one-time setup. The credentials JSON in `~/.cloudflared` is the secret.

## Rollback

`./infra/launchd/install.sh uninstall` (removes all three agents) — or `launchctl bootout
gui/$(id -u)/com.entramado.cloudflared` to drop only the tunnel — then optionally
`cloudflared tunnel route dns` cleanup / `cloudflared tunnel delete entramado`. The
Tailscale Funnel and the `sm-prod` stack are untouched; the site stays reachable on the
`…ts.net` URL. Email reverts by disabling Email Routing in the dashboard.
