"""The deployment files pin what the server installs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_requirements_pin_the_server_versions_exactly():
    lines = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines() if l.strip()]
    assert lines == ["streamlit==1.67.0", "pandas==3.1.1", "numpy==2.5.4", "sqlalchemy==2.1.8",
                     "pymysql==1.2.4", "cryptography==50.0.2", "bcrypt==5.0.0"]


def test_python_version_file_names_3_14():
    assert (ROOT / ".python-version").read_text().strip() == "3.14"
