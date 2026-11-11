FROM caddy:2.11.9-builder@sha256:1b828679ae08622c534d8e83ed80c6c25d343202f0586fe3b52d8e0ce33413b9 AS webserver
ENV GOTOOLCHAIN=local
# Remove once the upstream Caddy release carries x/net >= 0.62.0 (CVE-2026-78661).
RUN xcaddy build v2.11.9 --replace golang.org/x/net=golang.org/x/net@v0.62.0

FROM node:24-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
FROM caddy:2-alpine@sha256:78bd35025c7ba85961dd149ae670315129954e77abe2383ac47c89106b35ade9
COPY --from=webserver /usr/bin/caddy /usr/bin/caddy
COPY infra/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
