"""
OPDS feed format validators for OPDS 1.2 (Atom XML) and OPDS 2.0 (JSON).

Validates feeds against the OPDS specification requirements:
  - OPDS 1.2: https://specs.opds.io/opds-1.2
  - OPDS 2.0: https://drafts.opds.io/opds-2.0
  - Atom:     RFC 4287

Inspired by the W3C feedvalidator project structure, but purpose-built
for OPDS comic catalog feeds.
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Atom namespace
ATOM_NS = "http://www.w3.org/2005/Atom"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
PSE_NS = "http://vaemendis.net/opds-pse/ns"

# Valid OPDS acquisition relations
OPDS_ACQUISITION_RELS = {
    "http://opds-spec.org/acquisition",
    "http://opds-spec.org/acquisition/open-access",
    "http://opds-spec.org/acquisition/borrow",
    "http://opds-spec.org/acquisition/buy",
    "http://opds-spec.org/acquisition/sample",
    "http://opds-spec.org/acquisition/subscribe",
}

# OPDS image relations
OPDS_IMAGE_RELS = {
    "http://opds-spec.org/image",
    "http://opds-spec.org/image/thumbnail",
}

# RFC 3339 / ISO 8601 pattern (basic check)
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"([.]\d+)?"
    r"(Z|[+-]\d{2}:\d{2})$"
)


@dataclass
class ValidationResult:
    """Accumulates validation errors and warnings."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, msg: str):
        self.errors.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def __str__(self):
        lines = []
        for e in self.errors:
            lines.append(f"ERROR: {e}")
        for w in self.warnings:
            lines.append(f"WARN:  {w}")
        return "\n".join(lines) if lines else "Valid"

    def assert_valid(self):
        """Raise AssertionError with all errors if invalid."""
        if not self.is_valid:
            raise AssertionError(
                f"OPDS validation failed with {len(self.errors)} error(s):\n"
                + "\n".join(f"  - {e}" for e in self.errors)
            )


def _is_valid_rfc3339(ts: str) -> bool:
    """Check if a timestamp string is valid RFC 3339."""
    if not RFC3339_RE.match(ts):
        return False
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _atom(tag: str) -> str:
    """Qualify a tag name with the Atom namespace."""
    return f"{{{ATOM_NS}}}{tag}"


# ─────────────────────────────────────────────────────────
#  OPDS 1.2  (Atom XML)
# ─────────────────────────────────────────────────────────

def validate_opds1_feed(xml_bytes: bytes) -> ValidationResult:
    """Validate an OPDS 1.2 Atom feed against spec requirements.

    Checks:
      - Well-formed XML
      - Root element is atom:feed
      - Required feed elements: id, title, updated
      - updated is valid RFC 3339
      - self link present with correct type
      - start link present
      - search link present (OPDS requirement for catalogs with search)
      - Each entry has required elements: id, title, updated
      - Entry updated values are valid RFC 3339
      - Navigation entries have subsection link with correct type
      - Acquisition entries have acquisition link with OPDS rel
      - Link href attributes are non-empty
      - Link type attributes are present where required
    """
    result = ValidationResult()

    # Parse XML
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        result.error(f"Malformed XML: {e}")
        return result

    # Root must be atom:feed
    if root.tag != _atom("feed"):
        result.error(f"Root element must be atom:feed, got {root.tag}")
        return result

    # Required feed-level elements
    _check_required_text(root, "id", result, "Feed")
    _check_required_text(root, "title", result, "Feed")
    updated_el = root.find(_atom("updated"))
    if updated_el is None or not (updated_el.text or "").strip():
        result.error("Feed missing required <updated> element")
    elif not _is_valid_rfc3339(updated_el.text.strip()):
        result.error(f"Feed <updated> is not valid RFC 3339: {updated_el.text}")

    # Check feed-level links
    links = root.findall(_atom("link"))
    _check_feed_links(links, result)

    # Validate entries
    entries = root.findall(_atom("entry"))
    for i, entry in enumerate(entries):
        _validate_opds1_entry(entry, i, result)

    return result


def _check_required_text(parent, tag: str, result: ValidationResult, ctx: str):
    """Check that a required text element exists and is non-empty."""
    el = parent.find(_atom(tag))
    if el is None or not (el.text or "").strip():
        result.error(f"{ctx} missing required <{tag}> element")


