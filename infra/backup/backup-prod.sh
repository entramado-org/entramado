#!/usr/bin/env bash
# Encrypted backup of the LIVE production data (spec: security audit 2026-06-27).
#
# What it captures:
#   1. The VM-local Neo4j store /var/lib/sm-prod-neo4j (the canonical post-cutover
#      graph — ./neo4j/data on the Mac is the STALE seed, do not back that up).
#   2. The sm-vault notes/briefings (on the Mac home).
# Both are tar'd, then encrypted with AES-256 (openssl) using a passphrase kept in
# the macOS Keychain (service `sm-backup-passphrase`). Plaintext tars are shredded.
#
# Neo4j Community has no online backup, so we briefly `compose stop neo4j` for a
# consistent offline copy (~1 min; the web stays up, only graph queries blip). Run
# this off-peak.
#
# Usage:  ./infra/backup/backup-prod.sh [dest-dir]      (default: ~/sm-backups)
# Restore (manual): decrypt → extract neo4j store back to /var/lib/sm-prod-neo4j
#   inside sm-prod with the stack stopped; extract sm-vault over ./sm-vault.
#   openssl enc -d -aes-256-cbc -pbkdf2 -in <file>.tgz.enc -out <file>.tgz \
#     -pass pass:"$(security find-generic-password -w -s sm-backup-passphrase)"
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${1:-$HOME/sm-backups}"
CTR="${CONTAINER_BIN:-/opt/homebrew/bin/container}"
MACHINE=sm-prod
KEYCHAIN_SERVICE=sm-backup-passphrase
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

inmachine() { "$CTR" machine run -n "$MACHINE" --root -- "$@"; }
compose()   { inmachine bash "$REPO/prod-machine.sh" __in compose "$@"; }

# --- passphrase: reuse the Keychain one, or generate + store it once -----------
if ! PASS="$(security find-generic-password -w -s "$KEYCHAIN_SERVICE" 2>/dev/null)"; then
  PASS="$(openssl rand -base64 32)"
  security add-generic-password -a "$USER" -s "$KEYCHAIN_SERVICE" -w "$PASS" -U
  echo "🔑 New backup passphrase stored in Keychain (service '$KEYCHAIN_SERVICE')."
  echo "   COPY IT TO YOUR PASSWORD MANAGER — without it the backups are unrecoverable:"
  echo "   security find-generic-password -w -s $KEYCHAIN_SERVICE"
fi

encrypt() {  # <plaintext-tar>
  local f="$1"
  openssl enc -aes-256-cbc -pbkdf2 -salt -in "$f" -out "$f.enc" -pass pass:"$PASS"
  rm -f "$f"
  echo "  🔒 $(basename "$f").enc ($(du -h "$f.enc" | cut -f1))"
}

echo "▶ backup $TS → $DEST"

# 1) Neo4j store — brief stop for a consistent offline copy
echo "▶ stopping neo4j (graph downtime begins)…"
compose stop neo4j >/dev/null
trap 'echo "▶ restarting neo4j (downtime ends)…"; compose start neo4j >/dev/null' EXIT
inmachine bash "$REPO/infra/backup/_vm_dump_store.sh" "$DEST" "$TS"
compose start neo4j >/dev/null
trap - EXIT
echo "▶ neo4j back up"

# 2) Vault
tar czf "$DEST/sm-vault-$TS.tgz" -C "$REPO" sm-vault

# 3) Encrypt both, shred plaintext
encrypt "$DEST/sm-neo4j-store-$TS.tgz"
encrypt "$DEST/sm-vault-$TS.tgz"

echo "✅ done. Archives in $DEST (copy them OFF this Mac — external disk / cloud):"
ls -lh "$DEST"/*-"$TS".tgz.enc | awk '{print "   "$5, $9}'
