import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('install-context.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo, self.home, self.gcore = (self.base / p for p in ('genv', 'home', 'gcore'))
        for path, content in {'context/shared.md': 'shared rules', 'context/session-context.py': 'loader',
                              'claude/root.md': 'Claude adapter', 'codex/AGENTS.md': 'Codex adapter',
                              'codex/session-context.py': 'compat loader', 'skills/common/SKILL.md': 'common'}.items():
            self.write(self.repo / path, content)
        (self.repo / 'claude/CLAUDE.md').symlink_to('../context/shared.md')
        (self.repo / 'claude/AGENTS.md').symlink_to('CLAUDE.md')
        self.write(self.gcore / 'conventions.md', 'conventions')
        self.write(self.gcore / 'CLAUDE.md', 'topology')

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def install(self):
        m.install(self.repo, self.home, self.gcore)

    def test_fresh_home_and_idempotence(self):
        self.install()
        self.assertFalse((self.home / '.agents/skills').is_symlink())
        self.assertEqual((self.home / '.claude/skills').resolve(), self.home / '.agents/skills')
        config = (self.home / '.agents/context.json').read_bytes()
        self.install()
        self.assertEqual((self.home / '.agents/context.json').read_bytes(), config)
        self.assertFalse(list(self.home.rglob('*.pre-neutral*')))

    def test_migrate_host_imports_and_unmanaged_skills_without_claude_dependency(self):
        self.write(self.home / '.claude/extra.md', 'additional host facts')
        original = f'@{self.gcore}/conventions.md\n@{self.gcore}/CLAUDE.md\n@{self.repo}/claude/AGENTS.md\n@extra.md\nHost h: preserve me.\n'
        self.write(self.home / '.claude/CLAUDE.md', original)
        self.write(self.home / '.claude/skills/private/SKILL.md', 'private skill')
        (self.home / '.agents').mkdir()
        (self.home / '.agents/skills').symlink_to('../.claude/skills')
        self.install()
        host = (self.home / '.agents/host.md').read_text()
        self.assertIn('Host h: preserve me.', host)
        self.assertIn('@' + str(self.home / '.agents/imports/extra.md'), host)
        self.assertEqual((self.home / '.agents/imports/extra.md').read_text(), 'additional host facts')
        self.assertNotIn(str(self.gcore), host)
        self.assertEqual((self.home / '.claude/CLAUDE.md.pre-neutral').read_text(), original)
        self.assertEqual((self.home / '.agents/skills/private/SKILL.md').read_text(), 'private skill')
        self.assertEqual((self.home / '.claude/skills').resolve(), self.home / '.agents/skills')
        self.install()
        self.assertEqual((self.home / '.agents/host.md').read_text(), host)

    def test_nested_host_imports_and_cycles_survive_removing_claude(self):
        self.write(self.home / '.claude/CLAUDE.md', '@extra.md\nHost facts.')
        self.write(self.home / '.claude/extra.md', '@CLAUDE.md\n@nested/more.md\nExtra facts.')
        self.write(self.home / '.claude/nested/more.md', 'Deep facts.')
        self.install()
        shutil.rmtree(self.home / '.claude')
        self.assertIn('Deep facts.', (self.home / '.agents/imports/nested/more.md').read_text())
        self.assertIn('@' + str(self.home / '.agents/host.md'), (self.home / '.agents/imports/extra.md').read_text())
        self.assertNotIn('/.claude/', (self.home / '.agents/host.md').read_text())
        self.assertTrue((self.home / '.agents/skills/common/SKILL.md').is_file())

    def test_synced_skill_alias_survives_removing_claude(self):
        legacy = self.home / '.claude/skills'
        self.write(legacy / 'synced/ids/example/SKILL.md', 'synced private skill')
        (legacy / 'example').symlink_to(legacy / 'synced/ids/example')
        outside = self.write(self.base / 'other-repo/skills/external/SKILL.md', 'external')
        (legacy / 'external').symlink_to(outside.parent)
        self.install()
        shutil.rmtree(self.home / '.claude')
        self.assertEqual((self.home / '.agents/skills/example/SKILL.md').read_text(), 'synced private skill')
        self.assertEqual((self.home / '.agents/skills/external/SKILL.md').read_text(), 'external')

    def test_whole_client_root_links_do_not_mutate_repository(self):
        self.home.mkdir()
        (self.repo / 'claude/skills').symlink_to('../skills')
        self.write(self.repo / 'codex/config.toml', 'model = "preserved"')
        (self.home / '.claude').symlink_to(self.repo / 'claude')
        (self.home / '.codex').symlink_to(self.repo / 'codex')
        self.install()
        self.assertFalse((self.home / '.claude').is_symlink())
        self.assertFalse((self.home / '.codex').is_symlink())
        self.assertEqual((self.repo / 'claude/CLAUDE.md').readlink(), Path('../context/shared.md'))
        self.assertEqual((self.repo / 'claude/skills').readlink(), Path('../skills'))
        self.assertEqual((self.home / '.codex/config.toml').read_text(), 'model = "preserved"')
        self.assertEqual((self.home / '.agents/skills/common').resolve(), self.repo / 'skills/common')
        self.install()

    def test_existing_host_and_independent_config_survive(self):
        self.write(self.home / '.agents/host.md', 'neutral facts\n')
        self.write(self.home / '.claude/CLAUDE.md', 'legacy facts\n')
        self.write(self.home / '.agents/context.json', json.dumps({'custom': True, 'sources': {'topology': '/independent/topology.md'}}))
        self.write(self.home / '.codex/config.toml', 'model = "custom"\n')
        self.write(self.home / '.codex/hooks.json', '{"my-hook": true}')
        self.install()
        host = (self.home / '.agents/host.md').read_text()
        self.assertIn('neutral facts', host)
        self.assertIn('legacy facts', host)
        self.assertEqual(json.loads((self.home / '.agents/context.json').read_text())['sources']['topology'], '/independent/topology.md')
        self.assertEqual((self.home / '.codex/config.toml').read_text(), 'model = "custom"\n')
        self.assertEqual((self.home / '.codex/hooks.json').read_text(), '{"my-hook": true}')

    def test_divergent_skills_preserved_and_missing_managed_links_added(self):
        self.write(self.home / '.agents/skills/private/SKILL.md', 'neutral private')
        self.write(self.home / '.claude/skills/private/SKILL.md', 'Claude private')
        self.install()
        self.assertEqual((self.home / '.agents/skills/private/SKILL.md').read_text(), 'neutral private')
        self.assertEqual((self.home / '.claude/skills/private/SKILL.md').read_text(), 'Claude private')
        self.assertEqual((self.home / '.claude/skills/common/SKILL.md').read_text(), 'common')

    def test_stale_managed_skill_removed_but_independent_replacement_survives(self):
        self.install()
        (self.repo / 'skills/common/SKILL.md').unlink()
        self.install()
        self.assertFalse((self.home / '.agents/skills/common').is_symlink())
        self.write(self.repo / 'skills/common/SKILL.md', 'returned')
        self.install()
        (self.home / '.agents/skills/common').unlink()
        self.write(self.home / '.agents/skills/common/SKILL.md', 'user override')
        self.install()
        self.assertEqual((self.home / '.agents/skills/common/SKILL.md').read_text(), 'user override')


    def sources(self):
        return json.loads((self.home / '.agents/context.json').read_text())['sources']

    def test_sources_move_to_the_instance_only_from_the_old_default(self):
        instance = self.base / 'gp-core'
        self.write(instance / 'conventions.md', 'conventions')
        self.write(self.home / '.agents/context.json', json.dumps({'sources': {
            'conventions': str(self.gcore / 'conventions.md'), 'topology': '/independent/topology.md'}}))
        m.install(self.repo, self.home, instance)
        self.assertEqual(self.sources()['conventions'], str(instance.resolve() / 'conventions.md'))
        self.assertEqual(self.sources()['topology'], '/independent/topology.md')

    def test_old_default_stays_until_the_instance_file_exists(self):
        instance = self.base / 'gp-core'
        instance.mkdir()
        old = {'conventions': str(self.gcore / 'conventions.md'), 'topology': str(self.gcore / 'CLAUDE.md')}
        self.write(self.home / '.agents/context.json', json.dumps({'sources': old}))
        m.install(self.repo, self.home, instance)
        self.assertEqual({k: v for k, v in self.sources().items() if k in old}, old)

    def test_old_default_through_a_linked_checkout(self):
        (self.base / 'g-env').mkdir()
        linked = self.base / 'linked/genv'
        linked.parent.mkdir()
        linked.symlink_to(self.repo)
        instance = self.base / 'gp-core'
        self.write(instance / 'CLAUDE.md', 'topology')
        self.write(self.home / '.agents/context.json', json.dumps({'sources': {'topology': str(self.base / 'linked/gcore/CLAUDE.md')}}))
        m.install(linked, self.home, instance)
        self.assertEqual(self.sources()['topology'], str(instance.resolve() / 'CLAUDE.md'))

    def test_no_instance_installs_framework_sources_only(self):
        m.install(self.repo, self.home, None)
        self.assertEqual(set(self.sources()), {'host', 'shared'})

    def test_cli_accepts_legacy_gcore_and_hints_without_an_instance(self):
        script = Path(__file__).with_name('install-context.py')
        environ = {k: v for k, v in os.environ.items() if not k.startswith(('G_', 'GENV', 'GCORE', 'GSITE'))}
        run = subprocess.run([sys.executable, script, '--home', self.home, '--repo', self.repo, '--gcore', self.gcore],
                             env=environ, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.sources()['conventions'], str(self.gcore.resolve() / 'conventions.md'))
        shutil.rmtree(self.gcore)
        (self.home / '.agents/context.json').unlink()
        run = subprocess.run([sys.executable, script, '--home', self.home, '--repo', self.repo],
                             env=environ, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('no instance found', run.stderr)
        self.assertEqual(set(self.sources()), {'host', 'shared'})


if __name__ == '__main__':
    unittest.main()
