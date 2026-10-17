# Replicating the full StoryPool setup

## Summary

StoryPool should let another person reproduce Jeff's **entire assistant setup**: a worker, personal chat assistant, Andrea-style messaging bridge, email-drafting assistant, and private dashboards, with optional participation in a shared job queue and donated compute pool. Replication means installing the capabilities with that person's identity and data; it never means copying Jeff's accounts, history, contacts, credentials, or send permissions.

This is a research/design proposal, not an implemented installer or a claim that multi-person operation is ready. Research checked 2026-10-17. The existing `cluster/LANES.md` describes stage-1 lane infrastructure; it is not a personal-assistant installation contract. The open **HIGH LeaseService request/reply bug** is a release blocker for the replicated design. Use **publish-only subjects** as specified below; do not copy the current request/reply flow into a new install.

Non-negotiable boundaries: each person's private data and credentials remain on their own machine; no participant can see another's mail, texts, or keys; every outbound communication needs that person's approval for that exact send. Grok Bot is a **CONSERVE-ONLY** tracked budget that lanes never consume.

## Install kit

Deliver a versioned manifest, platform prerequisites, local configuration wizard, health checks, update/rollback instructions, and uninstall/revocation instructions. Each component must have its own identity and minimum permissions. Defaults are private, donation off, and sending disabled until a single send is approved.

| Component per person | Required behavior | Completion evidence |
| --- | --- | --- |
| Worker | Local execution sandbox, resource limits, pause switch, queue adapter; friends' (other people's) jobs run in a rootless podman sandbox with **no** keys or credentials available; donated jobs isolated from personal services | Synthetic job completes and cancellation works without accessing private files |
| Chat assistant | Private conversations, memory, retrieval, and model selection; local model or person's own approved API access | Owner can chat; another tenant cannot retrieve history or memory |
| Andrea-style messaging bridge | Owner's messaging identity, local inbound context, draft preview, approval-gated send adapter | Synthetic draft reaches review; unapproved send is rejected |
| Email-drafting assistant | Owner-authorized mailbox integration, minimum scopes, private draft store; send capability behind the local approval gate | Draft can be reviewed without sending; wrong-owner approval fails |
| Dashboards | Private assistant/inbox/draft/approval view; separate sanitized pool capacity and queue view | Private routes, caches, exports, and browser sessions pass two-person isolation checks |

“Andrea-style” describes the bridge capability, not permission to reuse Andrea's identity or conversations. Jeff still needs to inventory the exact components and versions behind his current setup. Ship reproducible templates with placeholders, never a disk image or home-directory copy. A missing platform adapter must display unavailable rather than silently route through Jeff's machine.

## Isolation model

Use a separate private service boundary per person: OS account or dedicated machine, local storage and search indexes, encryption keys, browser session, and backup policy. Friends' (other people's) donated jobs run in a rootless podman sandbox with **no** keys or credentials available and no mounts of the assistant's data or secret store. Other donated work uses a separate sandbox with the same no-mount rule. Containers alone do not protect private data from the machine's administrator; a dedicated trusted machine is the stronger option.

Joining the shared pool is via a **Tailscale share** of the queue node, with an ACL limited to **port 4222 (NATS) only**—no SSH, dashboards, or other ports on the operator's network.

