"""Security regressions: malicious specs and execution/config boundaries."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cluster import job_exec
from cluster.config import ALLOWED_ARGV, ALLOWED_REPOS, MAX_LOAD_CEILING, WORKER_PATH
from cluster.sanitize import sanitize_tail
from cluster.spec import SpecError, validate_job_spec


def valid_spec(**changes):
    spec = dict(v=1, id="job-1", **{"class": "test"},
                repo="rupret007/bob-ops-dashboard", ref="main",
                cmd=["npm", "test"], timeout_s=60, load_ceiling=4)
    spec.update(changes)
    return spec


def synthetic_secrets():
    # Construct unmistakably synthetic credentials; never use live values.
    return ["sk-proj-" + "x" * 48, "sk-ant-api03-" + "x" * 48,
            "AIza" + "x" * 35, "123456789:" + "x" * 35,
            "BSA" + "x" * 40, "QUJD" * 30 + "=="]


class TestSpecSecurity(unittest.TestCase):
    def reject(self, **changes):
        with self.assertRaises(SpecError):
            validate_job_spec(valid_spec(**changes))

    def test_exact_argv_allowlist_for_every_class(self):
        for klass, commands in ALLOWED_ARGV.items():
            for command in commands:
                with self.subTest(klass=klass, command=command):
                    validate_job_spec(valid_spec(**{"class": klass, "cmd": list(command)}))
                    self.reject(**{"class": klass, "cmd": list(command) + ["--eval=bad"]})
            for cmd in (["bash", "-c", "echo hi"], ["sh", "-c", "echo hi"],
                        ["/bin/sh", "-c", "echo hi"], ["curl", "https://example.invalid"],
                        ["python3", "-c", "print('bad')"], ["node", "-e", "process.exit()"],
                        ["env", "sh"], ["echo", "hi"], ["npm", "exec", "bad"],
                        ["npm", "test", "--", "--require=bad"], ["./npm", "test"]):
                with self.subTest(klass=klass, cmd=cmd):
                    self.reject(**{"class": klass, "cmd": cmd})
            for other, commands in ALLOWED_ARGV.items():
                if other != klass:
                    for command in commands:
                        self.reject(**{"class": klass, "cmd": list(command)})

    def test_job_ids(self):
        for value in (".", "..", "/", "", "../x", "a/b", "a.b", "a\\b", "a\n", "a b",
                      "x" * 65, "é", "*", ">", "a\x00"):
            with self.subTest(value=value):
                self.reject(id=value)
        for value in ("a", "A_0-z", "x" * 64):
            validate_job_spec(valid_spec(id=value))

    def test_git_refs(self):
        for value in ("-main", "--upload-pack=bad", "--help", "", " main", "main\n",
                      "main:refs/heads/other", "HEAD~1", "HEAD^{tree}", "a..b", "a//b",
                      "refs/heads/.hidden", "refs/heads/a.lock", "a.", "a/", "/a",
                      "a@{1}", "a*", "a?", "a[b", "a\\b", "a\x00", "a" * 201):
            with self.subTest(value=value):
                self.reject(ref=value)
        for value in ("a1" * 20, "AB" * 20, "main", "feature/security-1", "refs/tags/v1.2.3"):
            validate_job_spec(valid_spec(ref=value))

    def test_repo_allowlist_is_explicit(self):
        self.assertEqual(ALLOWED_REPOS, {"rupret007/bob-ops-dashboard"})
        for repo in ("evil/bob-ops-dashboard", "rupret007/unreviewed", "rupret007/../evil",
                     "rupret007/bob-ops-dashboard.git", "https://github.com/rupret007/bob-ops-dashboard",
                     "rupret007/bob-ops-dashboard/extra", "RUPRET007/bob-ops-dashboard"):
            with self.subTest(repo=repo):
                self.reject(repo=repo)

    def test_environment_names_and_values_are_allowlisted(self):
        for key in ("LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "PATH", "HOME",
                    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_SSH_COMMAND", "GIT_DIR",
                    "BOB_NODE_TOKEN", "OPENAI_API_KEY", "PYTHONPATH", "NODE_OPTIONS", "UNKNOWN"):
            with self.subTest(key=key):
                self.reject(env={key: "malicious"})
        self.reject(env={"CI": "arbitrary"})
        validate_job_spec(valid_spec(env={"CI": "true", "LANG": "C.UTF-8", "TZ": "UTC"}))

    def test_new_secret_patterns_in_argv_and_env(self):
        for secret in synthetic_secrets():
            with self.subTest(prefix=secret[:8]):
                for changes in ({"cmd": ["echo", secret]}, {"env": {"CI": secret}}):
                    with self.assertRaisesRegex(SpecError, "possible secret"):
                        validate_job_spec(valid_spec(**changes))
                self.assertNotIn(secret, sanitize_tail("output " + secret))
                self.assertIn("[REDACTED]", sanitize_tail(secret))

    def test_secrets_split_at_every_argv_boundary(self):
        for secret in synthetic_secrets():
            for split in range(1, len(secret)):
                with self.subTest(prefix=secret[:8], split=split):
                    with self.assertRaisesRegex(SpecError, "possible secret"):
                        validate_job_spec(valid_spec(cmd=["echo", secret[:split], secret[split:]]))
            with self.assertRaisesRegex(SpecError, "possible secret"):
                validate_job_spec(valid_spec(cmd=list(secret)))

    def test_resource_limits_are_finite_bounded_numbers(self):
        for field, maximum in (("timeout_s", 7200), ("load_ceiling", MAX_LOAD_CEILING)):
            for value in (float("nan"), float("inf"), -float("inf"), -1, 0, maximum + 1,
                          10 ** 1000, True, False, "10", None):
                with self.subTest(field=field, value=str(value)[:20]):
                    self.reject(**{field: value})
            for value in (0.1, maximum):
                validate_job_spec(valid_spec(**{field: value}))


class TestExecutionSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "jobs"
        self.root.mkdir()
        patcher = patch.object(job_exec, "WORK_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cleanup_rejects_root_traversal_and_symlinks_before_any_effect(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        marker = outside / "keep"
        marker.write_text("keep")
        (self.root / "escape").symlink_to(outside, target_is_directory=True)
        (self.root / "alias").symlink_to(self.root, target_is_directory=True)
        sibling = self.root / "sibling"
        sibling.mkdir()
        (self.root / "sibling-alias").symlink_to(sibling, target_is_directory=True)
        for job_id in (".", "..", "/", "", "../outside", "escape", "alias", "sibling-alias"):
            with self.subTest(job_id=job_id), patch.object(job_exec.shutil, "rmtree") as delete, \
                    patch.object(job_exec.subprocess, "run") as run:
                with self.assertRaises(SpecError):
                    job_exec.job_workdir(job_id)
                for dry_run in (False, True):
                    with self.assertRaises(SpecError):
                        job_exec.prepare_worktree(valid_spec(id=job_id), dry_run)
                delete.assert_not_called()
                run.assert_not_called()
                self.assertEqual(marker.read_text(), "keep")

    def test_job_workdir_rejects_root_alias_directly(self):
        (self.root / "alias").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(SpecError, "must not alias the jobs root"):
            job_exec.job_workdir("alias")
        # A root-aliasing symlink must also never reach rmtree or git.
        with patch.object(job_exec.shutil, "rmtree") as delete, patch.object(job_exec.subprocess, "run") as run:
            with self.assertRaisesRegex(SpecError, "must not alias the jobs root"):
                job_exec.prepare_worktree(valid_spec(id="alias"), False)
            delete.assert_not_called()
            run.assert_not_called()

    def test_job_workdir_rejects_preplanted_symlink_directly(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        sibling = self.root / "sibling"
        sibling.mkdir()
        (self.root / "escape").symlink_to(outside, target_is_directory=True)
        (self.root / "sibling-alias").symlink_to(sibling, target_is_directory=True)
        (self.root / "dangling").symlink_to(self.root / "not-there", target_is_directory=True)
        for job_id in ("escape", "sibling-alias", "dangling"):
            with self.subTest(job_id=job_id):
                with self.assertRaisesRegex(SpecError, "must not be a symlink"):
                    job_exec.job_workdir(job_id)
                with patch.object(job_exec.shutil, "rmtree") as delete, \
                        patch.object(job_exec.subprocess, "run") as run:
                    with self.assertRaisesRegex(SpecError, "must not be a symlink"):
                        job_exec.prepare_worktree(valid_spec(id=job_id), False)
                    delete.assert_not_called()
                    run.assert_not_called()

    def _resolve_job_to(self, job_id, target):
        real_resolve = Path.resolve
        candidate = self.root / job_id

        def fake_resolve(path, *args, **kwargs):
            if path == candidate:
                return target
            return real_resolve(path, *args, **kwargs)

        return patch.object(Path, "resolve", fake_resolve)

    def test_job_workdir_outside_root_check_fires_alone(self):
        # Not a symlink and not the root: only the outside-root check can reject.
        outside = self.root.parent / "outside" / "job1"
        with self._resolve_job_to("job1", outside):
            with self.assertRaisesRegex(SpecError, "strictly under jobs root"):
                job_exec.job_workdir("job1")

    def test_job_workdir_must_resolve_to_itself_check_fires_alone(self):
        # Resolves inside root to a different entry without being a symlink.
        with self._resolve_job_to("job1", self.root / "other-job"):
            with self.assertRaisesRegex(SpecError, "must resolve to itself"):
                job_exec.job_workdir("job1")

    def test_existing_job_cleanup_and_git_option_terminators(self):
        wd = self.root / "job-1"
        wd.mkdir()
        (wd / "old").write_text("old")
        with patch.object(job_exec, "ensure_mirror", return_value=self.root.parent / "mirror"), \
                patch.object(job_exec.subprocess, "run") as run:
            self.assertEqual(job_exec.prepare_worktree(valid_spec(), False), wd)
        self.assertFalse(wd.exists())
        calls = [call.args[0] for call in run.call_args_list]
        self.assertIn(["git", "fetch", "--", "origin", "main"], calls)
        self.assertIn(["git", "checkout", "--detach", "FETCH_HEAD", "--"], calls)
        for call in run.call_args_list:
            self.assertEqual(set(call.kwargs["env"]), {"PATH", "HOME", "LANG", "LC_ALL", "TZ"})

    def test_git_fetch_and_checkout_work_with_option_terminators(self):
        origin = self.root.parent / "origin"
        origin.mkdir()
        env = job_exec.minimal_env(self.root.parent / "git-home")

        def git(*args):
            return subprocess.run(["git", *args], cwd=origin, env=env,
                                  check=True, capture_output=True, text=True).stdout.strip()

        git("init")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "--allow-empty", "-m", "synthetic fixture")
        sha = git("rev-parse", "HEAD")
        git("branch", "fixture-branch")
        git("tag", "fixture-tag")
        with patch.object(job_exec, "ensure_mirror", return_value=origin):
            for ref in (sha, "fixture-branch", "refs/tags/fixture-tag"):
                with self.subTest(ref=ref):
                    wd = job_exec.prepare_worktree(valid_spec(ref=ref), False)
                    actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wd,
                                            env=env, check=True, capture_output=True, text=True)
                    self.assertEqual(actual.stdout.strip(), sha)

    def test_mirror_is_not_partial_so_shared_clones_can_checkout(self):
        with patch.object(job_exec, "MIRROR_ROOT", self.root.parent / "mirrors"), \
                patch.object(job_exec.subprocess, "run") as run:
            job_exec.ensure_mirror("rupret007/bob-ops-dashboard", False)
        clone = run.call_args_list[0].args[0]
        self.assertEqual(clone[:3], ["git", "clone", "--mirror"])
        self.assertFalse(any(a.startswith("--filter") for a in clone))

    def test_shared_clone_from_mirror_checks_out_files(self):
        src = self.root.parent / "src"
        src.mkdir()
        env = job_exec.minimal_env(self.root.parent / "git-home")

        def git(*args, cwd=src):
            return subprocess.run(["git", *args], cwd=cwd, env=env,
                                  check=True, capture_output=True, text=True).stdout.strip()

        git("init")
        (src / "f.txt").write_text("payload")
        git("add", "f.txt")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
        sha = git("rev-parse", "HEAD")
        mirror = self.root.parent / "mirror.git"
        git("clone", "--mirror", str(src), str(mirror), cwd=self.root.parent)
        with patch.object(job_exec, "ensure_mirror", return_value=mirror):
            wd = job_exec.prepare_worktree(valid_spec(ref=sha), False)
        self.assertEqual((wd / "f.txt").read_text(), "payload")

    def test_environment_is_minimal_for_commands_and_git(self):
        poison = {"BOB_NODE_TOKEN": "synthetic-nats", "OPENAI_API_KEY": "synthetic-api",
                  "PATH": "/untrusted", "LD_PRELOAD": "bad", "GIT_CONFIG_COUNT": "1"}
        wd = self.root / "job-1"
        wd.mkdir()
        with patch.dict(os.environ, poison), \
                patch.object(job_exec, "MIRROR_ROOT", self.root.parent / "mirrors"), \
                patch.object(job_exec.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok", "")) as run:
            self.assertEqual(job_exec.run_cmd(valid_spec(env={"CI": "true"}), wd, False), (0, "ok", False))
            job_exec.ensure_mirror("rupret007/bob-ops-dashboard", False)
            # Exercise mirror refresh as well as initial clone.
            (job_exec.mirror_path("rupret007/bob-ops-dashboard") / "objects").mkdir(parents=True)
            job_exec.ensure_mirror("rupret007/bob-ops-dashboard", False)
        for call in run.call_args_list:
            env = call.kwargs["env"]
            self.assertEqual(env["PATH"], WORKER_PATH)
            self.assertTrue(set(env) <= {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "CI"})
            for key, value in poison.items():
                self.assertNotEqual(env.get(key), value)
        self.assertEqual(run.call_args_list[0].kwargs["env"]["CI"], "true")
        self.assertEqual(run.call_args_list[0].kwargs["env"]["HOME"], str(wd / ".job-home"))

    def test_execution_revalidates_untrusted_specs(self):
        for changes in ({"cmd": ["sh", "-c", "bad"]}, {"env": {"PATH": "/bad"}},
                        {"ref": "--help"}, {"repo": "evil/repo"}):
            with patch.object(job_exec.subprocess, "run") as run:
                with self.assertRaises(SpecError):
                    job_exec.prepare_worktree(valid_spec(**changes), False)
                with self.assertRaises(SpecError):
                    job_exec.run_cmd(valid_spec(**changes), self.root, False)
                run.assert_not_called()


class TestNatsConfigSecurity(unittest.TestCase):
    def setUp(self):
        self.config = (Path(__file__).resolve().parents[1] / "nats.conf.template").read_text()

    def test_nucbox_worker_can_ack_jobs(self):
        block = self.config.split("user: nucbox", 1)[1].split("user:", 1)[0]
        publish = set(re.findall(r'"([^\"]+)"', re.search(r'publish: \[(.*?)\]', block).group(1)))
        self.assertIn("$JS.ACK.JOBS.workers.>", publish)

    def test_monitoring_is_loopback_and_does_not_conflict_with_vaultwarden(self):
        self.assertRegex(self.config, r'(?m)^http: "127\.0\.0\.1:8232"$')
        self.assertNotRegex(self.config, r'(?m)^http_port:')
        self.assertNotIn("8222", self.config)

    def test_workers_can_only_publish_required_jetstream_subjects(self):
        for node in ("mini", "macbook"):
            block = self.config.split(f"user: {node}", 1)[1].split("user:", 1)[0]
            publish = set(re.findall(r'"([^\"]+)"', re.search(r'publish: \[(.*?)\]', block).group(1)))
            self.assertEqual(publish, {f"metrics.{node}", "results.>",
                                      "$JS.API.CONSUMER.INFO.JOBS.workers",
                                      "$JS.API.CONSUMER.MSG.NEXT.JOBS.workers",
                                      "$JS.ACK.JOBS.workers.>"})
