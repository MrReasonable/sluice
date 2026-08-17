"""Tests for company placeholder detection and normalization."""
import pytest

from sluice.core.leads import NON_ANSWER_COMPANIES, fold_company_answer, is_placeholder_company


class TestNonAnswerCompanies:
    """The set of non-answer company strings."""

    def test_set_contains_unknown_and_confidential(self):
        """The set contains the two most common placeholders."""
        assert "unknown" in NON_ANSWER_COMPANIES
        assert "confidential" in NON_ANSWER_COMPANIES

    def test_set_has_expected_count(self):
        """The set has the expected number of members."""
        # Copied verbatim from triage/resolve.py's current _NON_ANSWERS
        assert len(NON_ANSWER_COMPANIES) == 19


class TestFoldCompanyAnswer:
    """Normalisation for company field candidates."""

    @pytest.mark.parametrize("value,expected", [
        ("Example Company", "example company"),
        ("Example Company.", "example company"),
        ("Example Company!", "example company"),
        (" Example Company ", "example company"),
        ("  Example Company.!  ", "example company.!"),  # Trailing spaces after .! mean they stay
        ("EXAMPLE COMPANY", "example company"),
        ("unknown", "unknown"),
        ("Unknown", "unknown"),
        ("UNKNOWN", "unknown"),
        (" Unknown . ", "unknown ."),  # Space after . means . stays
        ("unknown.", "unknown"),
        ("Unknown!", "unknown"),
        ("", ""),
        (None, ""),
    ])
    def test_fold_normalizes(self, value, expected):
        """fold_company_answer produces consistent case/whitespace/punctuation."""
        assert fold_company_answer(value) == expected


class TestIsPlaceholderCompany:
    """Detection of non-answer company fields."""

    def test_blank_is_placeholder(self):
        """Empty string is a placeholder."""
        assert is_placeholder_company("") is True

    def test_whitespace_only_is_placeholder(self):
        """Whitespace-only strings are placeholders."""
        assert is_placeholder_company("   ") is True
        assert is_placeholder_company("\t") is True
        assert is_placeholder_company("\n") is True

    def test_none_is_placeholder(self):
        """None is a placeholder."""
        assert is_placeholder_company(None) is True

    @pytest.mark.parametrize("value", [
        "unknown", "Unknown", "UNKNOWN", " unknown ", "unknown.",
        "confidential", "Confidential", "CONFIDENTIAL", " confidential ", "confidential!",
        "n/a", "N/A", " n/a ", "n/a.",
        "na", "NA", " na ", "na!",
        "not disclosed", "Not Disclosed", "NOT DISCLOSED",
        "not specified", "Not Specified", "NOT SPECIFIED",
        "private", "Private", "PRIVATE",
        "private company", "Private Company", "PRIVATE COMPANY",
        "stealth", "Stealth", "STEALTH",
        "stealth startup", "Stealth Startup", "STEALTH STARTUP",
        "various", "Various", "VARIOUS",
        "various clients", "Various Clients", "VARIOUS CLIENTS",
        "client", "Client", "CLIENT",
        "the client", "The Client", "THE CLIENT",
        "our client", "Our Client", "OUR CLIENT",
        "recruitment agency", "Recruitment Agency", "RECRUITMENT AGENCY",
        "recruiter", "Recruiter", "RECRUITER",
        "agency", "Agency", "AGENCY",
    ])
    def test_all_non_answers_are_placeholders(self, value):
        """Every NON_ANSWER_COMPANIES value (and casings) is a placeholder."""
        assert is_placeholder_company(value) is True

    def test_real_company_is_not_placeholder(self):
        """A real employer name is not a placeholder."""
        assert is_placeholder_company("Example Meridian") is False
        assert is_placeholder_company("Acme Corp") is False

    def test_company_with_placeholder_substring_not_placeholder(self):
        """A company name containing a placeholder word is not itself a placeholder."""
        # E.g., "Unknown Company" is not a placeholder (it's a real company name)
        # only "Unknown" alone is.
        assert is_placeholder_company("Unknown Company") is False
        assert is_placeholder_company("Our Firm") is False
