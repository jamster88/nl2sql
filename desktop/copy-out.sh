#!/bin/sh
# Put the jar where the checkout can reach it, as whoever owns that place.
#
# /out is the checkout's desktop/target, mounted. Copied as root, the jar
# would be root's in someone's working tree (V6-31); copied as the
# directory's owner, it is theirs, as if they had built it. A directory root
# owns -- Docker made it because nothing was there -- is written as root,
# the one account that can.
set -eu

out="${NL2SQL_OUT:-/out}"
jar="${NL2SQL_JAR:-/opt/nl2sql/nl2sql-desktop.jar}"
owner="$(stat -c %u:%g "$out")"
if [ "$(id -u)" = 0 ] && [ "${owner%%:*}" != 0 ]; then
    exec su-exec "$owner" cp "$jar" "$out/nl2sql-desktop.jar"
fi
exec cp "$jar" "$out/nl2sql-desktop.jar"
