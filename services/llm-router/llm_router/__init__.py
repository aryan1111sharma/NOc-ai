"""NOC LLM Router: a small OpenAI-compatible chat-completions proxy that fans
a logical route out to an ordered list of provider connections, falling back
to the next connection only when the previous one fails before it produced
any response bytes.
"""
