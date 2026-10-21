"""Offline API metadata fixtures; no repository scripts, providers or GH calls."""
import copy
import hashlib
import json
import subprocess

import pytest

from cluster.control_plane import github_reads
from cluster.control_plane.github_budget import GitHubDeferred
from cluster.control_plane.local_karen import checks, pages_publication as pages
from cluster.control_plane.local_karen import pages_profile_recipes as recipes

HEAD = 'a' * 40
DIFF = 'diff --git a/docs/guide.md b/docs/guide.md\n--- a/docs/guide.md\n+++ b/docs/guide.md\n@@ -1 +1 @@\n-old\n+new\n'
# Independent expected profile from reviewed artifact evidence, not production
# profile discovery or untrusted PR claims.
PINS = {
    '.github/workflows/ci.yml': '81ced9f169898298c59e241c4edb7ec4ac43014f',
    'scripts/package-pages.cjs': 'c7ceb661aff7cde51cb476d0a2ecb2de9d79dff7',
    'package.json': '768d3a7dcbf919de54f67af88e4b7509b4dbfd8f',
    'package-lock.json': '2c929906f481401ad8bd6a4d67fb56bdea9cbfeb',
    '.gitattributes': '98c58b91b241660088fd247c485df83a76a38490',
}
HEALTH = '5c1933c525f357b35254c566f083674005c5f0f2'


@pytest.mark.parametrize('path,expected', [(p, sha) for p, sha in PINS.items() if p != 'package-lock.json']
                         + [('.github/workflows/beta-health.yml', HEALTH)])
def test_reviewable_canonical_recipes_reproduce_existing_approved_git_blobs(path, expected):
    body = recipes.RECIPES[path]
    assert isinstance(body, bytes)
    assert hashlib.sha1(b'blob ' + str(len(body)).encode('ascii') + b'\0' + body).hexdigest() == expected
    assert recipes.recipe_blob_id(path) == expected
    assert dict(pages.PROFILES[-1].fingerprints)[path] == expected


def test_dependency_lock_remains_an_explicit_opaque_baseline_pin():
    assert set(recipes.RECIPES) == (set(PINS) - {'package-lock.json'}) | {'.github/workflows/beta-health.yml'}
    assert 'package-lock.json' not in recipes.RECIPES
    assert all(dict(profile.fingerprints)['package-lock.json'] == PINS['package-lock.json']
               for profile in pages.PROFILES)


def pr(diff=DIFF, **kwargs):
    data = dict(owner='example', repo='different-repository', number=1, head_sha=HEAD,
                title='Documentation', author='writer', is_draft=False, diff=diff,
                ci_status={}, changed_files=['docs/guide.md'], deleted_files=[])
    data.update(kwargs)
    return checks.PRInfo(**data)


