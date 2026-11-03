# Adversarial AI Coding

The workflow that runs two agent slots against one codebase until the work passes its gates.

## Language

**OpenCode 1**:
The OpenCode product line identified by major version 1 of the `opencode` command.
_Avoid_: opencode v1, the v1 adapter

**OpenCode 2**:
The OpenCode product line identified by major version 2 of the `opencode` command. Installing it replaces the `opencode` command; it does not sit beside OpenCode 1.
_Avoid_: v2, opencode v2

**Fresh**:
A call sent without an existing conversation id.
_Avoid_: new session, restart

**Resume**:
A worker call that continues the one active worker conversation captured in this process. It counts only when that conversation is known to still exist. If that cannot be told, the call fails and does not become fresh.
_Avoid_: continue, reconnect
