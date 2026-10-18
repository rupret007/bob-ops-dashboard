# StoryPool control plane

The conductor snapshots the PR backlog. The dispatcher applies machine and
provider guards, excludes never-touch repositories, requires local Karen findings
at the exact PR head, and creates a leased job in the locked JSONL ledger.

On Linux the dispatcher submits each fix worker as an independent user transient
service (`storypool-job-<id>`, Type=exec, collected on exit). Its working directory
and Python module path point at the installed repository; output goes to the job
log. Submission errors fail dispatch without falling back to a child in the
oneshot dispatch cgroup. No systemd-run helper PID is recorded as a worker PID.
On macOS the worker starts in a new session and the dispatch launchd plist sets
AbandonProcessGroup so it can outlive dispatch. The worker records its own PID and
owns ledger transitions, runs tests, pushes only the PR branch, verifies the
remote SHA, and requests Karen review. Only the Karen gate can merge.

Each dispatch tick reconciles running PIDs before allocating lanes. A missing
worker becomes failed with error `worker-died`; queued leases expire separately.
A successful process exit alone is not a verified fix. The dashboard reads state
and logs without mutating jobs. Watchdog diagnoses health; self-update deploys
pushed branch commits outside the monitor sandbox. Peer health and complete
provider preflight coverage remain outstanding; unknown health is not success.

The regression suite checks Linux transient service submission, failure without
unsafe fallback, macOS process-group preservation, dead-PID reconciliation, and
Python 3.9 compatibility. Real service survival on each host must also be verified
from post-deployment jobs; unit tests do not establish deployment health.
