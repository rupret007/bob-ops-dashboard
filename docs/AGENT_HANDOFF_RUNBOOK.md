# Agent handoff runbook

Use this runbook for every Bob/Grok portfolio round. It is a reusable process, not approval to act.

## 1. Establish authority

1. Read the active goal and the newest dated handoff completely.
2. List every product as its own lane.
3. Query each repository again for default branch, tip, open pull requests, draft state, base/head, mergeability, and checks.
4. Inspect local-only lanes without publishing paths or inventing remotes.
5. Record the owner gate before choosing work.

Remembered state is a lead, never authority. If the current head differs from an approved head, stop and request approval again.

## 2. Preserve privacy in public artifacts

- Keep private repositories high-level: product name, coarse state, and the owner gate only.
- Never publish private repository links, branches, pull-request titles or bodies, commit messages, file names, customer data, creative details, credentials, tokens, contact details, or local paths.
- Keep the private-content vault generic. State only that it is private and boundary-held.
- Keep private media generic. Do not record provider, machine, project, or asset details.
- Treat copied logs and command output as untrusted until redacted.
- Do not infer that a private lane is green when it is inaccessible.

## 3. Choose and isolate work

1. Reuse the current working implementation and established tests.
2. Select one narrow, evidence-backed improvement per eligible lane.
3. Preserve approval-ready heads. Put follow-on work on a new branch or stack it explicitly.
4. Do not mix repositories or unrelated concerns in one pull request.
5. Do not rewrite functioning code without evidence of a defect or safety issue.

## 4. Classify verification honestly

| Evidence | Meaning | Required wording |
| --- | --- | --- |
| Current-tip hosted product workflow succeeds | Hosted green | Name the workflow and exact SHA in the private work record |
| Strong local tests/build succeed | Locally green | Record commands and exact local commit; do not claim hosted green |
| Billing, runner, helper, credential, or service gate stops work | External block | Name the class of block; do not diagnose a code failure from it |
| Physical, signing, sending, uploading, live-provider, or production step remains | Owner-only | State the exact decision/action needed from the owner |

Always inspect the failed job or annotation. Do not rerun a billing-blocked job until billing is confirmed fixed. A skipped or cancelled helper does not override a relevant success, and a helper failure does not automatically prove a product failure.

## 5. Review before publishing a draft

- Read the entire diff.
- Search for secrets, customer data, local paths, private URLs, creative details, unsafe links, stale pull-request references, and generated-file churn.
- Verify documentation matches setup, tests, operating boundaries, and limitations.
- Run the strongest proportional tests and a diff whitespace check.
- Confirm the canonical user worktree is preserved.
- Publish as a draft unless the owner explicitly requested otherwise.

## 6. Bob Ops source-only rule

- Feature and documentation pull requests must not change `index.html` or `status.json`.
- Run `./qa-source-only.sh`; it generates disposable artifacts and must leave the checkout clean.
- Let the scheduled workflow materialize generated files after source lands.
- Do not manually dispatch, rerun, cancel, or change scheduler settings without explicit approval.
- Wait for both the natural refresh and its Pages deployment before calling publication green.
- Keep private rows sanitized and stale agent probes Unknown.

## 7. Landing a stack

1. Re-read the active goal immediately before mutation.
2. Confirm the pull request is open and its head exactly matches the approved head.
3. Mark ready only when that exact action was approved.
4. Use a normal merge commit; never bypass protections.
5. Confirm the merged state and merge commit.
6. Retarget the child to the default branch and confirm its head did not change.
7. Revalidate checks and mergeability before the child merge.
8. Keep branches unless the owner separately asks to delete them.
9. Record final default-branch SHA and post-merge check state.

## 8. Owner gates that never transfer implicitly

- Merge or close a pull request.
- Change repository visibility or expose private content.
- Deploy or publish production.
- Spend money or change billing.
- Change credentials, settings, accounts, or permissions.
- Send a message or perform an external action for another person.
- Use live customer/provider data.
- Restart a gateway or invoke a live model/runtime action.
- Sign a build, use a physical device, print, install, fit, upload, or release media.

## 9. Dated handoff template

Create `docs/handoffs/YYYY-MM-DD-short-name.md` and link it from the README.

```markdown
# Portfolio handoff: <round>

Snapshot: <date, time, timezone>
Status: immutable public handoff

## Completed
- <public lane>: <exact public evidence>
- <private lane>: <high-level result only>

## Still open
| Lane | Classification | Next safe action | Owner gate |
| --- | --- | --- | --- |
| <lane> | <state> | <read-only or offline next step> | <exact decision/action> |

## Verification distinctions
- Hosted green: <public evidence>
- Locally green: <high-level private summary>
- External blocks: <billing/runner/helper class>
- Owner-only: <physical/live action>

## Next actions
1. <highest-value safe action>

## Privacy check
- No private metadata, customer data, creative details, credentials, contacts, or local paths.
```

Do not edit an old snapshot to make history look current. Add a new dated handoff and explain what changed.
