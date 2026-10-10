---
name: g-codex-delegate
description: Hand a bounded coding, repository research or review job from Claude Code to the local Codex CLI. Uses ChatGPT subscription allowance when logged in with ChatGPT. Does not control ordinary ChatGPT conversations.
---

# Delegate to Codex

Use this for an independent coding task that benefits from OpenAI compute.
Preserve the user's chosen product: a request to operate ChatGPT's chat UI needs
the available browser/computer skill, not substitution with Codex. This workflow
controls local Codex sessions; it does not expose chat history or the chat UI.

Check `codex --version`, `codex exec --help` and `codex login status` on the target
host. ChatGPT sign-in uses subscription allowance; API-key sign-in uses separate
billing. Report missing CLI/auth rather than reading credentials or changing
identity. Check usage with `compute-usage` when installed or Codex `/status`.

Write a short brief with the objective, repo/worktree, scope, relevant evidence,
constraints, done condition and required verification. Do not paste the parent
conversation or ask the worker to delegate recursively. Choose an available model:
Luna for narrow reconnaissance; a stronger model for nuanced implementation or
quality review. Do not silently fall back to paid API calls.

For read-only reconnaissance, pass the brief on stdin:

```sh
codex exec -C /path/to/repo -m gpt-6-luna --sandbox read-only \
  --output-last-message /path/to/result.txt - < /path/to/brief.txt
```

For authorized implementation use a separate worktree and `--sandbox workspace-write`.
Use the host's normal approval and hook settings. Do not bypass sandboxing, hook
trust or approvals to make a bridge test pass. Keep brief/result files outside
the repo and private. Capture the exit status; bound runtime and stop or escalate
on failure rather than spawning duplicate workers. Resume the same worker when
appropriate using the installed CLI's documented resume command.

Read the result, inspect the diff, and run the affected check from the parent.
An exit code or worker claim is insufficient proof. Return the outcome, evidence,
uncertainties and observed usage if available. Do not claim a tested bridge until
a harmless real turn has completed on this host.

Sources: [Non-interactive Codex](https://learn.chatgpt.com/docs/noninteractive),
[Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Plan usage](https://learn.chatgpt.com/docs/pricing).
