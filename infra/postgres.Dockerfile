# Keep PostgreSQL 17 and Debian bookworm: existing volumes retain their libc
# collation version. Apply vendor security updates missing from the upstream build.
FROM golang:1.27-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea AS gosu
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOBIN=/out
# Upstream 1.19 is tagged without the Go module's v prefix; pin its commit.
RUN go install -ldflags='-X main.Version=1.19' github.com/tianon/gosu@6456aaa0f3c854d199d0f037f068eb97515b7513

FROM pgvector/pgvector:pg17@sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
COPY --from=gosu /out/gosu /usr/local/bin/gosu