def _check_feed_links(links: list, result: ValidationResult):
    """Validate feed-level links per OPDS 1.2 spec."""
    rels = {}
    for link in links:
        rel = link.get("rel", "")
        href = link.get("href", "")
        link_type = link.get("type", "")
        rels.setdefault(rel, []).append(link)
        if not href:
            result.error(f"Feed link rel='{rel}' has empty href")

    # self link (SHOULD per spec, but we treat as error for our server)
    if "self" not in rels:
        result.error("Feed missing <link rel='self'>")
    else:
        self_link = rels["self"][0]
        self_type = self_link.get("type", "")
        if "atom+xml" not in self_type:
            result.warn(f"Feed self link type should contain 'atom+xml', got '{self_type}'")

    # start link
    if "start" not in rels:
        result.warn("Feed missing <link rel='start'> (recommended by OPDS 1.2)")

    # search link
    if "search" not in rels:
        result.warn("Feed missing <link rel='search'> (recommended for searchable catalogs)")
    else:
        search_link = rels["search"][0]
        search_type = search_link.get("type", "")
        if "opensearchdescription" not in search_type:
            result.warn(f"Search link type should be 'application/opensearchdescription+xml', got '{search_type}'")


def _validate_opds1_entry(entry, index: int, result: ValidationResult):
    """Validate a single OPDS 1.2 entry."""
    ctx = f"Entry[{index}]"

    # Required entry elements (RFC 4287 §4.1.2)
    _check_required_text(entry, "id", result, ctx)
    _check_required_text(entry, "title", result, ctx)

    updated_el = entry.find(_atom("updated"))
    if updated_el is None or not (updated_el.text or "").strip():
        result.error(f"{ctx} missing required <updated> element")
    elif not _is_valid_rfc3339(updated_el.text.strip()):
        result.error(f"{ctx} <updated> is not valid RFC 3339: {updated_el.text}")

    # Check entry links
    links = entry.findall(_atom("link"))
    if not links:
        result.error(f"{ctx} has no <link> elements")
        return

    rels = [link.get("rel", "") for link in links]
    has_subsection = "subsection" in rels
    has_acquisition = any(r.startswith("http://opds-spec.org/acquisition") for r in rels)

    # Every entry must be either navigation (subsection) or acquisition
    if not has_subsection and not has_acquisition:
        result.error(
            f"{ctx} is neither navigation (subsection link) nor acquisition "
            f"(acquisition link). Rels found: {rels}"
        )

    # Validate individual links
    for link in links:
        rel = link.get("rel", "")
        href = link.get("href", "")
        link_type = link.get("type", "")

        if not href:
            result.error(f"{ctx} link rel='{rel}' has empty href")

        # Subsection links must have correct type
        if rel == "subsection":
            if "atom+xml" not in link_type:
                result.warn(f"{ctx} subsection link type should contain 'atom+xml', got '{link_type}'")

        # Acquisition links must have a type
        if rel.startswith("http://opds-spec.org/acquisition") and not link_type:
            result.error(f"{ctx} acquisition link rel='{rel}' missing type attribute")

        # Image links should have image MIME type
        if rel in OPDS_IMAGE_RELS and link_type and not link_type.startswith("image/"):
            result.warn(f"{ctx} image link rel='{rel}' has non-image type '{link_type}'")


# ─────────────────────────────────────────────────────────
#  OPDS 2.0  (JSON)
# ─────────────────────────────────────────────────────────

