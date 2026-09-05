#!/usr/bin/env python3
"""Run the full dashboard claim smoke on disposable, synthetic GitHub data."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from offline_qa_fixtures import (
    assert_fixture_calls,
    fixture_environment,
    prepare_generator,
)


ROOT = Path(__file__).resolve().parent
OFFLINE_NOTE = "OFFLINE QA FIXTURE - synthetic data, not live project status. Do not publish."


def validate_output_directory(value: str | None) -> Path | None:
    if value is None:
        return None
    output = Path(value).expanduser().resolve()
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if not any(output != temp_root and output.is_relative_to(temp_root) for temp_root in temp_roots):
        raise ValueError("--output-dir must be a child of a system temporary directory")
    if output.is_relative_to(ROOT) or any((parent / ".git").exists() for parent in (output, *output.parents)):
        raise ValueError("--output-dir must not be inside a repository")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("--output-dir must be absent or an empty temporary directory")
    if not output.parent.is_dir():
        raise ValueError("--output-dir parent must already exist")
    return output


def artifact_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("index.html", "status.json")
    }


def mark_offline_artifacts(generator: Path) -> None:
    status_path = generator / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["offline_fixture"] = {"synthetic": True, "publishable": False, "note": OFFLINE_NOTE}
    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    index_path = generator / "index.html"
    html = index_path.read_text(encoding="utf-8")
    body = '<body class="tab-home">'
    if html.count(body) != 1:
        raise RuntimeError("Cannot visibly label the offline fixture: unexpected page body")
    banner = (
        '\n<aside role="note" data-offline-fixture="true" '
        'style="padding:8px;text-align:center;border:1px dashed #d97757;'
        'font:12px system-ui;color:#f5d9b0;background:#17120f">'
        + OFFLINE_NOTE + "</aside>"
    )
    index_path.write_text(html.replace(body, body + banner), encoding="utf-8")


def run_offline(output: Path | None = None) -> None:
    print("OFFLINE QA: synthetic fixture collection only; no live GitHub or owner probes.", flush=True)
    before = artifact_hashes()
    try:
        with tempfile.TemporaryDirectory(prefix="bob-dashboard-offline-") as directory:
            scratch = Path(directory).resolve()
            generator = scratch / "generator"
            prepare_generator(ROOT, generator)
            env = fixture_environment(scratch)
            log_path = Path(env["BOB_DASHBOARD_FIXTURE_LOG"])
            if shutil.which("gh", path=env["PATH"]) != str(scratch / "fixture-bin" / "gh"):
                raise RuntimeError("Fake gh is not the first executable; refusing to run")
            subprocess.run(["bash", str(generator / "refresh.sh")], cwd=generator, env=env, check=True)
            assert_fixture_calls(log_path)
            mark_offline_artifacts(generator)
            smoke_env = {
                **env,
                "REFRESH_SH": str(generator / "refresh.sh"),
                "STATUS_JSON": str(generator / "status.json"),
            }
            subprocess.run(
                ["bash", str(ROOT / "qa-claim-smoke.sh"), str(generator / "index.html")],
                cwd=ROOT, env=smoke_env, check=True,
            )
            calls = assert_fixture_calls(log_path)
            if output is not None:
                # Revalidate after QA; never replace a directory populated by a
                # concurrent process. Preserve no generator, executable, or git data.
                output = validate_output_directory(str(output))
                assert output is not None
                output.mkdir(exist_ok=True)
                for name in ("index.html", "status.json"):
                    with (output / name).open("xb") as destination:
                        destination.write((generator / name).read_bytes())
                with (output / "OFFLINE-FIXTURE.txt").open("x", encoding="utf-8") as note:
                    note.write(OFFLINE_NOTE + "\nNo live repositories, providers, or owner probes were queried.\n")
                print(f"Offline fixture HTML: {output / 'index.html'}")
                print(f"Offline fixture JSON: {output / 'status.json'}")
    finally:
        if artifact_hashes() != before:
            raise RuntimeError("Offline QA changed scheduler-owned artifacts")
    print(f"OFFLINE QA PASSED ({len(calls)} synthetic GitHub reads; no live claims)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", help="Optionally retain marked browser fixtures in an empty temp directory")
    args = parser.parse_args()
    try:
        output = validate_output_directory(args.output_dir)
    except ValueError as error:
        parser.error(str(error))
    run_offline(output)


if __name__ == "__main__":
    main()
