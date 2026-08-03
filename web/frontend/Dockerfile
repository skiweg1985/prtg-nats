# The web interface.
#
# Built once and served as static files by the reverse proxy. Nothing here runs
# at request time, so the image is a build stage plus a copy.

FROM node:22-alpine AS build

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

# The runtime image is only a file carrier; Caddy serves the volume. The
# files stay in /dist because compose mounts the volume over /srv/www and
# copies them in at start - a build baked directly into /srv/www would be
# hidden by that mount.
FROM alpine:3.21
COPY --from=build /build/dist /dist
CMD ["true"]
