Owner runtime containment candidate — NOT QUALIFIED FOR EXECUTION

Production admission/execution latch remains hard-disabled. No wiring or deployment
is authorized by this candidate. Existing StoryPool fix jobs are unchanged.

Linux owner commands now require STORYPOOL_OWNER_CGROUP_ROOT to name an
operator-delegated private domain below /sys/fs/cgroup, with cgroup.kill support.
The child joins before exec; every completion (including successful leader exit)
kills the whole cgroup and waits for populated=0 before any followup can run.
macOS, missing delegation, spawn failures and teardown uncertainty fail closed.

The authority's separate OS sandbox MUST hide cgroupfs and prevent payload
migration to another cgroup. This lifecycle primitive alone is not a credential,
filesystem or network sandbox and does not attest the authority contract.

Run the real ordinary-child plus setsid-child regression with:

    STORYPOOL_OWNER_CGROUP_ROOT=/sys/fs/cgroup/OPERATOR_PRIVATE_DELEGATION \
      python3 -m cluster.control_plane.qualification.owner_descendant_containment

Missing delegation fails this command; it never reports a skipped qualification.
This sandbox has read-only cgroupfs and no writable delegated domain, so live
kernel teardown remains UNPROVEN. The original unguarded process-group repro
still fails, as expected for unchanged non-owner commands. Refusal/unit tests
passing do not establish actual descendant containment. Keep execution disabled
until the live program passes under the real credential-free OS sandbox, including
proof that payload cgroup migration and credential access are denied.
