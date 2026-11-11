#!/bin/sh
# Check the actual database image without a database, network or secrets.
set -eu

for package in libsqlite3-0 gpg gnupg; do
    status=$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)
    if [ "$status" = installed ]; then
        echo "Unnecessary runtime package remains: $package" >&2
        exit 1
    fi
done
if find /usr/lib /lib -name 'libsqlite3.so*' -print -quit | grep -q .; then
    echo 'SQLite shared library remains in the runtime image' >&2
    exit 1
fi
command -v gpgv >/dev/null
command -v gosu >/dev/null
test -x /usr/local/bin/docker-entrypoint.sh
postgres --version | grep -Eq '^postgres \(PostgreSQL\) 17\.'
test -f /usr/share/postgresql/17/extension/vector.control
if ldd "$(command -v postgres)" | grep -q 'not found'; then
    echo 'A PostgreSQL runtime dependency is missing' >&2
    exit 1
fi
echo 'PostgreSQL 17 runtime dependencies verified; SQLite and GPG tooling absent'
