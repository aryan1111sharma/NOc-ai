#!/bin/sh
# Applies the Knowledge Bases additions to a stock LibreChat tree.
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

echo "== 1/5 API route file =="
cp /patch-files/api/knowledgeBases.js "$APP/api/server/routes/knowledgeBases.js"
echo "  installed api/server/routes/knowledgeBases.js"

echo "== 2/5 register route module =="
patch_once "$APP/api/server/routes/index.js" \
  "const mcp = require('./mcp');" \
  "const mcp = require('./mcp');
const knowledgeBases = require('./knowledgeBases');"

patch_once "$APP/api/server/routes/index.js" \
  "  mcp," \
  "  mcp,
  knowledgeBases,"

echo "== 3/5 mount route =="
patch_once "$APP/api/server/index.js" \
  "  app.use('/api/mcp', routes.mcp);" \
  "  app.use('/api/mcp', routes.mcp);
  app.use('/api/knowledge-bases', routes.knowledgeBases);"

echo "== 4/5 client page + route =="
mkdir -p "$APP/client/src/components/KnowledgeBases"
cp /patch-files/client/KnowledgeBasesView.tsx \
   "$APP/client/src/components/KnowledgeBases/KnowledgeBasesView.tsx"

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

echo "== 5/5 sidebar nav item =="
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

echo "PATCHES APPLIED OK"