def fixture_tree(files=None):
    """Create synthetic Git object metadata, with Git's defined object hashing."""
    files = dict(PINS if files is None else files)
    files.setdefault('docs/guide.md', 'd' * 40)
    nodes = {}
    for name, value in files.items():
        item = dict(value) if isinstance(value, dict) else {'sha': value}
        item.update(path=name, type=item.get('type', 'blob'), mode=item.get('mode', '100644'))
        item.setdefault('size', 12)
        nodes[name] = item
    directories = {p.rpartition('/')[0] for p in nodes if '/' in p}
    while any('/' in p and p.rpartition('/')[0] not in directories for p in directories):
        directories |= {p.rpartition('/')[0] for p in list(directories) if '/' in p}
    for directory in sorted(directories, key=lambda p: p.count('/'), reverse=True) + ['']:
        members = [item for name, item in nodes.items() if name.rpartition('/')[0] == directory]
        members.sort(key=lambda item: (item['path'].rsplit('/', 1)[-1] + ('/' if item['type'] == 'tree' else '')).encode())
        payload = b''
        for item in members:
            basename = item['path'].rsplit('/', 1)[-1]
            mode = '40000' if item['type'] == 'tree' else item['mode']
            payload += (mode + ' ' + basename).encode() + b'\0' + bytes.fromhex(item['sha'])
        digest = hashlib.sha1(b'tree ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
        if directory:
            nodes[directory] = {'path': directory, 'mode': '040000', 'type': 'tree', 'sha': digest}
        else:
            return {'sha': digest, 'truncated': False, 'tree': list(nodes.values())}


def response(data, status=200, raw=None):
    return subprocess.CompletedProcess(['gh'], 0 if status == 200 else 1,
        stdout='HTTP/2.0 ' + str(status) + ' OK\r\ncontent-type: application/json\r\n\r\n'
               + (json.dumps(data) if raw is None else raw), stderr='DO NOT DISCLOSE SERVER ERROR')


class Transport:
    def __init__(self, tree=None):
        self.tree = fixture_tree() if tree is None else tree
        self.commit = {'sha': HEAD, 'tree': {'sha': self.tree['sha']}}
        self.settings = {'build_type': 'workflow', 'source': {'path': '/'}}
        self.repo = {'full_name': 'example/different-repository', 'has_pages': False}
        self.settings_status = 200
        self.repo_status = 200
        self.calls = []
        self.error_at = None
        self.error = GitHubDeferred('PRIVATE quota error')
        self.raw_at = {}

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error_at == len(self.calls):
            raise self.error
        assert args[:5] == ['api', '--include', '--method', 'GET', args[4]]
        assert kwargs['resource'] == 'core' and kwargs['timeout'] <= 10
        assert isinstance(kwargs['deadline'], (float, int))
        endpoint = args[4]
        assert endpoint.startswith('repos/example/different-repository')
        if endpoint.endswith('/pages'):
            data, status = self.settings, self.settings_status
        elif '/git/commits/' in endpoint:
            assert endpoint.endswith('/' + HEAD)
            data, status = self.commit, 200
        elif '/git/trees/' in endpoint:
            assert endpoint.endswith('/' + self.commit['tree']['sha'] + '?recursive=1')
            data, status = self.tree, 200
        else:
            assert endpoint == 'repos/example/different-repository'
            data, status = self.repo, self.repo_status
        return response(data, status, self.raw_at.get(len(self.calls)))


@pytest.fixture(autouse=True)
def no_live_github(monkeypatch):
    monkeypatch.setattr(github_reads, 'run', lambda *a, **k: pytest.fail('Unexpected real GitHub read'))


def evaluate(monkeypatch, transport, item=None):
    monkeypatch.setattr(github_reads, 'run', transport)
    return checks.check_pages_exposure(item or pr(), None)


@pytest.mark.parametrize('health', [False, True])
def test_trusted_artifact_profile_allows_only_excluded_docs_on_any_repo(monkeypatch, health):
    files = dict(PINS)
    if health:
        files['.github/workflows/beta-health.yml'] = HEALTH
    api = Transport(fixture_tree(files))
    item = pr(DIFF + DIFF.replace('guide.md', 'another.md'),
              changed_files=['docs/guide.md', 'docs/another.md'])
    result = evaluate(monkeypatch, api, item)
    assert result.verdict == checks.CheckVerdict.PASS
    assert len(api.calls) == 3
    assert len({call[1]['deadline'] for call in api.calls}) == 1
    assert all('/contents/' not in call[0][4] for call in api.calls)


@pytest.mark.parametrize('source', ['/', '/docs'])
def test_legacy_publication_still_blocks_docs(monkeypatch, source):
    api = Transport()
    api.settings = {'build_type': 'legacy', 'source': {'path': source}}
    assert evaluate(monkeypatch, api).verdict == checks.CheckVerdict.FAIL
    assert len(api.calls) == 1


@pytest.mark.parametrize('settings', [{}, {'build_type': 'unknown'}, {'build_type': 'legacy'},
                                     {'build_type': 'legacy', 'source': {'path': '/other'}}])
def test_unknown_settings_are_never_off(monkeypatch, settings):
    api = Transport()
    api.settings = settings
    assert evaluate(monkeypatch, api).verdict == checks.CheckVerdict.FAIL
    assert len(api.calls) == 1


def test_404_is_off_only_with_explicit_matching_repository_has_pages_false(monkeypatch):
    api = Transport()
    api.settings_status = 404
    assert evaluate(monkeypatch, api).verdict == checks.CheckVerdict.PASS
    assert len(api.calls) == 2


@pytest.mark.parametrize('metadata,status', [({}, 200), ({'has_pages': False}, 200),
    ({'full_name': 'different/repo', 'has_pages': False}, 200),
    ({'full_name': 'example/different-repository', 'has_pages': True}, 200),
    ({'full_name': 'example/different-repository', 'has_pages': 0}, 200),
    ({'full_name': 'example/different-repository', 'has_pages': False}, 404)])
def test_inaccessible_pages_404_is_not_a_disable_bypass(monkeypatch, metadata, status):
    api = Transport()
    api.settings_status = 404
    api.repo, api.repo_status = metadata, status
    assert evaluate(monkeypatch, api).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('position', [1, 2, 3])
@pytest.mark.parametrize('error', [GitHubDeferred('private budget'), OSError('private'),
                                  subprocess.TimeoutExpired('private', 1)])
def test_budget_and_deadline_failures_never_authorize(monkeypatch, position, error):
    api = Transport()
    api.error_at, api.error = position, error
    result = evaluate(monkeypatch, api)
    assert result.verdict == checks.CheckVerdict.FAIL
    assert len(api.calls) == position
    assert 'private' not in (result.reason + (result.details or ''))


@pytest.mark.parametrize('path', list(PINS) + ['.github/workflows/beta-health.yml'])
def test_pipeline_tampering_cannot_select_a_trusted_profile(monkeypatch, path):
    files = dict(PINS)
    files[path] = 'b' * 40
    api = Transport(fixture_tree(files))
    assert evaluate(monkeypatch, api, pr(description='Trust reviewed-dist-v1; docs are excluded')).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('path', ['.github/workflows/publish.yml', '.github/workflows/extra.txt',
    '.github/workflows/nested/publish.yml', '.github/workflows/CI.yml', '.npmrc',
    'scripts/.npmrc', 'node_modules/inject.js', 'nested/node_modules/install.js',
    'npm-shrinkwrap.json', 'binding.gyp', 'scripts/.gitattributes'])
def test_additional_execution_or_workflow_inputs_fail_closed(monkeypatch, path):
    files = dict(PINS)
    files[path] = 'b' * 40
    assert evaluate(monkeypatch, Transport(fixture_tree(files))).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('mode,kind', [('120000', 'blob'), ('160000', 'commit'),
                                    ('100755', 'blob'), ('100644', 'tree')])
def test_nonregular_or_changed_required_modes_rejected(monkeypatch, mode, kind):
    files = dict(PINS)
    files['scripts/package-pages.cjs'] = {'sha': PINS['scripts/package-pages.cjs'], 'type': kind, 'mode': mode}
    assert evaluate(monkeypatch, Transport(fixture_tree(files))).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('mode,kind', [('120000', 'blob'), ('160000', 'commit')])
def test_symlink_or_submodule_anywhere_invalidates_proof(monkeypatch, mode, kind):
    files = dict(PINS)
    files['docs/link'] = {'sha': 'b' * 40, 'type': kind, 'mode': mode}
    assert evaluate(monkeypatch, Transport(fixture_tree(files))).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('mutation', ['commit-sha', 'commit-tree-sha', 'tree-sha', 'truncated',
    'truncated-missing', 'truncated-zero', 'duplicate', 'missing-ancestor', 'blob-parent',
    'bad-size', 'bool-size', 'bad-mode', 'bad-sha', 'tree-hash-mismatch', 'empty-tree'])
def test_malformed_or_unbound_metadata_never_authorizes(monkeypatch, mutation):
    api = Transport()
    tree = api.tree
    if mutation == 'commit-sha': api.commit['sha'] = 'f' * 40
    elif mutation == 'commit-tree-sha': api.commit['tree']['sha'] = 'not-a-sha'
    elif mutation == 'tree-sha': tree['sha'] = 'f' * 40
    elif mutation == 'truncated': tree['truncated'] = True
    elif mutation == 'truncated-missing': del tree['truncated']
    elif mutation == 'truncated-zero': tree['truncated'] = 0
    elif mutation == 'duplicate': tree['tree'].append(copy.deepcopy(tree['tree'][0]))
    elif mutation == 'missing-ancestor': tree['tree'] = [e for e in tree['tree'] if e['path'] != 'scripts']
    elif mutation == 'blob-parent': next(e for e in tree['tree'] if e['path'] == 'scripts').update(type='blob', mode='100644', size=12)
    elif mutation == 'bad-size': tree['tree'][0]['size'] = -1
    elif mutation == 'bool-size': tree['tree'][0]['size'] = True
    elif mutation == 'bad-mode': tree['tree'][0]['mode'] = '100664'
    elif mutation == 'bad-sha': tree['tree'][0]['sha'] = 'F' * 40
    elif mutation == 'tree-hash-mismatch': next(e for e in tree['tree'] if e['path'] == 'docs/guide.md')['sha'] = 'f' * 40
    elif mutation == 'empty-tree': tree['tree'] = []
    assert evaluate(monkeypatch, api).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('path', ['/docs/private.md', 'docs/../private.md', 'docs//private.md',
                                  'docs\\private.md', 'docs/\x00private.md'])
def test_noncanonical_tree_paths_rejected(monkeypatch, path):
    api = Transport()
    api.tree['tree'].append({'path': path, 'sha': 'b' * 40, 'mode': '100644', 'type': 'blob', 'size': 1})
    assert evaluate(monkeypatch, api).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('position,body', [(1, '{"build_type":"legacy","build_type":"workflow"}'),
    (1, 'not-json'), (2, ' ' * (pages.COMMIT_BYTES + 1)), (3, ' ' * (pages.TREE_BYTES + 1)),
    (3, '{"sha":"' + 'a' * 40 + '","tree":[],"truncated":false,"truncated":false}')])
def test_response_bounds_bad_json_and_duplicate_keys_fail_closed(monkeypatch, position, body):
    api = Transport()
    api.raw_at[position] = body
    assert evaluate(monkeypatch, api).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('head', ['main', HEAD.upper(), HEAD[:-1], '', None])
def test_mutable_or_malformed_head_cannot_authorize(monkeypatch, head):
    api = Transport()
    assert evaluate(monkeypatch, api, pr(head_sha=head)).verdict == checks.CheckVerdict.FAIL
    assert len(api.calls) == 1  # Settings only; no mutable commit/tree request.


@pytest.mark.parametrize('path', ['CNAME', '_config.yml', 'docs/_config.yml'])
def test_verified_artifact_does_not_bypass_other_pages_guards(monkeypatch, path):
    item = pr(DIFF + f'diff --git a/{path} b/{path}\n+++ b/{path}\n+change\n', changed_files=['docs/guide.md', path])
    assert evaluate(monkeypatch, Transport(), item).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('path', ['docs/b/private.md', 'docs/web/private.md', 'docs/a b/private.md'])
def test_source_path_b_substring_cannot_hide_document_changes(monkeypatch, path):
    diff = f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x\n+y\n'
    api = Transport()
    api.settings = {'build_type': 'legacy', 'source': {'path': '/'}}
    assert evaluate(monkeypatch, api, pr(diff, changed_files=[])).verdict == checks.CheckVerdict.FAIL


def test_quoted_control_path_fails_closed_without_network(monkeypatch):
    item = pr('diff --git "a/docs/a\\tfile.md" "b/docs/a\\tfile.md"\n+++ "b/docs/a\\tfile.md"\n+x\n', changed_files=[])
    assert checks.check_pages_exposure(item, None).verdict == checks.CheckVerdict.FAIL


def test_authoritative_changed_files_cannot_be_hidden_by_missing_diff(monkeypatch):
    api = Transport()
    api.settings = {'build_type': 'legacy', 'source': {'path': '/'}}
    assert evaluate(monkeypatch, api, pr('', changed_files=['docs/private.md'])).verdict == checks.CheckVerdict.FAIL


def test_no_docs_means_no_pages_api_or_artifact_lookup(monkeypatch):
    item = pr('diff --git a/app.js b/app.js\n+++ b/app.js\n+x\n', changed_files=['app.js'])
    assert checks.check_pages_exposure(item, None).verdict == checks.CheckVerdict.PASS


@pytest.mark.parametrize('path', ['assets/private.md', 'r/hidden/private.md'])
@pytest.mark.parametrize('operation', ['rename', 'copy'])
def test_docs_origin_move_to_public_path_is_not_an_excluded_docs_change(monkeypatch, path, operation):
    diff = (f'diff --git a/docs/private.md b/{path}\nsimilarity index 100%\n'
            f'{operation} from docs/private.md\n{operation} to {path}\n')
    result = evaluate(monkeypatch, Transport(), pr(diff, changed_files=[path]))
    assert result.verdict == checks.CheckVerdict.FAIL
    assert 'moved/copied' in result.details


@pytest.mark.parametrize('quoted', [False, True])
def test_spaced_docs_origin_move_is_classified_from_both_paths(monkeypatch, quoted):
    source, dest = 'docs/private notes.md', 'assets/public notes.md'
    left, right = 'a/' + source, 'b/' + dest
    if quoted:
        left, right = json.dumps(left), json.dumps(right)
    diff = f'diff --git {left} {right}\nrename from {source}\nrename to {dest}\n'
    assert evaluate(monkeypatch, Transport(), pr(diff, changed_files=[dest])).verdict == checks.CheckVerdict.FAIL


def test_incomplete_or_ambiguous_diff_header_never_silently_skips_check(monkeypatch):
    item = pr('diff --git malformed-header\n+change\n', changed_files=[])
    assert checks.check_pages_exposure(item, None).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('source', ['CNAME', '_config.yml'])
def test_source_side_move_keeps_cname_and_jekyll_guards(monkeypatch, source):
    dest = 'assets/old-config.txt'
    diff = f'diff --git a/{source} b/{dest}\nrename from {source}\nrename to {dest}\n'
    item = pr(diff, changed_files=[dest])
    assert checks.check_pages_exposure(item, None).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('path', ['.github/workflows/ci.yml', 'scripts/package-pages.cjs',
    'package.json', 'package-lock.json', '.gitattributes', '.npmrc', 'npm-shrinkwrap.json',
    'binding.gyp', 'node_modules/inject.js'])
def test_publication_input_only_change_cannot_start_exposing_unchanged_docs(monkeypatch, path):
    files = dict(PINS)
    files[path] = 'b' * 40
    api = Transport(fixture_tree(files))
    item = pr(f'diff --git a/{path} b/{path}\n+++ b/{path}\n+change\n', changed_files=[path])
    result = evaluate(monkeypatch, api, item)
    assert result.verdict == checks.CheckVerdict.FAIL
    assert 'artifact boundary is unverified' in result.details
    assert len(api.calls) == 3


def test_exact_approved_health_workflow_addition_needs_no_docs_change(monkeypatch):
    files = dict(PINS, **{'.github/workflows/beta-health.yml': HEALTH})
    api = Transport(fixture_tree(files))
    path = '.github/workflows/beta-health.yml'
    item = pr(f'diff --git a/{path} b/{path}\n+++ b/{path}\n+health\n', changed_files=[path])
    assert evaluate(monkeypatch, api, item).verdict == checks.CheckVerdict.PASS
    assert len(api.calls) == 3


def test_confirmed_pages_off_does_not_block_publication_input_edits(monkeypatch):
    api = Transport()
    api.settings_status = 404
    path = '.github/workflows/ci.yml'
    item = pr(f'diff --git a/{path} b/{path}\n+++ b/{path}\n+change\n', changed_files=[path])
    assert evaluate(monkeypatch, api, item).verdict == checks.CheckVerdict.PASS
    assert len(api.calls) == 2


def test_profile_exclusion_is_not_extended_to_case_aliases(monkeypatch):
    item = pr('diff --git a/DOCS/private.md b/DOCS/private.md\n+++ b/DOCS/private.md\n+x\n', changed_files=['DOCS/private.md'])
    assert evaluate(monkeypatch, Transport(), item).verdict == checks.CheckVerdict.FAIL


@pytest.mark.parametrize('mode,kind', [('120000', 'blob'), ('160000', 'commit')])
@pytest.mark.parametrize('metadata', ['new file mode {mode}', 'old mode 100644\nnew mode {mode}',
                                     'index aaaa111..bbbb222 {mode}'])
def test_nonregular_published_path_changes_trigger_existing_tree_constraint(monkeypatch, mode, kind, metadata):
    files = dict(PINS)
    files['r/leak'] = {'sha': 'b' * 40, 'type': kind, 'mode': mode}
    api = Transport(fixture_tree(files))
    diff = 'diff --git a/r/leak b/r/leak\n' + metadata.format(mode=mode) + '\n--- a/r/leak\n+++ b/r/leak\n@@ -1 +1 @@\n-x\n+y\n'
    result = evaluate(monkeypatch, api, pr(diff, changed_files=['r/leak']))
    assert result.verdict == checks.CheckVerdict.FAIL
    assert len(api.calls) == 3


def test_hunk_text_that_resembles_mode_metadata_does_not_trigger_reads(monkeypatch):
    diff = 'diff --git a/app.js b/app.js\n--- a/app.js\n+++ b/app.js\n@@ -1 +1 @@\n-old\n+new file mode 120000\n+index aaaa111..bbbb222 160000\n'
    assert checks.check_pages_exposure(pr(diff, changed_files=['app.js']), None).verdict == checks.CheckVerdict.PASS


def test_malformed_git_header_cannot_hide_in_missing_section(monkeypatch):
    assert checks.check_pages_exposure(pr('diff --git\n+change\n', changed_files=[]), None).verdict == checks.CheckVerdict.FAIL


def test_expired_deadline_stops_before_reserving_or_reading_github(monkeypatch):
    assert pages.pages_source('example', 'different-repository', deadline=0) == pages.UNKNOWN
    assert not pages.approved_docs_exclusion('example', 'different-repository', HEAD, deadline=0)


def test_confirmed_pages_off_preserves_docs_origin_rename_support(monkeypatch):
    api = Transport()
    api.settings_status = 404
    diff = 'diff --git a/docs/private.md b/src/private.md\nrename from docs/private.md\nrename to src/private.md\n'
    result = evaluate(monkeypatch, api, pr(diff, changed_files=['src/private.md']))
    assert result.verdict == checks.CheckVerdict.PASS
    assert len(api.calls) == 2
