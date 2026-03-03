#!/bin/bash
# GPG wrapper: logs plaintext to stdout before encrypting.
# For non-encrypt operations, passes through directly.

is_encrypt=false
for arg in "$@"; do
  if [ "$arg" = "--encrypt" ] || [ "$arg" = "-e" ]; then
    is_encrypt=true
    break
  fi
done

if $is_encrypt && [ ! -t 0 ]; then
  # Stdin is piped — tee it so plaintext shows on stdout
  tmp=$(mktemp)
  cat > "$tmp"
  echo "=== PLAINTEXT ($(date +%H:%M:%S)) ==="
  cat "$tmp"
  echo "=== END PLAINTEXT ==="
  gpg.real "$@" < "$tmp"
  rc=$?
  rm -f "$tmp"
  exit $rc
else
  exec gpg.real "$@"
fi
