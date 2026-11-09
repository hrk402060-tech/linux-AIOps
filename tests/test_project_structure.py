"""Initialization checks for the project scaffold."""

from pathlib import Path
import unittest

from src.main import get_project_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DIRECTORIES = (
    "backend",
    "config",
    "docs",
    "frontend",
    "scanner",
    "src",
    "tests",
)
REQUIRED_FILES = (".gitignore", "README.md", "docs/project.md")


class ProjectStructureTest(unittest.TestCase):
    """Verify the required project initialization contract."""

    def test_project_name_is_compliant(self) -> None:
        metadata = get_project_metadata()
        self.assertEqual(metadata["name"], "app-linux-vuln-sentinel")
        self.assertLessEqual(len(metadata["name"]), 40)

    def test_required_directories_exist(self) -> None:
        for directory in REQUIRED_DIRECTORIES:
            with self.subTest(directory=directory):
                self.assertTrue((PROJECT_ROOT / directory).is_dir())

    def test_required_files_exist(self) -> None:
        for file_name in REQUIRED_FILES:
            with self.subTest(file_name=file_name):
                self.assertTrue((PROJECT_ROOT / file_name).is_file())


if __name__ == "__main__":
    unittest.main()

