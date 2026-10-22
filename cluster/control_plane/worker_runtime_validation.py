"""Native closure verification supplied with the executing facade namespace."""
from __future__ import annotations

def _qualified_runtime(_runtime_bindings, head_sha, tree):
    _runtime_bindings['_require'](isinstance(head_sha, str) and _runtime_bindings['_HEAD'].fullmatch(head_sha)
             and _runtime_bindings['platform'].system() == 'Linux' and _runtime_bindings['platform'].machine() == 'x86_64')
    home = _runtime_bindings['Path'].home()
    _runtime_bindings['_directory'](home)
    root = home / _runtime_bindings['PROFILE']
    # Every parent below HOME must be owned and resistant to other-user writes.
    path = home
    for component in _runtime_bindings['Path'](_runtime_bindings['PROFILE']).parts:
        path = path / component
        _runtime_bindings['_directory'](path, private=path == root)
    for relative in ('venv', 'venv/bin', 'native', 'receipts',
                     'config', 'cache', 'data', 'state', 'xdg-runtime'):
        _runtime_bindings['_directory'](root / relative)
    empty_netrc, _ = _runtime_bindings['_digest'](root / 'config/empty-netrc', 1, private=True, allow_empty=True)
    _runtime_bindings['_require'](empty_netrc == b'')
    raw, receipt_hash = _runtime_bindings['_digest'](root / 'qualification.json', 128 * 1024, private=True)
    value = _runtime_bindings['_json'](raw)
    _runtime_bindings['_require'](type(value.get('schema')) is int and value['schema'] == 1
             and value.get('repo') == _runtime_bindings['WEBJAM'] and value.get('machine') == 'nucbox'
             and value.get('platform') == 'linux-x86_64')
    _runtime_bindings['_require'](value.get('test_recipe') == _runtime_bindings['TEST_RECIPE']
             and value.get('headless_audio_config_sha256') == _runtime_bindings['ALSA_SHA256'])
    audio, audio_hash = _runtime_bindings['_digest'](root / 'config/alsa.conf', 4096, private=True)
    _runtime_bindings['_require'](audio == _runtime_bindings['ALSA_CONFIG'] and audio_hash == _runtime_bindings['ALSA_SHA256'])
    python = root / 'venv/bin/python'
    native = root / 'native'
    _runtime_bindings['_require'](value.get('python') == str(python) and value.get('native_prefix') == str(native)
             and isinstance(value.get('python_version'), str)
             and _runtime_bindings['re'].fullmatch(r'3\.11\.\d+', value['python_version']))
    resolved = python.resolve(strict=True)
    _runtime_bindings['_require'](str(resolved) == value.get('python_realpath'))
    _, binary_hash = _runtime_bindings['_digest'](resolved, 64 * 1024 * 1024)
    _runtime_bindings['_require'](binary_hash == value.get('python_sha256') and _runtime_bindings['os'].access(python, _runtime_bindings['os'].X_OK))
    cfg, _ = _runtime_bindings['_digest'](root / 'venv/pyvenv.cfg', 16384)
    _runtime_bindings['_require'](_runtime_bindings['re'].search(rb'(?mi)^include-system-site-packages\s*=\s*false\s*$', cfg))
    manifests = {}
    for name, field in (('wheels', 'wheel_manifest_sha256'), ('native', 'native_manifest_sha256')):
        data, digest = _runtime_bindings['_digest'](root / ('receipts/' + name + '.json'), 4 * 1024 * 1024)
        _runtime_bindings['_require'](isinstance(value.get(field), str) and _runtime_bindings['_SHA'].fullmatch(value[field])
                 and digest == value[field])
        manifest = manifests[name] = _runtime_bindings['_json'](data)
        _runtime_bindings['_require'](type(manifest.get('schema')) is int and manifest['schema'] == 1)
        artifacts = manifest.get('artifacts')
        _runtime_bindings['_require'](type(artifacts) is list and 0 < len(artifacts) <= 1024)
        names = set()
        for artifact in artifacts:
            _runtime_bindings['_require'](type(artifact) is dict and isinstance(artifact.get('filename'), str)
                     and _runtime_bindings['re'].fullmatch(r'[A-Za-z0-9_.+~\-]+', artifact['filename'])
                     and artifact['filename'] not in names
                     and isinstance(artifact.get('sha256'), str) and _runtime_bindings['_SHA'].fullmatch(artifact['sha256'])
                     and type(artifact.get('bytes')) is int and artifact['bytes'] > 0)
            names.add(artifact['filename'])
            if name == 'native':
                _runtime_bindings['_require'](all(isinstance(artifact.get(k), str) and artifact[k]
                             for k in ('package', 'version')))
        if name == 'native':
            _runtime_bindings['_require'](manifest.get('signature_verified') is True
                     and isinstance(manifest.get('index_sha256'), str)
                     and _runtime_bindings['_SHA'].fullmatch(manifest['index_sha256']))
            audio_proof = manifest.get('headless_audio')
            _runtime_bindings['_require'](type(audio_proof) is dict and audio_proof.get('backend') == 'alsa-null'
                     and audio_proof.get('configuration') == 'config/alsa.conf'
                     and audio_proof.get('configuration_sha256') == _runtime_bindings['ALSA_SHA256']
                     and all(audio_proof.get(k) is False
                             for k in ('includes', 'hardware_audio', 'audio_server')))
            fonts = manifest.get('font_closure')
            _runtime_bindings['_require'](type(fonts) is dict and fonts.get('directory') == _runtime_bindings['FONT_DIRECTORY']
                     and any(a.get('package') == 'fonts-liberation'
                             and a['sha256'] == fonts.get('package_sha256') for a in artifacts))
            files = fonts.get('files')
            _runtime_bindings['_require'](type(files) is list and len(files) == 12)
            directory = native
            for component in _runtime_bindings['Path'](_runtime_bindings['FONT_DIRECTORY']).parts:
                directory = directory / component
                _runtime_bindings['_directory'](directory)
            filenames = []
            for font in files:
                _runtime_bindings['_require'](type(font) is dict and isinstance(font.get('filename'), str)
                         and _runtime_bindings['re'].fullmatch(r'[A-Za-z0-9_.-]+\.ttf', font['filename'])
                         and type(font.get('bytes')) is int and font['bytes'] > 0)
                data, digest = _runtime_bindings['_digest'](directory / font['filename'], 8 * 1024 * 1024)
                _runtime_bindings['_require'](digest == font.get('sha256') and len(data) == font['bytes'])
                filenames.append(font['filename'])
            _runtime_bindings['_require'](filenames == sorted(set(filenames))
                     and set(filenames) == {p.name for p in directory.iterdir()}
                     and _runtime_bindings['hashlib'].sha256(_runtime_bindings['json'].dumps(files, sort_keys=True,
                         separators=(',', ':')).encode()).hexdigest() == fonts.get('sha256'))
            _runtime_bindings['_font_link'](root / 'data/fonts', directory)
    proof = value.get('imports')
    _runtime_bindings['_require'](type(proof) is dict and proof.get('modules') == list(_runtime_bindings['IMPORTS'])
             and type(proof.get('returncode')) is int and proof['returncode'] == 0)
    heads = value.get('qualified_heads')
    _runtime_bindings['_require'](type(heads) is list and 0 < len(heads) <= 32)
    seen = set()
    selected = None
    for head in heads:
        _runtime_bindings['_require'](type(head) is dict and isinstance(head.get('head_sha'), str)
                 and _runtime_bindings['_HEAD'].fullmatch(head['head_sha']) and head['head_sha'] not in seen
                 and isinstance(head.get('tree_sha'), str) and _runtime_bindings['_HEAD'].fullmatch(head['tree_sha'])
                 and all(isinstance(head.get(k), str) and _runtime_bindings['_SHA'].fullmatch(head[k])
                         for k in ('requirements_sha256', 'ci_sha256', 'test_files_sha256'))
                 and type(head.get('test_file_count')) is int and 0 < head['test_file_count'] <= 1024)
        seen.add(head['head_sha'])
        collection = head.get('collection')
        _runtime_bindings['_require'](type(collection) is dict and type(collection.get('returncode')) is int
                 and collection['returncode'] == 0 and type(collection.get('count')) is int
                 and collection['count'] > 0)
        if head['head_sha'] == head_sha:
            selected = head
    _runtime_bindings['_require'](selected is not None)
    _runtime_bindings['_require'](manifests['wheels'].get('requirements_sha256') == selected['requirements_sha256'])
    if tree is not None:
        for relative, field in (('requirements.txt', 'requirements_sha256'),
                                ('.github/workflows/ci.yml', 'ci_sha256')):
            # Checkout contents are data; symlinked dependency recipes are refused.
            path = _runtime_bindings['Path'](tree) / relative
            _runtime_bindings['_require'](all(not p.is_symlink() for p in (path, *path.parents)
                         if p == _runtime_bindings['Path'](tree) or _runtime_bindings['Path'](tree) in p.parents))
            _, digest = _runtime_bindings['_digest'](path, 1024 * 1024)
            _runtime_bindings['_require'](digest == selected[field])
    runtime = _runtime_bindings['QualifiedRuntime'](str(python), str(native), receipt_hash, selected)
    result = _runtime_bindings['subprocess'].run([str(python), '-I', '-c', _runtime_bindings['_PROBE']],
        env=runtime.environment(_runtime_bindings['os'].environ), capture_output=True, text=True, timeout=15)
    _runtime_bindings['_require'](result.returncode == 0 and len(result.stdout) <= 1024)
    observed = _runtime_bindings['_json'](result.stdout)
    _runtime_bindings['_require'](observed == {'python': value['python_version'], 'pyside6': '6.11.1'})
    return runtime
