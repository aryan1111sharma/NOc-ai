/**
 * Agents are sometimes named with a trailing "(Provider)" suffix, e.g.
 * "NOC Engineer (Kimi)", to distinguish variants that differ only in which
 * model serves them (see agents/agents.json). That suffix belongs in the
 * agent's stored name so admins can tell variants apart, but it should not
 * leak into the chat header -- so this strips it for display only. The
 * stored agent.name (config, exports, backend) is untouched.
 */
export function displayAgentName(name: string): string {
  return name.replace(/\s*\([^()]*\)\s*$/, '');
}
