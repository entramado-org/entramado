#!/bin/bash
# Paridad de la web (spec 066, AC9b/AC12/AC13/AC14): captura comparable de una base (prod o :3100).
# Uso: _web_parity.sh <base-url> <outdir>     p.ej. _web_parity.sh https://entramado.org /tmp/antes
# Guarda: sitemap.xml y robots.txt tal cual; status de TODAS las URL del sitemap; <head> SEO de 6 rutas × 5 UAs
# (navegador + 4 bots de vistas previas); cabeceras ISR de entidad/caso; 404 de slugs inexistentes. Solo lectura.
set -uo pipefail
BASE="${1:?base url}"; OUT="${2:?outdir}"; mkdir -p "$OUT"
UA_NAV='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
declare -a UAS=("$UA_NAV" "Googlebot/2.1 (+http://www.google.com/bot.html)" "Twitterbot/1.0" "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)" "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)")
declare -a UAN=(nav googlebot twitterbot linkedinbot facebook)
cb() { printf '%s' "$1" | grep -q '?' && printf '%s&cb=%s' "$1" "$(date +%s%N)" || printf '%s?cb=%s' "$1" "$(date +%s%N)"; }

echo "== sitemap/robots"
curl -s -A "$UA_NAV" "$BASE/sitemap.xml" > "$OUT/sitemap.xml"; curl -s -A "$UA_NAV" "$BASE/robots.txt" > "$OUT/robots.txt"
python3 - "$OUT/sitemap.xml" "$BASE" > "$OUT/urls.txt" <<'PY'
import re,sys; x=open(sys.argv[1],encoding='utf-8').read(); base=sys.argv[2].rstrip('/')
urls=re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', x)
# normaliza al host pedido (el sitemap siempre apunta a entramado.org)
print('\n'.join(re.sub(r'^https?://[^/]+', base, u) for u in urls))
PY
echo "urls en sitemap: $(wc -l < "$OUT/urls.txt" | tr -d ' ')"

echo "== status de todas las URL (AC14)"
: > "$OUT/status.tsv"
while read -r u; do
  [ -z "$u" ] && continue
  code=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -A "$UA_NAV" "$u")
  printf '%s\t%s\n' "$code" "${u#$BASE}" >> "$OUT/status.tsv"
done < "$OUT/urls.txt"
awk -F'\t' '{split($1,a," "); c[a[1]]++} END {for (k in c) printf "  %s: %d\n", k, c[k]}' "$OUT/status.tsv"
grep -v $'^200 \t' "$OUT/status.tsv" | head -10

echo "== <head> SEO de 6 rutas × 5 UAs (AC13)"
ENT=$(grep -m1 '/entidad/' "$OUT/urls.txt" | sed "s#^$BASE##"); CASO=$(grep -m1 '/caso/[^/]' "$OUT/urls.txt" | sed "s#^$BASE##")
for r in "/" "$ENT" "$CASO" "/ultima-hora" "/contribuir" "/aviso-legal"; do
  for i in 0 1 2 3 4; do
    f="$OUT/head_$(printf '%s' "$r" | tr '/:' '__')_${UAN[$i]}.json"
    curl -s -A "${UAS[$i]}" "$(cb "$BASE$r")" | python3 -c '
import sys,json,re
from html.parser import HTMLParser
class P(HTMLParser):
    def __init__(s): super().__init__(); s.t=False; s.o={"title":"","meta":{},"link":{}}
    def handle_starttag(s,tag,attrs):
        a=dict(attrs)
        if tag=="title": s.t=True
        if tag=="meta":
            k=a.get("name") or a.get("property")
            if k and (k in ("description","robots") or k.startswith("og:") or k.startswith("twitter:")): s.o["meta"][k]=a.get("content","")
        if tag=="link" and a.get("rel") in ("canonical","alternate","icon"): s.o["link"][a.get("rel")+"|"+a.get("hreflang","")]=a.get("href","")
    def handle_data(s,d):
        if s.t: s.o["title"]+=d
    def handle_endtag(s,tag):
        if tag=="title": s.t=False
p=P(); h=sys.stdin.read(); p.feed(h)
p.o["title"]=p.o["title"].strip(); p.o["html_bytes"]=len(h); p.o["hydration_markers"]={"__next_f":h.count("__next_f"),"cf_email":h.count("__cf_email__")}
print(json.dumps(p.o,ensure_ascii=False,sort_keys=True,indent=1))' > "$f"
  done
  echo "  $r → title: $(python3 -c "import json;print(json.load(open('$OUT/head_$(printf '%s' "$r" | tr '/:' '__')_nav.json'))['title'][:70])")"
done

echo "== cabeceras ISR entidad/caso (AC12)"
for r in "$ENT" "$CASO"; do
  curl -s -D - -o /dev/null -A "$UA_NAV" "$BASE$r" | grep -i '^cache-control\|^x-nextjs-cache\|^cf-cache-status\|^age:' | tr -d '\r' | sed "s#^#  $r  #"
done | tee "$OUT/isr_headers.txt"

echo "== 404 de slugs inexistentes (AC9b)"
for r in /entidad/no-existe-xyz /caso/no-existe-xyz /briefings/no-existe-xyz /pagina-que-no-existe; do
  printf '  %s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' -A "$UA_NAV" "$(cb "$BASE$r")")" "$r"
done | tee "$OUT/404.txt"
echo "captura en $OUT"