def validate_opds2_feed(data: dict) -> ValidationResult:
    """Validate an OPDS 2.0 JSON feed against spec requirements.

    Checks:
      - metadata present with title
      - modified is valid RFC 3339 (if present)
      - links present with self link
      - self link has correct type
      - At least one of: navigation, publications, groups
      - Navigation items have title and href
      - Publications have metadata.title and links
      - Publication links include acquisition link
      - Publication modified timestamps are valid RFC 3339
      - Pagination metadata fields are integers (if present)
    """
    result = ValidationResult()

    if not isinstance(data, dict):
        result.error(f"Feed must be a JSON object, got {type(data).__name__}")
        return result

    # metadata
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        result.error("Feed missing 'metadata' object")
    else:
        if not metadata.get("title"):
            result.error("Feed metadata missing 'title'")
        modified = metadata.get("modified")
        if modified and not _is_valid_rfc3339(modified):
            result.error(f"Feed metadata 'modified' is not valid RFC 3339: {modified}")

        # Pagination fields should be integers
        for field_name in ("numberOfItems", "itemsPerPage", "currentPage"):
            val = metadata.get(field_name)
            if val is not None and not isinstance(val, int):
                result.error(f"Feed metadata '{field_name}' should be an integer, got {type(val).__name__}")

    # links
    links = data.get("links")
    if not isinstance(links, list) or not links:
        result.error("Feed missing 'links' array")
    else:
        _check_opds2_links(links, result, "Feed")

    # Must have at least one collection key (may be empty for e.g. empty search)
    has_nav = "navigation" in data
    has_pubs = "publications" in data
    has_groups = "groups" in data
    if not has_nav and not has_pubs and not has_groups:
        result.error("Feed must contain at least one of: 'navigation', 'publications', 'groups'")

    # Validate navigation items
    for i, nav in enumerate(data.get("navigation") or []):
        _validate_opds2_nav(nav, i, result)

    # Validate publications
    for i, pub in enumerate(data.get("publications") or []):
        _validate_opds2_publication(pub, i, result)

    return result


def _check_opds2_links(links: list, result: ValidationResult, ctx: str):
    """Validate an OPDS 2.0 links array."""
    rels = {}
    for link in links:
        if not isinstance(link, dict):
            result.error(f"{ctx} links contains non-object element")
            continue
        rel = link.get("rel", "")
        href = link.get("href", "")
        rels.setdefault(rel, []).append(link)

        if not href:
            result.error(f"{ctx} link rel='{rel}' has empty href")

    # self link required
    if "self" not in rels:
        result.error(f"{ctx} links missing 'self' link")
    else:
        self_link = rels["self"][0]
        self_type = self_link.get("type", "")
        if self_type != "application/opds+json":
            result.warn(f"{ctx} self link type should be 'application/opds+json', got '{self_type}'")


def _validate_opds2_nav(nav: dict, index: int, result: ValidationResult):
    """Validate a single OPDS 2.0 navigation item."""
    ctx = f"Navigation[{index}]"
    if not isinstance(nav, dict):
        result.error(f"{ctx} must be a Link Object (dict)")
        return
    if not nav.get("title"):
        result.error(f"{ctx} missing required 'title'")
    if not nav.get("href"):
        result.error(f"{ctx} missing required 'href'")


def _validate_opds2_publication(pub: dict, index: int, result: ValidationResult):
    """Validate a single OPDS 2.0 publication."""
    ctx = f"Publication[{index}]"
    if not isinstance(pub, dict):
        result.error(f"{ctx} must be an object")
        return

    # metadata
    metadata = pub.get("metadata")
    if not isinstance(metadata, dict):
        result.error(f"{ctx} missing 'metadata' object")
    else:
        if not metadata.get("title"):
            result.error(f"{ctx} metadata missing 'title'")
        modified = metadata.get("modified")
        if modified and not _is_valid_rfc3339(modified):
            result.error(f"{ctx} metadata 'modified' is not valid RFC 3339: {modified}")

    # links
    pub_links = pub.get("links")
    if not isinstance(pub_links, list) or not pub_links:
        result.error(f"{ctx} missing 'links' array")
    else:
        has_acquisition = False
        for link in pub_links:
            if not isinstance(link, dict):
                result.error(f"{ctx} links contains non-object element")
                continue
            rel = link.get("rel", "")
            if rel.startswith("http://opds-spec.org/acquisition"):
                has_acquisition = True
            if not link.get("href"):
                result.error(f"{ctx} link rel='{rel}' has empty href")
            if not link.get("type"):
                result.warn(f"{ctx} link rel='{rel}' missing 'type'")

        if not has_acquisition:
            result.error(f"{ctx} missing acquisition link (rel starting with 'http://opds-spec.org/acquisition')")

    # images (should be present per spec)
    images = pub.get("images")
    if not isinstance(images, list):
        result.warn(f"{ctx} missing 'images' array")
    elif not images:
        result.warn(f"{ctx} 'images' array is empty (thumbnails recommended)")
