#!/bin/sh
# Applies the Knowledge Bases additions, the custom-endpoint file-attachment
# fix (step 7), and the agent-header provider-suffix fix (step 8), to a stock
# LibreChat tree.
#
# Every edit is anchored on a unique, verbatim string and asserted to match
# exactly once. A silent no-op here would produce an image that builds and boots
# but is missing the feature, which is far worse to debug than a failed build --
# so every substitution is verified and the script aborts on the first miss.
set -eu

APP=/app

fail() { echo "PATCH FAILED: $1" >&2; exit 1; }

# Assert `needle` occurs exactly once in `file`, then replace it with `repl`.
# Counting happens in Python, not grep: anchors here span multiple lines, and
# `grep -c` counts matching *lines*, which silently reports the wrong number for
# a multi-line needle.
patch_once() {
  file="$1"; needle="$2"; repl="$3"
  python3 - "$file" "$needle" "$repl" <<'PY' || exit 1
import sys, pathlib
path, needle, repl = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
s = p.read_text(encoding="utf-8")
n = s.count(needle)
if n != 1:
    sys.stderr.write(
        f"PATCH FAILED: expected 1 occurrence of anchor in {path}, found {n}\n"
        f"--- anchor ---\n{needle}\n"
    )
    sys.exit(1)
p.write_text(s.replace(needle, repl), encoding="utf-8")
PY
  echo "  patched $file"
}

echo "== 1/7 API route files =="
cp /patch-files/api/knowledgeBases.js "$APP/api/server/routes/knowledgeBases.js"
echo "  installed api/server/routes/knowledgeBases.js"
# knowledgeBases.js does `require('./kbUploadLimits')`, so a missing copy here is a
# boot-time crash rather than a degraded feature.
cp /patch-files/api/kbUploadLimits.js "$APP/api/server/routes/kbUploadLimits.js"
echo "  installed api/server/routes/kbUploadLimits.js"

echo "== 2/7 register route module =="
patch_once "$APP/api/server/routes/index.js" \
  "const mcp = require('./mcp');" \
  "const mcp = require('./mcp');
const knowledgeBases = require('./knowledgeBases');"

patch_once "$APP/api/server/routes/index.js" \
  "  mcp," \
  "  mcp,
  knowledgeBases,"

echo "== 3/7 mount route =="
patch_once "$APP/api/server/index.js" \
  "  app.use('/api/mcp', routes.mcp);" \
  "  app.use('/api/mcp', routes.mcp);
  app.use('/api/knowledge-bases', routes.knowledgeBases);"

echo "== 4/7 body-parser ceiling for KB uploads =="
# A KB document upload carries the whole file base64-encoded inside a JSON body, so it
# is bounded by express.json, not by the route. Upstream's global parser is 3mb, which
# capped every PDF at ~2.25 MB regardless of what kb-mcp would accept.
#
# The larger parser is mounted for /api/knowledge-bases ONLY, and BEFORE the global one.
# Both details matter:
#
#   * Path-scoped, so raising the KB ceiling to 150mb does not let every other route in
#     LibreChat accept a 150 MB body. The global 3mb parser is left exactly as upstream
#     ships it.
#   * Before, because whichever parser runs first is the one that reads the stream.
#     body-parser skips a request whose stream is already finished (lib/read.js checks
#     onFinished.isFinished(req)), so the global parser no-ops for these requests
#     instead of re-reading or rejecting them.
#
# 150mb clears the ~133.4 MB that 100 MB of file becomes on the wire; nginx.conf is set
# above this again, and services/mcp/kb/tests/test_upload_limits.py asserts the ordering.
patch_once "$APP/api/server/index.js" \
  "  app.use(express.json({ limit: '3mb' }));" \
  "  /* NOC: KB document uploads carry a base64-encoded file in the JSON body. Mounted
     before the global parser, and scoped to this path, so no other route is loosened.
     See apply-patches.sh step 4/7. */
  app.use('/api/knowledge-bases', express.json({ limit: '150mb' }));
  app.use(express.json({ limit: '3mb' }));"

echo "== 5/7 client page + route =="
mkdir -p "$APP/client/src/components/KnowledgeBases"
cp /patch-files/client/KnowledgeBasesView.tsx \
   "$APP/client/src/components/KnowledgeBases/KnowledgeBasesView.tsx"
# The bulk-upload queue the page delegates to. Kept as its own module so its behaviour
# (sequential uploads, per-file status, continue-on-failure, the size gate) is testable
# with the plain Node test runner -- see custom-image/tests/kbUpload.test.ts.
cp /patch-files/client/kbUpload.ts \
   "$APP/client/src/components/KnowledgeBases/kbUpload.ts"

