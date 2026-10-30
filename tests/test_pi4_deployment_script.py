import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SELECTOR = PROJECT_ROOT / "scripts" / "select_glib_package.sh"
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup_pi4.sh"


class Pi4DeploymentScriptTests(unittest.TestCase):
    def _select_glib_package(self, version_id: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            os_release = Path(temporary_directory) / "os-release"
            os_release.write_text(
                f'ID=debian\nVERSION_ID="{version_id}"\n',
                encoding="utf-8",
            )
            return subprocess.run(
                ["bash", str(PACKAGE_SELECTOR), str(os_release)],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_selects_glib_runtime_for_supported_debian_releases(self):
        expected_packages = {
            "12": "libglib2.0-0",
            "13": "libglib2.0-0t64",
        }

        for version_id, expected_package in expected_packages.items():
            with self.subTest(version_id=version_id):
                result = self._select_glib_package(version_id)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected_package)

    def test_service_environment_files_are_optional(self):
        setup_source = SETUP_SCRIPT.read_text(encoding="utf-8")

        expected_environment_files = (
            "EnvironmentFile=-${INSTALL_HOME}/.config/qos-edgeai/snmp.env",
            "EnvironmentFile=-${INSTALL_HOME}/.config/qos-edgeai/ssh.env",
            "EnvironmentFile=-${INSTALL_HOME}/.env.telegram",
            "EnvironmentFile=-${PROJECT_DIR}/.env.telegram",
        )

        for environment_file in expected_environment_files:
            with self.subTest(environment_file=environment_file):
                self.assertIn(environment_file, setup_source)


if __name__ == "__main__":
    unittest.main()
