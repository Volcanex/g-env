import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('instance', REPO / 'context/instance.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
VARIABLES = ('G_ENV', 'GENV', 'GENV_DIR', 'G_INSTANCE', 'GCORE_DIR', 'GSITE_GCORE_DIR', 'GCORE_ENV', 'GCORE_MANIFEST')


class ResolverTests(unittest.TestCase):
    """bin/g-instance and context/instance.py are twins: every case checks both."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.framework = self.base / 'g-env'
        self.framework.mkdir()

    def environ(self, framework=True, **values):
        environ = {k: v for k, v in os.environ.items() if k not in VARIABLES}
        if framework:
            environ['G_ENV'] = str(self.framework)
        environ.update(values)
        return environ

    def shell(self, kind, environ, command=REPO / 'bin/g-instance'):
        return subprocess.run([str(command), kind], env=environ, capture_output=True, text=True)

    def resolve(self, kind='dir', **values):
        environ = self.environ(**values)
        python = m.instance(kind, environ)
        run = self.shell(kind, environ)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), str(python or ''), f'{kind} {values}')
        return python

    def test_no_instance_is_none_with_a_hint_and_no_failure(self):
        for kind in ('dir', 'env', 'manifest'):
            self.assertIsNone(self.resolve(kind))
        run = self.shell('dir', self.environ())
        self.assertEqual((run.returncode, run.stdout), (0, ''))
        self.assertIn('no instance found', run.stderr)

    def test_legacy_gcore_sibling(self):
        (self.base / 'gcore').mkdir()
        self.assertEqual(self.resolve(), self.base / 'gcore')
        self.assertEqual(self.resolve('env'), self.base / 'gcore/.env')
        self.assertEqual(self.resolve('manifest'), self.base / 'gcore/manifest.json')

    def test_gp_core_sibling_beats_legacy_gcore(self):
        (self.base / 'gcore').mkdir()
        (self.base / 'gp-core').mkdir()
        self.assertEqual(self.resolve(), self.base / 'gp-core')

    def test_g_instance_beats_siblings(self):
        (self.base / 'gp-core').mkdir()
        self.assertEqual(self.resolve(G_INSTANCE='/elsewhere'), Path('/elsewhere'))
        self.assertEqual(self.resolve('manifest', G_INSTANCE='/elsewhere'), Path('/elsewhere/manifest.json'))

    def test_legacy_variables_win(self):
        (self.base / 'gp-core').mkdir()
        every = dict(G_INSTANCE='/new', GSITE_GCORE_DIR='/site', GCORE_DIR='/dir')
        self.assertEqual(self.resolve(**every), Path('/dir'))
        del every['GCORE_DIR']
        self.assertEqual(self.resolve(**every), Path('/site'))
        self.assertEqual(self.resolve('env', GCORE_ENV='/passport', **every), Path('/passport'))
        self.assertEqual(self.resolve('manifest', GCORE_ENV='/passport', **every), Path('/site/manifest.json'))
        self.assertEqual(self.resolve('manifest', GCORE_MANIFEST='/m.json'), Path('/m.json'))

    def test_framework_fallbacks_and_linked_checkout(self):
        (self.base / 'gp-core').mkdir()
        (self.base / 'genv').symlink_to('g-env')
        for variable in ('G_ENV', 'GENV', 'GENV_DIR'):
            environ = self.environ(framework=False, **{variable: str(self.base / 'genv')})
            self.assertEqual(m.framework(environ), self.framework)
            self.assertEqual(self.shell('framework', environ).stdout.strip(), str(self.framework))
            self.assertEqual(self.resolve(framework=False, **{variable: str(self.base / 'genv')}), self.base / 'gp-core')

    def test_own_checkout_through_a_bin_link(self):
        link = self.base / 'bin/g-instance'
        link.parent.mkdir()
        link.symlink_to(REPO / 'bin/g-instance')
        run = self.shell('framework', self.environ(framework=False), command=link)
        self.assertEqual(run.stdout.strip(), str(REPO))
        self.assertEqual(m.framework({}), REPO)

    def test_service_url_reads_the_manifest_domain(self):
        instance = self.base / 'gp-core'
        instance.mkdir()
        (instance / 'manifest.json').write_text(
            '{"services": [{"id": "brain-api", "domain": "api.example.com"}, {"id": "bare"}]}')
        environ = self.environ()
        self.assertEqual(m.service_url('brain-api', environ), 'https://api.example.com')
        self.assertIsNone(m.service_url('bare', environ))
        self.assertIsNone(m.service_url('missing', environ))
        self.assertIsNone(m.service_url('brain-api', self.environ(G_INSTANCE=str(self.base / 'none'))))

    def test_unknown_kind_is_a_usage_error(self):
        run = self.shell('bogus', self.environ())
        self.assertEqual(run.returncode, 2)
        self.assertIn('usage', run.stderr)


if __name__ == '__main__':
    unittest.main()