patch_once "$APP/client/src/routes/index.tsx" \
  "const loadSkillsView = () =>" \
  "const loadKnowledgeBasesView = () =>
  import('~/components/KnowledgeBases/KnowledgeBasesView').then((m) => ({
    Component: m.default,
  }));

const loadSkillsView = () =>"

patch_once "$APP/client/src/routes/index.tsx" \
  "            {
              path: 'search',
              element: <Search />,
            }," \
  "            {
              path: 'search',
              element: <Search />,
            },
            {
              path: 'knowledge-bases',
              lazy: loadKnowledgeBasesView,
            },"

echo "== 6/7 sidebar nav item =="
patch_once "$APP/client/src/hooks/Nav/useUnifiedSidebarLinks.ts" \
  "import { MessagesSquare } from 'lucide-react';" \
  "import { Database, MessagesSquare } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { SystemRoles } from 'librechat-data-provider';
import { useAuthContext } from '~/hooks/AuthContext';"

patch_once "$APP/client/src/hooks/Nav/useUnifiedSidebarLinks.ts" \
  "export default function useUnifiedSidebarLinks() {" \
  "export default function useUnifiedSidebarLinks() {
  const navigate = useNavigate();
  const { user } = useAuthContext();
  /* Mirrors how LibreChat gates its own admin UI (see AdminSettingsDialog).
     Hiding the entry is convenience only -- /api/knowledge-bases independently
     requires access:admin AND manage:mcpservers, so a non-admin who navigates
     to the route directly gets an empty page backed by 403s. */
  const isAdmin = user?.role === SystemRoles.ADMIN;"

patch_once "$APP/client/src/hooks/Nav/useUnifiedSidebarLinks.ts" \
  "    return [conversationLink, ...sideNavLinks];
  }, [sideNavLinks]);" \
  "    const links: NavLink[] = [conversationLink, ...sideNavLinks];

    if (isAdmin) {
      links.push({
        title: 'com_ui_knowledge_bases' as NavLink['title'],
        label: 'Knowledge Bases',
        icon: Database,
        id: 'knowledge-bases',
        onClick: () => navigate('/knowledge-bases'),
      });
    }

    return links;
  }, [sideNavLinks, isAdmin, navigate]);"

