#!/bin/sh
# 使用本机 CA 证书库，修复迁移后 Python 默认 CA 路径缺失的问题。
set -eu
cd "$(dirname "$0")"
if [ -z "${SSL_CERT_FILE:-}" ]; then
  for ca_file in /opt/homebrew/etc/ca-certificates/cert.pem /etc/ssl/cert.pem; do
    if [ -r "$ca_file" ]; then
      export SSL_CERT_FILE="$ca_file"
      break
    fi
  done
fi
exec python3 app/serve.py "$@"
