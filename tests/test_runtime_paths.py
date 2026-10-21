"""Explorer-launched installations must not depend on the agent's PATH."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'skills/quota-resume/scripts'))
import runtime_paths


class RuntimePaths(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root/'Local'),
            'APPDATA': str(self.root/'Roaming'), 'ProgramFiles': str(self.root/'Programs'),
            'TASK_RESUME_CODEX': str(self.root/'missing.exe')})
        env.start()
        self.addCleanup(env.stop)
        which = patch.object(runtime_paths.shutil, 'which', return_value=None)
        which.start()
        self.addCleanup(which.stop)

    def cli(self, relative):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_desktop_cli_is_found_without_path_and_newest_cache_is_preferred(self):
        old = self.cli('Local/OpenAI/Codex/bin/old/codex.exe')
        new = self.cli('Local/OpenAI/Codex/bin/new/codex.exe')
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        self.assertEqual(runtime_paths.find_codex(), str(new))

    def test_packaged_client_is_found_if_extracted_cache_does_not_exist(self):
        packaged = self.cli('Programs/WindowsApps/OpenAI.Codex_1.0_x64/app/resources/codex.exe')
        self.assertEqual(runtime_paths.find_codex(), str(packaged))

    def test_installation_checks_cli_identity_and_skips_an_invalid_executable(self):
        wrong = self.cli('desktop-app.exe')
        cli = self.cli('Local/OpenAI/Codex/bin/current/codex.exe')
        def probe(argv, **kwargs):
            self.assertEqual(argv[1:], ['--version'])
            self.assertNotIn('shell', kwargs)
            return subprocess.CompletedProcess(argv, 0, 'Desktop app' if argv[0] == str(wrong) else 'codex-cli 0.163.0')
        with patch.dict(os.environ, {'TASK_RESUME_CODEX': str(wrong)}), patch.object(runtime_paths.subprocess, 'run', side_effect=probe):
            self.assertEqual(runtime_paths.find_codex(check=True), str(cli))

    def test_missing_cli_gives_an_actionable_error_without_touching_user_path(self):
        original = os.environ.get('PATH')
        with self.assertRaisesRegex(ValueError, '请先启动一次 Codex 客户端'):
            runtime_paths.find_codex()
        self.assertEqual(os.environ.get('PATH'), original)


if __name__ == '__main__':
    unittest.main()