echo "== 7/7 no native file parts for custom endpoints =="
# A `custom` endpoint is any OpenAI-compatible provider declared in librechat.yaml
# (here: Kimi). Upstream v0.8.7 puts EModelEndpoint.custom in documentSupportedProviders
# (packages/data-provider/src/schemas.ts), and since PR #12117 an agent resolves its
# endpointType from its provider -- so the NOC Engineer (Kimi) agent resolves to
# `custom` and the attach menu offers "Upload to Provider". That path sends the
# document as an OpenAI `{ type: 'file' }` content part, which api.kimi.com/coding/v1
# rejects:
#     400 the message at position N with role 'user' contains an invalid part type: file
# The failed part is persisted to the conversation, so every later turn resends it and
# the thread stays broken (upstream issue #13334).
#
# Fixing it in documentSupportedProviders would not work: the client resolves
# librechat-data-provider to its prebuilt dist/, which `npm run build` in client/ does
# not recompile. So the two components that read the flag are patched instead -- both,
# because the paperclip menu and drag-and-drop must make the same decision.
#
# Documents on custom endpoints go through "Upload as Text" (the `context` capability,
# built-in document_parser: PDF/DOCX/XLS/XLSX, no external service) which every model
# accepts. Images still upload natively. Built-in providers -- openAI, anthropic,
# google, bedrock, azureOpenAI+Responses -- are untouched.
patch_once "$APP/client/src/components/Chat/Input/Files/AttachFileMenu.tsx" \
  "      if (
        isDocumentSupportedProvider(endpointType) ||
        isDocumentSupportedProvider(currentProvider) ||
        isAzureWithResponsesApi
      ) {
        items.push({" \
  "      /* NOC: custom endpoints cannot be assumed to accept native \`file\` parts. */
      const isCustomEndpoint =
        endpointType === EModelEndpoint.custom || currentProvider === EModelEndpoint.custom;

      if (
        !isCustomEndpoint &&
        (isDocumentSupportedProvider(endpointType) ||
          isDocumentSupportedProvider(currentProvider) ||
          isAzureWithResponsesApi)
      ) {
        items.push({"

patch_once "$APP/client/src/components/Chat/Input/Files/DragDropModal.tsx" \
  "    // Check if provider supports document upload
    if (
      isDocumentSupportedProvider(endpointType) ||
      isDocumentSupportedProvider(currentProvider) ||
      isAzureWithResponsesApi
    ) {" \
  "    /* NOC: mirrors AttachFileMenu.tsx -- dropping a PDF must not re-open the
       provider-native path that Kimi rejects with 'invalid part type: file'. */
    const isCustomEndpoint =
      endpointType === EModelEndpoint.custom || currentProvider === EModelEndpoint.custom;

    // Check if provider supports document upload
    if (
      !isCustomEndpoint &&
      (isDocumentSupportedProvider(endpointType) ||
        isDocumentSupportedProvider(currentProvider) ||
        isAzureWithResponsesApi)
    ) {"

# Upstream's own specs assert the pre-fix behaviour, so they are inverted here rather
# than left failing. Both use 'Moonshot' (Kimi) as the custom provider, which is
# exactly the reported case.
patch_once "$APP/client/src/components/Chat/Input/Files/__tests__/AttachFileMenu.spec.tsx" \
  "    it('shows \"Upload to Provider\" when endpointType is custom (resolved from agent provider)', () => {
      setupMocks({ provider: 'Moonshot' });
      renderMenu({ endpointType: EModelEndpoint.custom });
      openMenu();
      expect(screen.getByText('Upload to Provider')).toBeInTheDocument();
      expect(screen.queryByText('Upload Image')).not.toBeInTheDocument();
    });" \
  "    /* NOC: inverted from upstream -- see apply-patches.sh step 7/7. */
    it('shows \"Upload Image\", not \"Upload to Provider\", when endpointType is custom', () => {
      setupMocks({ provider: 'Moonshot' });
      renderMenu({ endpointType: EModelEndpoint.custom });
      openMenu();
      expect(screen.getByText('Upload Image')).toBeInTheDocument();
      expect(screen.queryByText('Upload to Provider')).not.toBeInTheDocument();
    });"

patch_once "$APP/client/src/components/Chat/Input/Files/__tests__/AttachFileMenu.spec.tsx" \
  "    it('shows \"Upload to Provider\" when agents endpoint has custom endpointType from provider', () => {
      setupMocks({ provider: 'Moonshot' });
      renderMenu({
        endpoint: EModelEndpoint.agents,
        endpointType: EModelEndpoint.custom,
      });
      openMenu();
      expect(screen.getByText('Upload to Provider')).toBeInTheDocument();
    });" \
  "    /* NOC: this is the reported bug exactly -- the NOC Engineer (Kimi) agent
       resolves endpointType to \`custom\`. */
    it('shows \"Upload Image\" when an agent resolves a custom endpointType from its provider', () => {
      setupMocks({ provider: 'Moonshot' });
      renderMenu({
        endpoint: EModelEndpoint.agents,
        endpointType: EModelEndpoint.custom,
      });
      openMenu();
      expect(screen.getByText('Upload Image')).toBeInTheDocument();
      expect(screen.queryByText('Upload to Provider')).not.toBeInTheDocument();
    });"

echo "== 8/8 hide provider suffix in agent chat header =="
# Agents are sometimes named "<Agent Name> (Provider)" to distinguish variants
# that differ only in which model serves them, e.g. agent_kimi26NOCEngineerV1's
# stored name "NOC Engineer (Kimi)" (agents/agents.json). getDisplayValue feeds
# the chat header's model-selector button directly from that stored name, so it
# rendered the provider suffix verbatim. Stripped for display only, in the one
# function that renders it there -- the stored agent.name is untouched.
cp /patch-files/client/displayAgentName.ts \
   "$APP/client/src/components/Chat/Menus/Endpoints/displayAgentName.ts"

patch_once "$APP/client/src/components/Chat/Menus/Endpoints/utils.ts" \
  "import { getSpecAgentAvatarURL } from '~/utils';" \
  "import { getSpecAgentAvatarURL } from '~/utils';
import { displayAgentName } from './displayAgentName';"

patch_once "$APP/client/src/components/Chat/Menus/Endpoints/utils.ts" \
  "      return endpoint.agentNames[selectedValues.model];
    } else if (isAgentsEndpoint(endpoint.value) && agentsMap) {
      const agent = agentsMap[selectedValues.model];
      return agent?.name || selectedValues.model;
    }" \
  "      return displayAgentName(endpoint.agentNames[selectedValues.model]);
    } else if (isAgentsEndpoint(endpoint.value) && agentsMap) {
      const agent = agentsMap[selectedValues.model];
      return displayAgentName(agent?.name || selectedValues.model);
    }"

echo "PATCHES APPLIED OK"
