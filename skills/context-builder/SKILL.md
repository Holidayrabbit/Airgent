# context-builder
Build context from the current request, local transcript, and long-term memory.

Use this skill when the user asks something that depends on prior preferences, ongoing projects, or past commitments.

Workflow:
1. Call `search_memory` with the user's current topic.
2. Reconcile memory hits with the current request instead of blindly trusting them.
3. If the conversation reveals a durable preference, project fact, or commitment, call `remember_note`.
4. Do not store secrets, access tokens, or temporary logistics unless the user explicitly requests it.

Examples of good memory:
- "The user prefers concise technical answers."
- "The user's side project is named Airgent."
- "The user uses uv to manage Python environments."

Examples of bad memory:
- One-time OTP codes.
- Temporary meeting links.
- Raw API keys or passwords.
