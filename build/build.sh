#!/bin/bash
# 本地打包：app/ 内容由 fnpack 自动压成 app.tgz 并算 checksum
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WEBUI_VER="$(tr -d '[:space:]' < VERSION)"
[ -n "${WEBUI_VER}" ] || { echo "VERSION 为空"; exit 1; }
REV="${REV:-1}"
PKG_VER="${WEBUI_VER}-${REV}"

echo "→ 上游 hermes-web-ui = ${WEBUI_VER}，包版本 = ${PKG_VER}"

# app/ 里的内容就是解到 @appcenter/<app>/ 的东西
cp -f VERSION app/VERSION
[ -f AGENT_VERSION ] && cp -f AGENT_VERSION app/AGENT_VERSION

# 注入 manifest 的 version（checksum 由 fnpack 计算）
python3 - "$PKG_VER" <<'PY'
import re, sys
ver = sys.argv[1]
p = "manifest"
t = open(p, encoding="utf-8").read()
if re.search(r"(?m)^version\s*=", t):
    t = re.sub(r"(?m)^version\s*=.*$", f"version = {ver}", t, count=1)
else:
    t = t.rstrip("\n") + f"\nversion = {ver}\n"
# checksum 由 fnpack 注入，先移除旧的避免校验冲突
t = re.sub(r"(?m)^checksum\s*=.*\n?", "", t)
open(p, "w", encoding="utf-8").write(t)
print("→ manifest version =", ver)
PY

rm -f ./*.fpk
fnpack build 2>&1 | sed 's/^/  /'

FPK="$(ls -1 ./*.fpk 2>/dev/null | head -1)"
[ -n "${FPK}" ] || { echo "✗ 未生成 fpk"; exit 1; }

HDR="$(od -An -tx1 -N2 "$FPK" | tr -d ' \n')"
[ "$HDR" = "1f8b" ] || { echo "✗ 不是 gzip（${HDR}）→ 安装报 10111"; exit 1; }
echo "→ gzip 头 OK"

rm -rf /tmp/fpkchk && mkdir -p /tmp/fpkchk
tar xzf "$FPK" -C /tmp/fpkchk 2>/dev/null
for f in manifest cmd/main cmd/install_init cmd/install_callback cmd/uninstall_init \
         cmd/uninstall_callback cmd/upgrade_init cmd/upgrade_callback \
         cmd/config_init cmd/config_callback \
         config/privilege config/resource app.tgz; do
    [ -e "/tmp/fpkchk/$f" ] || { echo "✗ 缺 ${f}"; exit 1; }
done
echo "→ 包内必需项齐全"

rm -rf /tmp/appchk && mkdir -p /tmp/appchk
tar xzf /tmp/fpkchk/app.tgz -C /tmp/appchk
for f in VERSION ui/config ui/images/icon_256.png ui/images/icon_64.png; do
    [ -e "/tmp/appchk/$f" ] || { echo "✗ app.tgz 缺 ${f}"; exit 1; }
done
echo "→ app.tgz 结构 OK"
echo
echo "✓ 打包完成: $FPK  $(stat -c %s "$FPK") B  md5=$(md5sum "$FPK" | cut -d' ' -f1)"
