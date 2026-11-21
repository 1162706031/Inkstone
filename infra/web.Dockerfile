# Build context: platform/. Builds the workbench and serves it through Caddy with TLS and the /api proxy.
FROM node:24.22.3-bookworm-slim AS build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install -g npm@12.2.3 && npm ci --no-audit --no-fund
COPY contracts /src/contracts
COPY frontend ./
RUN npm run api:generate && npm run build

FROM caddy:2.10.2-alpine
COPY infra/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/frontend/dist /srv
