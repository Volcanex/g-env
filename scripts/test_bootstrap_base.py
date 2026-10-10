import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
# What bootstrap-base.sh runs or links with content; other link targets may dangle.
FILES = ('bootstrap-base.sh', 'bin/g-instance', 'scripts/install-context.py', 'scripts/mirror-claude.py',
         'context/instance.py', 'codex/configure.py', 'claude/settings.json', 'git/gitconfig',
         'codex/rules/default.rules')


class OverlayTests(unittest.TestCase):
    """Run the real bootstrap-base.sh from a copy, with HOME and every sibling inside a temp dir."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.repo, self.home = self.base / 'g-env', self.base / 'home'
        for name in FILES:
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, self.repo / name)
        self.home.mkdir()
        self.stub = self.base / 'stub'
        self.stub.mkdir()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def bootstrap(self, darwin=False):
        if darwin:
            self.write(self.stub / 'uname', '#!/bin/sh\necho Darwin\n').chmod(0o755)
        environ = {k: v for k, v in os.environ.items() if not k.startswith(('G_', 'GENV', 'GCORE', 'GSITE'))}
        environ.update(HOME=str(self.home), PATH=f'{self.stub}:{os.environ["PATH"]}')
        run = subprocess.run(['bash', str(self.repo / 'bootstrap-base.sh')], env=environ, cwd=self.base,
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return run.stdout + run.stderr

    def target(self, path):
        return Path(os.readlink(self.home / path))

    def sources(self):
        return json.loads((self.home / '.agents/context.json').read_text())['sources']

    def instance(self, name='gp-core', env=()):
        instance = self.base / name
        self.write(instance / 'conventions.md', 'conventions')
        self.write(instance / 'CLAUDE.md', 'topology')
        for row in env:
            self.write(instance / 'env' / row, f'# instance {row}\n')
        return instance

    def test_no_instance_uses_framework_defaults(self):
        output = self.bootstrap()
        self.assertIn('no instance found', output)
        self.assertEqual(self.target('.claude/settings.json'), self.repo / 'claude/settings.json')
        self.assertEqual(self.target('.gitconfig'), self.repo / 'git/gitconfig')
        self.assertEqual(self.target('.local/bin/g-instance'), self.repo / 'bin/g-instance')
        self.assertEqual(set(self.sources()), {'host', 'shared'})

    def test_legacy_gcore_without_env_keeps_framework_files(self):
        gcore = self.instance('gcore')
        self.bootstrap()
        self.assertEqual(self.target('.claude/settings.json'), self.repo / 'claude/settings.json')
        self.assertEqual(self.target('.gitconfig'), self.repo / 'git/gitconfig')
        self.assertEqual(self.sources()['conventions'], str(gcore / 'conventions.md'))

    def test_instance_rows_replace_framework_links(self):
        instance = self.instance(env=('claude/settings.json', 'git/gitconfig'))
        (self.home / '.claude').mkdir()
        (self.home / '.claude/settings.json').symlink_to(self.repo / 'claude/settings.json')  # b today
        self.bootstrap()
        self.assertEqual(self.target('.claude/settings.json'), instance / 'env/claude/settings.json')
        self.assertEqual(self.target('.gitconfig'), instance / 'env/git/gitconfig')
        self.bootstrap()  # a managed instance link stays managed
        self.assertEqual(self.target('.claude/settings.json'), instance / 'env/claude/settings.json')
        (instance / 'env/claude/settings.json').unlink()
        self.bootstrap()  # and falls back once the instance drops its copy
        self.assertEqual(self.target('.claude/settings.json'), self.repo / 'claude/settings.json')

    def test_guard_preserves_independent_settings(self):
        self.instance(env=('claude/settings.json',))
        local = self.write(self.home / '.claude/settings.json', '{"local": true}')  # h today
        output = self.bootstrap()
        self.assertFalse(local.is_symlink())
        self.assertEqual(local.read_text(), '{"local": true}')
        self.assertIn('preserving independent settings.json', output)
        local.unlink()
        local.symlink_to(self.write(self.base / 'elsewhere.json', '{}'))
        self.bootstrap()
        self.assertEqual(self.target('.claude/settings.json'), self.base / 'elsewhere.json')

    def test_codex_row_links_only_an_instance_copy_on_macos(self):
        self.bootstrap(darwin=True)
        self.assertFalse((self.home / '.codex/config.toml').is_symlink())
        (self.home / '.codex/config.toml').unlink()
        instance = self.instance(env=('codex/config.toml',))
        self.bootstrap(darwin=True)
        self.assertEqual(self.target('.codex/config.toml'), instance / 'env/codex/config.toml')
        self.assertIn('web_search', (instance / 'env/codex/config.toml').read_text())  # configure.py writes through

    def test_codex_row_is_skipped_off_macos(self):
        self.instance(env=('codex/config.toml',))
        self.bootstrap()
        self.assertFalse((self.home / '.codex/config.toml').is_symlink())

    def test_codex_rules_row_prefers_the_instance_copy(self):
        self.bootstrap()
        self.assertEqual(self.target('.codex/rules/default.rules'), self.repo / 'codex/rules/default.rules')
        instance = self.instance(env=('codex/rules/default.rules',))
        self.bootstrap()
        self.assertEqual(self.target('.codex/rules/default.rules'), instance / 'env/codex/rules/default.rules')

    KEY_ROWS = ('ssh/id_ed25519', 'ssh/id_ed25519.pub', 'ssh/known_hosts', 'ssh/config', 'ssh/config.macos',
                'claude/daemon/control.key')

    def test_key_rows_link_instance_copies_on_macos(self):
        instance = self.instance(env=self.KEY_ROWS)
        (self.home / '.ssh').mkdir()
        (self.home / '.ssh/id_ed25519').symlink_to(self.repo / 'ssh/id_ed25519')  # the Mac, dangling after split
        local = self.write(self.home / '.ssh/known_hosts', 'local')
        output = self.bootstrap(darwin=True)
        self.assertEqual(self.target('.ssh/id_ed25519'), instance / 'env/ssh/id_ed25519')
        self.assertEqual(self.target('.ssh/id_ed25519.pub'), instance / 'env/ssh/id_ed25519.pub')
        self.assertEqual(self.target('.claude/daemon/control.key'), instance / 'env/claude/daemon/control.key')
        self.assertFalse(local.is_symlink())
        self.assertIn('ssh/known_hosts: preserving independent', output)
        # The platform bootstraps own both ssh configs.
        self.assertFalse((self.home / '.ssh/config').exists())
        self.assertFalse((self.home / '.ssh/config.macos').exists())
        self.assertEqual((instance / 'env/ssh/id_ed25519').stat().st_mode & 0o777, 0o600)
        self.assertEqual((instance / 'env/claude/daemon/control.key').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.home / '.ssh').stat().st_mode & 0o777, 0o700)
        self.assertNotEqual((instance / 'env/ssh/id_ed25519.pub').stat().st_mode & 0o777, 0o600)

    def test_key_rows_are_skipped_off_macos(self):
        self.instance(env=self.KEY_ROWS)
        self.bootstrap()
        self.assertFalse((self.home / '.ssh').exists())
        self.assertFalse((self.home / '.claude/daemon').exists())

    def test_key_rows_need_an_instance(self):
        self.bootstrap(darwin=True)
        self.assertFalse((self.home / '.ssh').exists())


if __name__ == '__main__':
    unittest.main()
