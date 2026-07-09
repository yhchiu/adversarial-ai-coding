You modified protected acceptance test files, which the workflow rules forbid:
{{VIOLATIONS}}
Restore these files exactly to commit {{BASE}}, for example with git checkout {{BASE}} -- <file>, and commit that restoration. If you believe a test is wrong, record the objection in the Assumptions and Open Questions section of {{SPEC_FILE}}, but do not modify the test file.
