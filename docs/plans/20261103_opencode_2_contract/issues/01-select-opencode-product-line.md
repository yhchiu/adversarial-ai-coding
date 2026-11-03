# 01: Select the OpenCode product line at startup

**What to build:** A run that will invoke the built-in `opencode` command reads the installed major version once at startup and speaks that product line for the rest of the process. OpenCode 1 keeps the call it has today. A version with no major number is refused before any paid stage. A run that never invokes `opencode` does not need the binary.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A run whose slots never use the built-in `opencode` command does not read a version.
- [ ] When slot A, slot B, or an independent implementation slot uses `opencode`, the major version is read once during startup. The major version is the first integer in the version output.
- [ ] The version is not stored in the run snapshot. The next process, including one started with `RESUME_RUN`, reads it again.
- [ ] Output with no major number, including a development build that prints `local`, fails startup and says the version could not be read. AAC does not guess a contract.
- [ ] Major version 1 keeps a positional prompt, a closed empty stdin, and a worker Resume that passes `--session` with no existence check.
- [ ] The startup failure happens before a paid stage and uses the existing settings-error locale path.
- [ ] Both READMEs say the installed major version chooses the contract.
- [ ] Tests drive startup validation and the worker and reviewer calls with a fake `opencode`. They do not need a live binary.
