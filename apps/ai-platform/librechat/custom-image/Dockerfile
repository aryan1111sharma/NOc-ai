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
#
# The two attach-file specs run here, in the only window where jest exists in this
# image (after devDeps are installed, before they are pruned). They are the tests
# that cover step 6/6's gate, so a future base bump that reverts the fix fails the
# build instead of silently shipping 400s to Kimi again. Scoped to those two files
# so this costs seconds, not a full client suite.
RUN cd /app \
 && npm install --workspace=client --include=dev --no-audit --no-fund \
 && cd /app/client \
 && npm run test:ci -- AttachFileMenu.spec DragDropModal.spec \
 && npm run build \
 && cd /app \
 && npm prune --omit=dev --no-audit --no-fund \
 && npm cache clean --force \
 && rm -rf /patch-files /apply-patches.sh

# Fail the build if the patches did not actually land in the bundle.
#
# The body-parser check is not redundant with apply-patches.sh's assert-once: that runs
# before the client build, and this confirms the mount survived into the image that
# ships. Without it a 100 MB upload would fail at LibreChat's global 3mb parser with no
# message the Knowledge Bases UI can show -- exactly the opaque failure this update fixes.
RUN test -f /app/api/server/routes/knowledgeBases.js || (echo "API route missing" && exit 1) \
 && test -f /app/api/server/routes/kbUploadLimits.js || (echo "upload-limits module missing" && exit 1) \
 && grep -q "knowledge-bases" /app/api/server/index.js || (echo "route not mounted" && exit 1) \
 && grep -q "app.use('/api/knowledge-bases', express.json({ limit: '150mb' }))" /app/api/server/index.js \
      || (echo "KB body-parser ceiling not mounted" && exit 1) \
 && grep -rq "knowledge-bases" /app/client/dist/assets/ || (echo "client bundle missing KB page" && exit 1) \
 && test -f /app/api/server/routes/llmRouting.js || (echo "LLM Routing API route missing" && exit 1) \
 && grep -q "app.use('/api/llm-routing', routes.llmRouting)" /app/api/server/index.js \
      || (echo "LLM Routing route not mounted" && exit 1) \
 && grep -rq "llm-routing" /app/client/dist/assets/ || (echo "client bundle missing LLM Routing page" && exit 1) \
 && test -f /app/api/server/routes/sessions.js || (echo "Sessions API route missing" && exit 1) \
 && grep -q "app.use('/api/sessions', routes.sessions)" /app/api/server/index.js \
      || (echo "Sessions route not mounted" && exit 1) \
 && grep -rq "com_ui_sessions" /app/client/dist/assets/ || (echo "client bundle missing Sessions page" && exit 1)

USER node