At the shared broker, assign separate NATS accounts to tenants and a separate pool account. NATS accounts provide isolated subject spaces; cross-account sharing requires explicit exports/imports. Subject prefixes alone are not the tenant boundary. Apply narrow per-component publish/subscribe permissions and per-tenant storage/connection quotas. Never give members system/operator identities or unrestricted wildcards. These choices build on [NATS accounts](https://docs.nats.io/learn/security/accounts-and-multitenancy) and [subject authorization](https://docs.nats.io/learn/security/authorization).

Keep mail, texts, contact names, assistant memory, drafts, and credentials off the shared broker entirely. A broker administrator can control infrastructure; account isolation is not encryption against that administrator. Only explicitly shareable job inputs and sanitized operational events cross into the pool. Authenticate dashboard/API access on the server for every object; do not trust a caller-supplied tenant ID or rely on UI filtering. Private logs, backups, caches, and error traces follow the same boundary.

**LeaseService HIGH blocker:** the existing service in `cluster/lanes.py` handles a caller-provided reply subject. The replication design must use publish-only lease command subjects with no NATS reply field, no request API, and no dynamic response grants. Proposed command shape: `storypool.lease.<tenant>.<worker>.<op>`; proposed status events: `storypool.lease_status.<tenant>.<worker>`. These are proposed names, not existing APIs. The hub derives identity from authenticated account/allowed subject bindings, validates the operation, and publishes status only to its preconfigured tenant/worker destination. A correlation ID matches an operation; it cannot select a response destination. Members publish only their own commands and subscribe only to their own status events. Do not export a request/reply service across accounts.

The hub alone mutates lease state. Preserve compare-and-set, expiry, fencing, deduplication, and owner binding. Publish-only transport does not by itself guarantee delivery: require operation IDs, bounded retries, duplicate handling, and an authoritative grant before any work starts. Missing or stale status means stop. Before rollout, prove that forged tenant/worker identities, reply headers, expired grants, duplicate commands, and other tenants' status subscriptions fail safely. This document does not fix or close the bug.

## Credentials

Each person signs in and provisions credentials **on their own machine**. Store API keys, OAuth refresh tokens, messaging sessions, NATS private keys, and approval-signing material in that person's OS secret store or equivalently protected local storage. Authentication may present a credential to its intended provider over an encrypted connection; StoryPool never uploads it to a central vault, dispatcher, donor, repository, dashboard, or support bundle.

Give each component a separate scoped credential where supported. The shared registration record contains public identity, capabilities, and revocation metadata only. A donor's API key stays on the donor's own machine and is accessed only by a restricted local adapter; arbitrary job code must not receive it or be able to read its environment. Record provider, permitted workload, spending cap, and expiry without recording key values. Rotation and revocation are performed by the owner. Lost-machine recovery requires local reauthentication, not downloading someone's saved session from the pool. Backups must preserve the same owner-only boundary.

## Send approval

All outbound email, texts, chat messages, forwards, replies, and attachments require the **sending person's own explicit approval for each send**. No standing sends, recurring approvals, trusted-recipient bypass, blanket batch permission, or approval by Jeff on another person's behalf. Connecting an account, asking for a draft, donating compute, or approving a job never authorizes sending.

The local gate shows the sending account, channel, complete recipients, final body, and attachments. Bind a short-lived, single-use approval to the owner identity and exact payload digest. Any edit or recipient change invalidates it. The local send adapter verifies and consumes approval atomically; workers, models, inbox content, and shared administrators cannot mint it. Maintain an owner-private audit record of review and outcome. If delivery is ambiguous after a timeout, reconcile status before retrying and obtain fresh approval for any new send attempt; never blindly replay. Revocation, expired approval, wrong tenant, and unavailable approval service all fail closed.

Internal queue/status publication is transport, not permission to contact another person. Any adapter capable of externally visible communication must pass the send gate, including future integrations.

## Shared vs private

| Shared, by explicit opt-in | Always private to each person |
| --- | --- |
| Queue scheduling, opaque job/worker identifiers, resource requirements, sanitized status | Assistant chat, memory, retrieval indexes, inboxes, texts, contacts, drafts, and approval records |
| Donated CPU/GPU time, local-model capacity, donor-operated API execution | API keys, OAuth tokens, Apple sessions, private keys, and local credential stores |
| Approved shareable input and output artifacts; aggregate capacity/health | Private dashboard routes, logs, backups, and personal filesystem |

Donors can inspect plaintext processed on their machines. Therefore private assistant, email, and messaging tasks stay on the owner's machine and never enter the donated pool. Removing obvious identifiers is not proof that a private document is shareable. Cloud inference for personal work requires that person's explicit data-processing choice and their own provider account; local inference is the option for keeping content on-device. Donated output is untrusted and cannot authorize tools, unlock secrets, or approve sends. Donors choose workload classes, concurrency, time windows, cost limits, and can stop immediately.

## Onboarding

1. Inventory the person's required capabilities and hardware. Explain which components are available, platform-dependent, or still proposed; select a local model or personally owned API account.
2. Install the pinned kit on their machine, create their local identity/storage, and leave outbound sends and donation disabled. Verify package integrity and provide rollback instructions.
3. Let the person authenticate locally to their own mail and messaging accounts. Request only the needed permissions and explain which content an optional cloud provider will receive. Never import Jeff's sessions.
4. Create scoped component identities and a distinct broker account if joining the pool. Exchange public registration data only; verify server identity and encrypted transport.
5. Complete synthetic chat, email draft, message draft, and dashboard checks. Demonstrate one rejected unapproved send and a simulated single-use approval; setup must not send real messages.
6. Run two-person isolation tests against APIs, broker subscriptions, storage, logs, backups, and dashboard sessions. Confirm a donated worker cannot read local credentials or private context.
7. Optionally enroll donation with workload and resource limits. Resolve the LeaseService blocker before connecting the new lease design. Start with synthetic/public jobs and prove revocation and restart recovery.
8. Hand over local pause, revoke, export/delete, update, and recovery controls. The person performs any later real send only after inspecting and approving that exact message.

## Platform limits

For this kit's Apple Messages bridge, **iMessage requires a Mac** running the local bridge and signed in to the person's Apple Account. This is a bridge-host requirement, not a claim that iMessage itself exists only on Macs. Apple documents the Mac setup in [Set up Messages on Mac](https://support.apple.com/guide/messages/set-up-messages-ichte16154fb/mac). A Linux or Windows worker is not a substitute Apple Messages endpoint.

SMS/MMS/RCS forwarding depends on the person's iPhone and Apple configuration, including the same Apple Account and the connectivity/setup conditions Apple specifies. See [Text Message Forwarding](https://support.apple.com/en-us/102545). Do not promise identical channel support on all machines. These Apple user guides do not establish a supported general-purpose bot API: bridge implementation, macOS permissions, OS-update compatibility, and reliable send-result detection remain validation work. Do not read `chat.db` as part of this research or onboarding validation.

Email providers differ in OAuth scopes, administrator consent, rate limits, and draft/send APIs; validate each adapter before advertising support. Sleeping/offline machines cannot provide reliable always-on bridges or donated capacity. Local models need sufficient memory/storage and a compatible runtime. Private services must remain useful when the shared pool is offline, with explicit unavailable/stale states instead of fallback to another person's account.

## Provider-terms risks

Subscription capacity is not automatically transferable. [Anthropic consumer terms](https://www.anthropic.com/legal/consumer-terms) prohibit account/credential sharing and restrict automated access except as permitted; [Grok consumer terms](https://x.ai/legal/terms-of-service) likewise prohibit sharing credentials or making an account available to others. A proxy that keeps the password local can still amount to sharing account access. Do not pool consumer subscriptions, cookies, browser sessions, or subscription-backed automation simply because the owner offers spare capacity.

Safe donation **types for this architecture**, subject to their actual licenses/contracts, are:

- Donor-owned API access executed on the donor's own machine, with the key never transferred and jobs limited by a local adapter and budget.
- Local models whose licenses permit the intended workload and sharing arrangement.
- CPU/GPU compute for approved shareable tasks, without access to personal subscriptions or data.

These categories are not blanket provider approval. [Anthropic commercial terms](https://www.anthropic.com/legal/commercial-terms) permit powering customer-facing products but include restrictions, including on resale. [xAI enterprise terms](https://x.ai/legal/terms-of-service-enterprise) govern API/business use separately from consumer access. Check the applicable account agreement, permitted users, redistribution/resale, automation, regions, workload, and rate limits before enabling a provider. Disable an integration whose permission is unclear; use local compute or a reviewed API arrangement instead. **Grok Bot terms are unverified** (the `x.ai/legal/grok-bot-terms` URL returned HTTP 403 during this research and was not opened).

## Grok Bot budget

**CONSERVE-ONLY. Lanes never consume Grok Bot budget.** No lane routing, fallback, retry, health probe, benchmarking, or usage discovery may invoke Grok Bot. API donation capacity and Grok Bot entitlement are separate resources. Jeff's own use, if any, remains outside lane scheduling and subject to his own approvals.

No authoritative Grok Bot usage source was found in the inspected repository. Agent presence/status is not usage accounting. This research did not access billing accounts or invoke the bot, and does not establish whether a provider-side usage source exists.

| Budget field | Current evidence |
| --- | --- |
| Policy | CONSERVE-ONLY; lane consumption prohibited |
| Verified usage source | unknown — no source found in inspected repository |
| Allowance and units | unknown |
| Used / remaining | unknown / unknown |
| Reset period / last measured timestamp | unknown / unknown |

The eventual read-only budget record must include account owner, source, unit, reporting window, measured timestamp, allowance, usage, and remaining amount only when supported by that source. Distinguish missing, stale, and measured data. Without a verified source, show **unknown**, never zero, a fabricated estimate, or an inferred balance from subscription price. A future usage import must not itself spend Bot budget; budget visibility never grants lanes permission to consume it.

## Milestones

| Milestone | Exit evidence |
| --- | --- |
| 1. Inventory and design | Jeff confirms all five install-kit components, supported platforms, data map, and owners; gaps explicitly tracked |
| 2. Lease safety and tenant boundary | HIGH bug resolved in a separate implementation change; publish-only protocol passes adversarial tenant/worker, reply-header, duplicate, expiry, and disconnect tests |
| 3. Local private kit | A second person installs chat, drafts, bridge where supported, and private dashboards using only their own accounts; no real sends during setup |
| 4. Approval and isolation proof | Two-person tests show no cross-tenant reads or sends; changed payloads, replay, retries, and revoked approvals fail safely |
| 5. Donation pilot | Public/synthetic jobs only; credential isolation, resource limits, stop/revoke, and provider-terms review demonstrated; no Grok Bot consumption |
| 6. Repeatable replication | Another clean install succeeds from the written kit; update/rollback/recovery verified; dashboards expose truthful capability and budget states |

No milestone completion is claimed by this documentation. Pool rollout is gated on lease safety and isolation; lack of a Grok Bot usage source must remain visible as unknown and must never enable consumption.

## Open decisions for Jeff

- What exact applications, versions, bridge behavior, and dashboard capabilities define parity with his entire current setup?
- Which platforms and email providers are supported first, and what reduced capability is acceptable without a Mac/iPhone?
- Who operates the shared broker, issues/revokes public identities, and verifies tenant-isolation evidence without accessing private data?
- Who owns the HIGH LeaseService lease-reply fix ([bob-ops-dashboard #76](https://github.com/rupret007/bob-ops-dashboard/pull/76)) and the publish-only protocol review? The bug stays **open (HIGH)** until #76 merges; what evidence will establish closure?
- Which workloads are explicitly shareable, which donor/API arrangements have been reviewed, and who sets resource/spending ceilings?
- Which local approval UX and identity verification will each send adapter use? How will ambiguous delivery be reconciled?
- Is there an authoritative, read-only Grok Bot usage source, who may read it, and what units/window does it report? Until answered, keep all quantities unknown and all lanes excluded.
- Who maintains installer releases, platform compatibility, local recovery instructions, and support with redacted diagnostics?

## Sources

Only provider/documentation URLs opened during this research are listed. Terms and platform support can change; recheck the applicable agreement before implementation.

- [NATS: Accounts and multitenancy](https://docs.nats.io/learn/security/accounts-and-multitenancy)
- [NATS: Authorization](https://docs.nats.io/learn/security/authorization)
- [Apple: Set up Messages on Mac](https://support.apple.com/guide/messages/set-up-messages-ichte16154fb/mac)
- [Apple: Forward text messages from your iPhone](https://support.apple.com/en-us/102545)
- [Anthropic: Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms)
- [Anthropic: Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms)
- [xAI: Consumer Terms of Service](https://x.ai/legal/terms-of-service)
- [xAI: Enterprise Terms of Service](https://x.ai/legal/terms-of-service-enterprise)

Repository evidence inspected: `cluster/LANES.md`, `cluster/lanes.py`, `cluster/lanes_perms.py`, and the Grok status probe in `probe-agents-status.sh`. The HIGH lease-reply bug is tracked in [bob-ops-dashboard #76](https://github.com/rupret007/bob-ops-dashboard/pull/76) and remains **open (HIGH)** until that PR merges. Grok Bot terms are unverified (see Provider-terms risks).
