import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('mirror', Path(__file__).with_name('mirror-claude.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MirrorTests(unittest.TestCase):
    def test_tracked_but_absent_guidance_is_not_a_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            source = root / 'CLAUDE.md'
            source.write_text('sparse or deleted instructions')
            subprocess.run(['git', '-C', tmp, 'add', 'CLAUDE.md'], check=True)
            source.unlink()
            counts = m.mirror(root)
            self.assertEqual(counts['conflict'], 0)
            self.assertFalse(source.exists())
            self.assertFalse((root / 'AGENTS.md').exists())

    def test_untracked_ignored_conflicts_and_immediate_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            (root / '.gitignore').write_text('ignored/\n')
            for name in ['CLAUDE.md', 'nested/CLAUDE.md', 'ignored/CLAUDE.md', 'conflict/CLAUDE.md']:
                p = root / name
                p.parent.mkdir(exist_ok=True)
                p.write_text('guidance')
            independent = root / 'conflict/AGENTS.md'
            independent.write_text('standing policy')
            m.mirror(root, dry_run=True)
            self.assertFalse((root / 'AGENTS.md').exists())
            counts = m.mirror(root)
            self.assertEqual(counts['linked'], 2)
            self.assertEqual(counts['conflict'], 1)
            self.assertFalse((root / 'ignored/AGENTS.md').exists())
            self.assertEqual(independent.read_text(), 'standing policy')
            (root / 'CLAUDE.md').write_text('updated')
            self.assertEqual((root / 'AGENTS.md').read_text(), 'updated')
            self.assertEqual((root / 'AGENTS.md').readlink(), Path('CLAUDE.md'))
            self.assertEqual(m.mirror(root)['current'], 2)

    def test_knowledge_notes_are_not_mirrored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            note = root / 'nerves/example/CLAUDE.md'
            note.parent.mkdir(parents=True)
            note.write_text('---\ntitle: CLAUDE\ntype: note\npermalink: brain/nerves/example/claude\n---\nImported guidance')
            (root / 'CLAUDE.md').write_text('---\ntitle: Repository guidance\n---\nActual instructions')
            counts = m.mirror(root)
            self.assertEqual(counts['linked'], 1)
            self.assertEqual(counts['knowledge_notes'], 1)
            self.assertFalse(note.with_name('AGENTS.md').exists())
            existing = note.with_name('AGENTS.md')
            existing.write_text('Existing corpus note; do not mutate')
            m.mirror(root)
            self.assertEqual(existing.read_text(), 'Existing corpus note; do not mutate')

    def test_agents_only_repo_links_back_to_claude_and_tracks_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            guidance = root / 'nested/AGENTS.md'
            guidance.parent.mkdir()
            guidance.write_text('shared rules')
            counts = m.mirror(root)
            canonical = guidance.with_name('CLAUDE.md')
            self.assertEqual(counts['linked'], 1)
            self.assertEqual(canonical.readlink(), Path('AGENTS.md'))
            guidance.write_text('updated shared rules')
            self.assertEqual(canonical.read_text(), 'updated shared rules')
            self.assertEqual(m.mirror(root)['current'], 1)

    def test_identical_files_converge_on_claude_and_reverse_link_is_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            (root / 'CLAUDE.md').write_text('same')
            (root / 'AGENTS.md').write_text('same')
            self.assertEqual(m.mirror(root)['linked'], 1)
            agents = root / 'AGENTS.md'
            self.assertEqual(agents.readlink(), Path('CLAUDE.md'))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            agents = root / 'AGENTS.md'
            agents.write_text('shared')
            (root / 'CLAUDE.md').symlink_to('AGENTS.md')
            self.assertEqual(m.mirror(root)['current'], 1)
            self.assertEqual(agents.read_text(), 'shared')

    def test_divergent_files_and_symlink_loops_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            (root / 'CLAUDE.md').write_text('claude policy')
            (root / 'AGENTS.md').write_text('agents policy')
            self.assertEqual(m.mirror(root)['conflict'], 1)
            self.assertEqual((root / 'CLAUDE.md').read_text(), 'claude policy')
            self.assertEqual((root / 'AGENTS.md').read_text(), 'agents policy')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            (root / 'CLAUDE.md').symlink_to('AGENTS.md')
            (root / 'AGENTS.md').symlink_to('CLAUDE.md')
            self.assertEqual(m.mirror(root)['conflict'], 1)
            self.assertEqual((root / 'CLAUDE.md').readlink(), Path('AGENTS.md'))
            self.assertEqual((root / 'AGENTS.md').readlink(), Path('CLAUDE.md'))

    def test_agents_named_knowledge_note_is_never_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            note = root / 'notes/AGENTS.md'
            note.parent.mkdir()
            note.write_text('---\ntype: note\npermalink: brain/example\n---\nImported')
            counts = m.mirror(root)
            self.assertEqual(counts['knowledge_notes'], 1)
            self.assertFalse(note.with_name('CLAUDE.md').exists())

    def test_dangling_generated_link_is_classified_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            link = root / 'AGENTS.md'
            link.symlink_to('CLAUDE.md')
            counts = m.mirror(root)
            self.assertEqual(counts['stale_links'], 1)
            self.assertEqual(link.readlink(), Path('CLAUDE.md'))
            self.assertFalse((root / 'CLAUDE.md').exists())

    def test_skill_alias_collision_is_separate_from_guidance_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            (root / '.claude/skills').mkdir(parents=True)
            (root / '.agents/skills').mkdir(parents=True)
            counts = m.mirror(root)
            self.assertEqual(counts['skill_conflict'], 1)
            self.assertNotIn('conflict', counts)
            self.assertTrue((root / '.agents/skills').is_dir())

    def test_note_metadata_must_be_complete_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'CLAUDE.md'
            for content in [
                '# Guidance\n---\ntype: note\npermalink: example\n---\n',
                '---\ntitle: Guide\n---\ntype: note\npermalink: example\n',
                '---\ntype: note\npermalink: example\n',
                '---\ntype: note\n---\n',
            ]:
                source.write_text(content)
                self.assertFalse(m.is_knowledge_note(source), content)
            source.write_bytes(b'---\r\ntype: "note"\r\npermalink: "brain/example"\r\n---\r\n')
            self.assertTrue(m.is_knowledge_note(source))

    def test_discovery_includes_registered_worktree_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'projects/main'
            root.mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'CLAUDE.md').write_text('tracked')
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'initial'], check=True)
            worktree = base / 'elsewhere'
            subprocess.run(['git', '-C', str(root), 'worktree', 'add', '-q', '--detach', str(worktree)], check=True)
            self.assertEqual(set(m.discover([base / 'projects'])), {root, worktree})
            self.assertEqual(m.mirror(worktree)['linked'], 1)

    def test_server_wide_discovery_is_unbounded_but_prunes_system_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / 'deep/nested/g-system/project'
            repo.mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            shallow = base / 'deep/project'
            shallow.mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(shallow)], check=True)
            nested = repo / 'components/standalone'
            nested.mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(nested)], check=True)
            found = m.discover([base], depth=None, server_wide=True)
            self.assertEqual(set(found), {repo, shallow, nested})
            self.assertNotIn(repo, m.discover([base], depth=3))

    def test_invalid_git_markers_are_reported_and_nested_repositories_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            broken = base / 'stale-worktree'
            broken.mkdir()
            (broken / '.git').write_text('gitdir: /missing/worktree/admin')
            nested = broken / 'source/real-repo'
            nested.mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(nested)], check=True)
            skipped = set()
            found = m.discover([base], depth=None, server_wide=True, skipped_invalid=skipped)
            self.assertEqual(set(found), {nested})
            self.assertEqual(skipped, {broken})

    def test_cli_reports_invalid_explicit_repo_without_counting_it_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / 'stale-worktree'
            broken.mkdir()
            (broken / '.git').write_text('gitdir: /missing/worktree/admin')
            result = subprocess.run([sys.executable, str(m.__file__), str(broken), '--dry-run'],
                                    check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Skipped invalid repository marker:', result.stdout)
            self.assertIn('invalid_skipped=1 failed=0', result.stdout)

    def test_server_scan_mirrors_nonrepo_tool_roots_without_cross_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'home'
            claude = base / '.claude'
            codex = base / '.codex'
            claude.mkdir(parents=True)
            codex.mkdir()
            external = Path(tmp) / 'shared-behavior.md'
            external.write_text('shared behavior')
            claude_source = claude / 'CLAUDE.md'
            claude_source.symlink_to(external)
            codex_source = codex / 'AGENTS.md'
            codex_source.write_text('Codex adapter')
            guidance_dirs = set()
            m.discover([base], depth=None, server_wide=True, guidance_dirs=guidance_dirs)
            counts, failures = m.mirror_all([], guidance_dirs)
            self.assertEqual(failures, [])
            self.assertEqual(counts['linked'], 2)
            self.assertEqual((claude / 'AGENTS.md').readlink(), Path('CLAUDE.md'))
            self.assertEqual((codex / 'CLAUDE.md').readlink(), Path('AGENTS.md'))
            self.assertTrue(claude_source.is_symlink())
            self.assertEqual(external.read_text(), 'shared behavior')

    def test_server_scan_deduplicates_repo_guidance_and_skips_external_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            (repo / 'CLAUDE.md').write_text('repository instructions')
            note_dir = base / 'brain/notes'
            note_dir.mkdir(parents=True)
            note = note_dir / 'AGENTS.md'
            note.write_text('---\ntype: note\npermalink: brain/example\n---\nImported')
            guidance_dirs = set()
            repos = m.discover([base], depth=None, server_wide=True,
                               guidance_dirs=guidance_dirs)
            counts, failures = m.mirror_all(repos, guidance_dirs)
            self.assertEqual(failures, [])
            self.assertEqual(counts['linked'], 1)
            self.assertEqual(counts['knowledge_notes'], 1)
            self.assertEqual((repo / 'AGENTS.md').readlink(), Path('CLAUDE.md'))
            self.assertFalse((note_dir / 'CLAUDE.md').exists())


if __name__ == '__main__':
    unittest.main()
