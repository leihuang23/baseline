"""License-clear starter corpus for local ingestion smoke tests."""

from __future__ import annotations

import datetime as dt

from baseline_api.db.models.enums import KnowledgeSourceType, TrustLevel
from packages.knowledge.models import KnowledgeSourceDocument

PUBLIC_DOMAIN_US_GOVERNMENT = "U.S. federal government work; public domain in the United States"

STARTER_CORPUS: tuple[KnowledgeSourceDocument, ...] = (
    KnowledgeSourceDocument(
        title="Physical Activity Guidelines for Americans, 2nd edition",
        author_or_org="U.S. Department of Health and Human Services",
        source_type=KnowledgeSourceType.guideline,
        url_or_identifier=(
            "https://health.gov/sites/default/files/2019-09/"
            "Physical_Activity_Guidelines_2nd_edition.pdf"
        ),
        license_status=PUBLIC_DOMAIN_US_GOVERNMENT,
        published_at=dt.date(2018, 11, 1),
        version="2018-2nd-edition",
        trust_level=TrustLevel.authoritative,
        content=(
            "The Physical Activity Guidelines for Americans describe general population "
            "recommendations for aerobic activity, muscle-strengthening activity, and "
            "progressive increases in weekly movement. This starter excerpt is a "
            "hand-authored summary for retrieval plumbing tests, not personalized advice."
        ),
    ),
    KnowledgeSourceDocument(
        title="Dietary Guidelines for Americans, 2020-2025",
        author_or_org=(
            "U.S. Department of Agriculture and U.S. Department of Health and Human Services"
        ),
        source_type=KnowledgeSourceType.guideline,
        url_or_identifier=(
            "https://www.dietaryguidelines.gov/sites/default/files/2021-03/"
            "Dietary_Guidelines_for_Americans-2020-2025.pdf"
        ),
        license_status=PUBLIC_DOMAIN_US_GOVERNMENT,
        published_at=dt.date(2020, 12, 1),
        version="2020-2025",
        trust_level=TrustLevel.authoritative,
        content=(
            "The Dietary Guidelines for Americans summarize broad nutrition patterns for "
            "the general public, including nutrient-dense foods, hydration context, and "
            "limits on added sugars, saturated fat, and sodium. This starter excerpt is "
            "curated external context and does not contain Baseline user data."
        ),
    ),
)
