# Custom LibreChat image: stock upstream + a Knowledge Bases admin page.
#
# Built FROM the exact upstream image rather than from LibreChat source. That is
# deliberate: it guarantees every dependency, package build and asset is bit-for-bit
# what is already running, so the only difference between this image and the one in
# production is the patch set below. Building from source would re-resolve the whole
# dependency tree and make "did the upgrade break it?" unanswerable.
#
# Pin BASE to a digest or version tag before relying on this in production; :latest
# means a rebuild can silently move the base underneath the patches.
ARG BASE=ghcr.io/danny-avila/librechat:latest
FROM ${BASE}

USER root

# The runtime image ships client/src but strips the frontend build tooling, so the
# devDependencies have to come back before vite can regenerate client/dist.
# python3 is only used by apply-patches.sh for exact-match, assert-once edits.
RUN apk add --no-cache python3 2>/dev/null || (apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*)

COPY files /patch-files
COPY apply-patches.sh /apply-patches.sh

RUN chmod +x /apply-patches.sh && /apply-patches.sh

# Install client devDeps, rebuild the SPA, then drop them again so the shipped
# image stays close to upstream's size and surface.
RUN cd /app \
 && npm install --workspace=client --include=dev --no-audit --no-fund \
 && cd /app/client \
 && npm run build \
 && cd /app \
 && npm prune --omit=dev --no-audit --no-fund \
 && npm cache clean --force \
 && rm -rf /patch-files /apply-patches.sh

# Fail the build if the patches did not actually land in the bundle.
RUN test -f /app/api/server/routes/knowledgeBases.js || (echo "API route missing" && exit 1) \
 && grep -q "knowledge-bases" /app/api/server/index.js || (echo "route not mounted" && exit 1) \
 && grep -rq "knowledge-bases" /app/client/dist/assets/ || (echo "client bundle missing KB page" && exit 1)

USER node
