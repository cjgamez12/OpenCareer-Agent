"""Tests for the profile and resume helpers."""
import pytest

from opencareer import applicant


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "Alex_Rivera_SWE_Resume copy.pdf").write_bytes(b"%PDF-fake")
    (tmp_path / "Alex_Rivera_Resume (1) copy.pdf").write_bytes(b"%PDF-fake")
    (tmp_path / "notes.docx").write_bytes(b"ignored")
    return tmp_path


def test_lists_only_resume_files(folder):
    assert [p.name for p in applicant.list_resumes(folder)] == [
        "Alex_Rivera_Resume (1) copy.pdf", "Alex_Rivera_SWE_Resume copy.pdf"]


@pytest.mark.parametrize("wanted, found", [
    ("Alex_Rivera_SWE_Resume.pdf", "Alex_Rivera_SWE_Resume copy.pdf"),
    ("alex rivera swe resume.pdf", "Alex_Rivera_SWE_Resume copy.pdf"),
    ("Alex_Rivera_Resume.pdf", "Alex_Rivera_Resume (1) copy.pdf"),
])
def test_find_resume_ignores_copy_numbers_and_case(folder, wanted, found):
    assert applicant.find_resume(folder, wanted).name == found


def test_find_resume_missing(folder):
    assert applicant.find_resume(folder, "Networking.pdf") is None


def test_resume_by_filename_is_exact(folder):
    assert applicant.resume_by_filename(folder, "../etc/passwd") is None


def test_save_profile_validates(tmp_path):
    with pytest.raises(ValueError):
        applicant.save_profile_text(tmp_path, "- just\n- a list\n")
    applicant.save_profile_text(tmp_path, "name: Alex")
    assert applicant.read_profile_text(tmp_path) == "name: Alex\n"


def test_blank_template_is_valid_yaml():
    assert "base_resumes" in applicant.parse_profile(applicant.BLANK_TEMPLATE)
