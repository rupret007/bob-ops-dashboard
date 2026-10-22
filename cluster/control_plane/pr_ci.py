"""Conservative interpretation of GitHub's current PR CI rollup."""


def ci_status_from_pr(pr):
    """Keep the worst result for a name; check names are not unique identities.

    GitHub can report a check run and commit status (or different workflows)
    with the same name. The rollup lacks enough identity information to infer
    supersession from list order. Missing or malformed evidence cannot be green.
    """
    checks = pr.get('statusCheckRollup')
    if checks is None:
        return {}
    if not isinstance(checks, list):
        return {'unknown': 'pending'}
    result = {}
    severity = {'success': 0, 'pending': 1, 'failure': 2}
    for check in checks:
        name, state = 'unknown', 'pending'
        if isinstance(check, dict):
            candidate = check.get('name') or check.get('context')
            if isinstance(candidate, str) and candidate.strip():
                name = candidate
                raw = check.get('conclusion') or check.get('state') or 'pending'
                raw = raw.lower() if isinstance(raw, str) else 'pending'
                if raw in ('failure', 'error', 'timed_out', 'cancelled',
                           'action_required', 'startup_failure'):
                    state = 'failure'
                elif raw in ('success', 'neutral', 'skipped'):
                    # An explicit unfinished run cannot inherit a stale conclusion.
                    status = check.get('status', 'COMPLETED')
                    if isinstance(status, str) and status.upper() == 'COMPLETED':
                        state = 'success'
        previous = result.get(name, 'success')
        result[name] = max((previous, state), key=severity.__getitem__)
    return result
