# Cloudflare Worker — página de mantenimiento (`entramado.org`). Spec 040.

Sustituye el **error 1033** de Cloudflare (el "Argo Tunnel error" que sale cuando el Mac que
sirve el sitio se apaga o pierde internet y `cloudflared` se desconecta) por una **página propia
de mantenimiento**.

## Por qué un Worker

El sitio se sirve desde este Mac vía Cloudflare Tunnel (ver [`../cloudflared/`](../cloudflared/)).
El error 1033 lo genera el **edge de Cloudflare** cuando el túnel está caído, y en el plan
**gratuito** no se puede personalizar esa clase de errores 5xx vía "Custom Error Pages" (es Pro+).
La vía sin coste es un **Worker** en la ruta `entramado.org/*`:

- **Origen arriba:** el Worker hace `fetch(request)` al origen y devuelve la respuesta **tal cual**
  (proxy transparente, streaming incluido).
- **Origen caído:** esa subrequest resuelve con **HTTP 530** (el 1033) o lanza excepción; el Worker
  la detecta y responde:
  - navegador (`Accept: text/html`) → **página HTML de mantenimiento**, `503` + `Retry-After: 120`,
    con `<meta refresh>` a 30 s (vuelve sola al sitio cuando el Mac reconecta);
  - `/api`, `/mcp` y clientes no-HTML → `503` con `{"status":"maintenance"}`.

Solo se intercepta cuando el origen/túnel está inaccesible: **HTTP ≥ 502** (probado en vivo — el
túnel caído llega al Worker como **502**, no como el 530 del error 1033 que ve un cliente directo;
también cuenta 503/504 y el rango 52x de Cloudflare) o excepción de `fetch`. Un **500/501**
*aplicativo* con el Mac encendido se deja pasar sin enmascarar.

**Anti-bucle:** una subrequest de un Worker a su propia ruta/zona **no re-invoca** el Worker — va
directa al origen —, así que el proxy no recursa. El Worker corre en el edge (siempre activo,
independiente del Mac), por eso puede servir la página aunque el origen esté apagado del todo.

El free tier de Workers (100k req/día) sobra para el tráfico del sitio.

## Deploy (una vez)

`cloudflared` ya está autenticado para el túnel, pero **wrangler usa auth propio**:

```sh
cd infra/cloudflare-worker
npx wrangler login          # OAuth en el navegador (una vez)
npx wrangler whoami         # copiar el account_id → descomentarlo en wrangler.toml
npx wrangler deploy         # publica el Worker + la ruta entramado.org/*
```

No toca el túnel, ni Caddy, ni la VM `sm-prod`, ni el DNS: solo añade un Worker sobre la ruta ya
proxied de la zona. Para cambios posteriores en `worker.js`, basta `npx wrangler deploy`.

## Probar

1. **Proxy transparente (Mac online):** `curl -sI https://entramado.org` → `200`; el sitio carga
   normal. `npx wrangler tail` muestra el tráfico pasando sin intervenir.
2. **Simular origen caído** (sin apagar el Mac), parando solo el túnel:
   ```sh
   launchctl bootout gui/$(id -u)/com.entramado.cloudflared   # detiene cloudflared
   curl -sI https://entramado.org                                # → 503 + Retry-After: 120
   curl -s  https://entramado.org/api/graph/overview             # → {"status":"maintenance"}
   ```
   Abrir `https://entramado.org` en el navegador → la página de mantenimiento, no el 1033.
3. **Restaurar** el túnel y confirmar que la página (meta-refresh 30 s) vuelve sola al sitio:
   ```sh
   ../launchd/install.sh        # reinstala/reactiva com.entramado.cloudflared
   ```

## Editar la página

El HTML vive inline en `worker.js` (constante `MAINTENANCE_HTML`, una sola fuente de verdad, sin
build). Edítalo ahí y vuelve a `npx wrangler deploy`.

## Rollback

```sh
npx wrangler delete          # elimina el Worker y su ruta
```

El tráfico vuelve al comportamiento actual (1033 cuando el origen cae). No hay nada que revertir en
el túnel ni en el stack.
