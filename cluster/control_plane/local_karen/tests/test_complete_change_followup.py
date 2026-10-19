"""Whole-change follow-ups preserve the rendered prompt budget for each judge.

Only synthetic inputs, fake immutable source, and the existing offline provider
transports are used. No credentials, repository source, or network are accessed.
"""
from dataclasses import replace
import hashlib
import json
from unittest.mock import patch

import pytest

from cluster.control_plane.local_karen import reviewers as r
from cluster.control_plane.local_karen.chunk_context import build_review_scope, context_size
from cluster.control_plane.local_karen.source_context import PinnedSourceContext
from cluster.control_plane.local_karen.tests import test_complete_change_plan as fixtures
from cluster.control_plane.local_karen.tests.test_complete_change_plan import answer, decode, large_pr


SOURCE_PATH = 'src/required.ts'


def prompt_bytes(prompt):
    return len(prompt.encode('utf-8')) + len(r.MINIMAX_SYSTEM_CONTRACT.encode('utf-8'))


def source_entry(pr, text):
    raw = text.encode('utf-8')
    return dict(path=SOURCE_PATH, head_sha=pr.head_sha, tree_sha='b' * 40,
                blob_sha=hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(),
                bytes=len(raw), source=text)


class RequestingAdapters(fixtures.OfflineAdapters):
    """The selected judge requests source on its first actual adapter call."""

    def __init__(self, tmp_path, actor, pr, source):
        super().__init__(tmp_path)
        self.actor, self.pr, self.source = actor, pr, source
        self.fetches = []

    def __enter__(self):
        super().__enter__()
        self.stack.enter_context(patch.object(PinnedSourceContext, 'fetch', autospec=True,
                                             side_effect=self.fetch))
        return self

    def fetch(self, resolver, supplied, path):
        assert resolver.identity == (self.pr.owner, self.pr.repo, self.pr.head_sha)
        assert (supplied.owner, supplied.repo, supplied.number, supplied.head_sha) == (
            self.pr.owner, self.pr.repo, self.pr.number, self.pr.head_sha)
        assert path == SOURCE_PATH
        self.fetches.append(path)
        return dict(self.source)

    def reply(self, actor, prompt):
        entries = decode(prompt)['supplemental_material']['entries']
        if actor == self.actor and not any(entry == self.source for entry in entries):
            result = json.loads(answer('BORDERLINE'))
            result['reason'] = 'Need the complete pinned existing implementation to assess this integration'
            result['requested_source_files'] = [SOURCE_PATH]
            return json.dumps(result)
        return answer()

    def cli(self, args, **kwargs):
        response = self.reply('codex', kwargs['input'])
        with patch.object(fixtures, 'answer', return_value=response):
            return super().cli(args, **kwargs)

    def api(self, request, **kwargs):
        prompt = json.loads(request.data)['messages'][1]['content']
        response = self.reply('minimax', prompt)
        with patch.object(fixtures, 'answer', return_value=response):
            return super().api(request, **kwargs)


@pytest.mark.parametrize('actor', ['codex', 'minimax'])
@pytest.mark.parametrize('kind', ['sixteen_k', 'json_escaping'])
def test_whole_change_oversized_source_followup_stays_unresolved_without_another_model_call(tmp_path, actor, kind):
    pr = large_pr()
    text = '//' + ('x' * 15998 if kind == 'sixteen_k' else '\u2603' * 2000)
    source = source_entry(pr, text)
    part = replace(pr, review_scope=build_review_scope(pr, [pr.diff], 1))
    initial_size = prompt_bytes(r.build_review_prompt(part))
    followup = r._context_part(part, [source], 'Requested immutable source context')
    assert r._bounded_complete_change(pr)
    assert source['bytes'] <= 16000 and max(context_size([source])) < 48000
    assert initial_size <= 96000 < prompt_bytes(r.build_review_prompt(followup))
    if kind == 'json_escaping':
        # A raw UTF-8 check would accept this; rendered JSON must be measured.
        assert initial_size + source['bytes'] < 96000

    with RequestingAdapters(tmp_path, actor, pr, source) as adapters:
        result, reviews = adapters.run(pr)

    assert result.verdict == r.ReviewVerdict.BORDERLINE
    assert len(adapters.prompts[actor]) == 1
    assert len(adapters.prompts['codex']) == 1
    assert len(adapters.prompts['minimax']) == (actor == 'minimax')
    assert adapters.fetches == [SOURCE_PATH]
    blocked = next(review for review in reviews if review.reviewer == actor)
    assert blocked.scope['context_incomplete'] is True
    assert blocked.scope['context_resolution']['code'] == 'context_budget'
    assert blocked.requested_source_files == [SOURCE_PATH]
    for prompts in adapters.prompts.values():
        for prompt in prompts:
            fixtures.assert_complete_packet(prompt, pr)


@pytest.mark.parametrize('actor', ['codex', 'minimax'])
def test_whole_change_small_source_followup_passes_after_both_independent_votes(tmp_path, actor):
    pr = large_pr()
    source = source_entry(pr, '//' + 'x' * 998)
    with RequestingAdapters(tmp_path, actor, pr, source) as adapters:
        result, reviews = adapters.run(pr)

    assert result.passed
    assert len(adapters.prompts[actor]) == 2
    other = 'minimax' if actor == 'codex' else 'codex'
    assert len(adapters.prompts[other]) == 1
    assert adapters.fetches == [SOURCE_PATH]
    assert {review.reviewer for review in reviews} == {'codex', 'minimax'}
    assert all(review.passed for review in reviews)
    fixtures.assert_complete_packet(adapters.prompts[actor][0], pr)
    fixtures.assert_complete_packet(adapters.prompts[other][0], pr)
    followup = decode(adapters.prompts[actor][1])
    assert followup['pinned_head_sha'] == pr.head_sha
    assert followup['chunk_under_review'] == {'diff': pr.diff, 'characters': len(pr.diff)}
    assert followup['review_scope']['whole_change_summary']['chunks'] == 1
    assert followup['supplemental_material'] == {'phase': 'requested', 'entries': [source]}
    assert all(prompt_bytes(prompt) <= 96000 for prompts in adapters.prompts.values() for prompt in prompts)
