"""Tests for SEC EDGAR Form C narrative text extraction."""

from __future__ import annotations

import pytest

from startuplens.pipelines.sec_edgar_text import (
    _parse_submission_text,
    extract_narrative_from_html,
    extract_narrative_from_xml,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_FORM_C_XML = """\
<edgarSubmission xmlns="http://www.sec.gov/edgar/formc" \
xmlns:com="http://www.sec.gov/edgar/common">
  <formData>
    <issuerInformation>
      <issuerInfo>
        <nameOfIssuer>TestCo Inc</nameOfIssuer>
        <legalStatus>
          <legalStatusForm>Corporation</legalStatusForm>
          <jurisdictionOrganization>DE</jurisdictionOrganization>
        </legalStatus>
      </issuerInfo>
    </issuerInformation>
    <offeringInformation>
      <compensationAmount>7 percent</compensationAmount>
      <securityOfferedType>Other</securityOfferedType>
      <securityOfferedOtherDesc>SAFE</securityOfferedOtherDesc>
      <descOverSubscription>At issuer discretion</descOverSubscription>
      <priceDeterminationMethod>Based on valuation cap</priceDeterminationMethod>
    </offeringInformation>
  </formData>
</edgarSubmission>"""

SAMPLE_HTML = """\
<html>
<head><title>Offering</title><style>body { color: black; }</style></head>
<body>
<h1>Business Plan</h1>
<p>We are building an AI-powered widget that helps small businesses manage inventory.</p>
<p>Our target market is the $50B SMB software market.</p>
<script>console.log('tracking');</script>
<div>
  <h2>Use of Proceeds</h2>
  <p>60% engineering, 30% marketing, 10% operations.</p>
</div>
</body>
</html>"""

SAMPLE_SUBMISSION_TEXT = """\
<SEC-DOCUMENT>
<DOCUMENT>
<TYPE>C
<SEQUENCE>1
<FILENAME>primary_doc.xml
<DESCRIPTION>
<TEXT>
""" + SAMPLE_FORM_C_XML + """
</TEXT>
</DOCUMENT>
<DOCUMENT>
<TYPE>EX-99
<SEQUENCE>2
<FILENAME>offering.htm
<DESCRIPTION>OFFERING MEMORANDUM
<TEXT>
""" + SAMPLE_HTML + """
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""


# ---------------------------------------------------------------------------
# XML extraction
# ---------------------------------------------------------------------------


class TestExtractNarrativeFromXml:
    def test_extracts_company_name(self):
        sections = extract_narrative_from_xml(SAMPLE_FORM_C_XML)
        assert sections["company_name"] == "TestCo Inc"

    def test_extracts_legal_status(self):
        sections = extract_narrative_from_xml(SAMPLE_FORM_C_XML)
        assert "Corporation" in sections["legal_status"]
        assert "DE" in sections["legal_status"]

    def test_extracts_security_type(self):
        sections = extract_narrative_from_xml(SAMPLE_FORM_C_XML)
        assert sections["security_type"] == "SAFE"

    def test_extracts_oversubscription(self):
        sections = extract_narrative_from_xml(SAMPLE_FORM_C_XML)
        assert "issuer discretion" in sections["oversubscription_policy"]

    def test_invalid_xml_returns_empty(self):
        sections = extract_narrative_from_xml("not xml at all")
        assert sections == {}


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------


class TestExtractNarrativeFromHtml:
    def test_extracts_text(self):
        text = extract_narrative_from_html(SAMPLE_HTML)
        assert "AI-powered widget" in text
        assert "Use of Proceeds" in text

    def test_strips_scripts(self):
        text = extract_narrative_from_html(SAMPLE_HTML)
        assert "console.log" not in text

    def test_strips_styles(self):
        text = extract_narrative_from_html(SAMPLE_HTML)
        assert "color: black" not in text

    def test_empty_html(self):
        text = extract_narrative_from_html("<html><body></body></html>")
        assert text == ""


# ---------------------------------------------------------------------------
# SGML submission parsing
# ---------------------------------------------------------------------------


class TestParseSubmissionText:
    def test_parses_documents(self):
        docs = _parse_submission_text(SAMPLE_SUBMISSION_TEXT)
        assert len(docs) == 2

    def test_primary_doc_type(self):
        docs = _parse_submission_text(SAMPLE_SUBMISSION_TEXT)
        assert docs[0]["type"] == "C"
        assert docs[0]["filename"] == "primary_doc.xml"

    def test_exhibit_type(self):
        docs = _parse_submission_text(SAMPLE_SUBMISSION_TEXT)
        assert docs[1]["type"] == "EX-99"
        assert docs[1]["description"] == "OFFERING MEMORANDUM"

    def test_exhibit_has_content(self):
        docs = _parse_submission_text(SAMPLE_SUBMISSION_TEXT)
        assert "AI-powered widget" in docs[1]["content"]

    def test_empty_text(self):
        docs = _parse_submission_text("")
        assert docs == []


# ---------------------------------------------------------------------------
# Text length filtering
# ---------------------------------------------------------------------------


class TestTextLengthFiltering:
    def test_short_text_filtered(self):
        """Text under 100 chars should not be stored."""
        short_html = "<html><body><p>Short.</p></body></html>"
        text = extract_narrative_from_html(short_html)
        assert len(text) < 100

    def test_long_text_passes(self):
        text = extract_narrative_from_html(SAMPLE_HTML)
        assert len(text) >= 100
