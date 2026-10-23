"""Shared synthetic GitHub observations; no policy authority."""
def contract_observation(metadata, live):
    target = dict(oid=live, __typename='Commit')
    pr = dict(metadata, baseRefName='cursor/storypool-all-in-one-47c7',
              baseRef=dict(target=dict(target)))
    return {'data': {'repository': {'ref': {'target': dict(target)}, 'pullRequest': pr}}}
