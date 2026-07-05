"""License-clear starter corpus for local ingestion smoke tests."""

from __future__ import annotations

import datetime as dt

from baseline_api.db.models.enums import KnowledgeSourceType, TrustLevel
from packages.knowledge.models import KnowledgeSourceDocument

PUBLIC_DOMAIN_US_GOVERNMENT = "U.S. federal government work; public domain in the United States"
OPEN_ACCESS_SOURCE = "Open-access source; curated summary stored, not full-text reproduction"


def _metadata(*, publisher: str, accessed_at: dt.date, canonical_url: str) -> dict[str, str]:
    return {
        "publisher": publisher,
        "accessed_at": accessed_at.isoformat(),
        "canonical_url": canonical_url,
    }


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
        source_metadata=_metadata(
            publisher="health.gov",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url=(
                "https://health.gov/sites/default/files/2019-09/"
                "Physical_Activity_Guidelines_2nd_edition.pdf"
            ),
        ),
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
        source_metadata=_metadata(
            publisher="dietaryguidelines.gov",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url=(
                "https://www.dietaryguidelines.gov/sites/default/files/2021-03/"
                "Dietary_Guidelines_for_Americans-2020-2025.pdf"
            ),
        ),
        content=(
            "The Dietary Guidelines for Americans summarize broad nutrition patterns for "
            "the general public, including nutrient-dense foods, hydration context, and "
            "limits on added sugars, saturated fat, and sodium. This starter excerpt is "
            "curated external context and does not contain Baseline user data."
        ),
    ),
    KnowledgeSourceDocument(
        title="CDC Physical Activity Basics",
        author_or_org="U.S. Centers for Disease Control and Prevention",
        source_type=KnowledgeSourceType.guideline,
        url_or_identifier="https://www.cdc.gov/physical-activity-basics/",
        license_status=PUBLIC_DOMAIN_US_GOVERNMENT,
        published_at=dt.date(2024, 1, 1),
        version="cdc-physical-activity-basics-2024",
        trust_level=TrustLevel.authoritative,
        source_metadata=_metadata(
            publisher="cdc.gov",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url="https://www.cdc.gov/physical-activity-basics/",
        ),
        content=(
            "CDC physical activity basics describe aerobic and muscle-strengthening "
            "movement as broad public-health guidance. The content is useful for "
            "general context about activity consistency, but it does not assess an "
            "individual Baseline user's readiness or prescribe training."
        ),
    ),
    KnowledgeSourceDocument(
        title="CDC Sleep and Sleep Disorders",
        author_or_org="U.S. Centers for Disease Control and Prevention",
        source_type=KnowledgeSourceType.guideline,
        url_or_identifier="https://www.cdc.gov/sleep/",
        license_status=PUBLIC_DOMAIN_US_GOVERNMENT,
        published_at=dt.date(2024, 1, 1),
        version="cdc-sleep-2024",
        trust_level=TrustLevel.authoritative,
        source_metadata=_metadata(
            publisher="cdc.gov",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url="https://www.cdc.gov/sleep/",
        ),
        content=(
            "CDC sleep materials describe sleep duration and regularity as general "
            "health and recovery context. Baseline may cite this source to explain why "
            "sleep debt or irregular sleep increases uncertainty, without making a "
            "medical diagnosis."
        ),
    ),
    KnowledgeSourceDocument(
        title="NIH News in Health: Good Sleep for Good Health",
        author_or_org="U.S. National Institutes of Health",
        source_type=KnowledgeSourceType.article,
        url_or_identifier="https://newsinhealth.nih.gov/2021/04/good-sleep-good-health",
        license_status=PUBLIC_DOMAIN_US_GOVERNMENT,
        published_at=dt.date(2021, 4, 1),
        version="nih-newsinhealth-sleep-2021",
        trust_level=TrustLevel.authoritative,
        citation_urls=(
            "https://www.nhlbi.nih.gov/health/sleep-deprivation",
            "https://www.cdc.gov/sleep/",
        ),
        source_metadata=_metadata(
            publisher="newsinhealth.nih.gov",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url="https://newsinhealth.nih.gov/2021/04/good-sleep-good-health",
        ),
        content=(
            "NIH sleep education explains that sleep supports attention, mood, and "
            "physical recovery. Baseline uses this only as general background when "
            "sleep debt, poor consistency, or cognitive-readiness uncertainty appears."
        ),
    ),
    KnowledgeSourceDocument(
        title="MedlinePlus: Exercise and Physical Fitness",
        author_or_org="U.S. National Library of Medicine",
        source_type=KnowledgeSourceType.article,
        url_or_identifier="https://medlineplus.gov/exerciseandphysicalfitness.html",
        license_status=PUBLIC_DOMAIN_US_GOVERNMENT,
        published_at=dt.date(2024, 1, 1),
        version="medlineplus-exercise-2024",
        trust_level=TrustLevel.authoritative,
        citation_urls=(
            "https://health.gov/our-work/nutrition-physical-activity/physical-activity-guidelines",
            "https://www.cdc.gov/physical-activity-basics/",
        ),
        source_metadata=_metadata(
            publisher="medlineplus.gov",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url="https://medlineplus.gov/exerciseandphysicalfitness.html",
        ),
        content=(
            "MedlinePlus describes exercise benefits and general safety considerations. "
            "It supports Baseline's boundary that recommendations are wellness "
            "decision support and should avoid diagnosis, treatment, or claims about "
            "medical conditions."
        ),
    ),
    KnowledgeSourceDocument(
        title="ACSM Resistance Training for Health and Fitness",
        author_or_org="American College of Sports Medicine",
        source_type=KnowledgeSourceType.guideline,
        url_or_identifier=(
            "https://www.acsm.org/education-resources/trending-topics-resources/"
            "resource-library/detail?id=f1c90a56-fb62-4b01-b2a2-c82c56e1f2f8"
        ),
        license_status=OPEN_ACCESS_SOURCE,
        published_at=dt.date(2019, 1, 1),
        version="acsm-resistance-training-health-fitness",
        trust_level=TrustLevel.authoritative,
        source_metadata=_metadata(
            publisher="acsm.org",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url=(
                "https://www.acsm.org/education-resources/trending-topics-resources/"
                "resource-library/detail?id=f1c90a56-fb62-4b01-b2a2-c82c56e1f2f8"
            ),
        ),
        content=(
            "ACSM resistance-training guidance frames strength work around consistency, "
            "progression, recovery, and appropriate load selection. Baseline uses this "
            "as general context for strength-training consistency proxies and does not "
            "infer measured maximal strength without load data."
        ),
    ),
    KnowledgeSourceDocument(
        title="NSCA Essentials of Strength Training and Conditioning Position Context",
        author_or_org="National Strength and Conditioning Association",
        source_type=KnowledgeSourceType.book,
        url_or_identifier="ISBN 9781492501626",
        license_status="Commercial reference; curated bibliographic summary only",
        published_at=dt.date(2016, 1, 1),
        version="nsca-essentials-4th-edition-summary",
        trust_level=TrustLevel.authoritative,
        source_metadata=_metadata(
            publisher="Human Kinetics",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url="ISBN 9781492501626",
        ),
        content=(
            "Strength and conditioning references distinguish measured strength from "
            "training exposure. Baseline may discuss workout consistency and trend "
            "proxies, but should avoid claiming improvements in lift strength when "
            "load, reps, and testing data are absent."
        ),
    ),
    KnowledgeSourceDocument(
        title="Frontiers in Physiology: Heart Rate Variability in Sport and Exercise",
        author_or_org="Frontiers in Physiology",
        source_type=KnowledgeSourceType.research_paper,
        url_or_identifier="https://www.frontiersin.org/journals/physiology",
        license_status=OPEN_ACCESS_SOURCE,
        published_at=dt.date(2017, 1, 1),
        version="frontiers-hrv-sport-exercise-summary",
        trust_level=TrustLevel.peer_reviewed,
        source_metadata=_metadata(
            publisher="Frontiers",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url="https://www.frontiersin.org/journals/physiology",
        ),
        content=(
            "Peer-reviewed HRV literature emphasizes trend interpretation, context, "
            "and measurement variability. Baseline should treat HRV as one recovery "
            "signal among sleep, resting heart rate, check-ins, and training load, not "
            "as a standalone command to train hard or rest."
        ),
    ),
    KnowledgeSourceDocument(
        title="NSF Sleep in America Poll Sleep Health Context",
        author_or_org="National Sleep Foundation",
        source_type=KnowledgeSourceType.article,
        url_or_identifier="https://www.thensf.org/sleep-in-america-polls/",
        license_status=OPEN_ACCESS_SOURCE,
        published_at=dt.date(2024, 1, 1),
        version="nsf-sleep-health-context-2024",
        trust_level=TrustLevel.curated,
        citation_urls=("https://www.cdc.gov/sleep/",),
        source_metadata=_metadata(
            publisher="thensf.org",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url="https://www.thensf.org/sleep-in-america-polls/",
        ),
        content=(
            "Sleep health resources describe regularity, duration, and subjective "
            "restoration as broad lifestyle context. Baseline can use this to explain "
            "uncertainty and recovery tradeoffs, while avoiding diagnosis of sleep "
            "disorders."
        ),
    ),
    KnowledgeSourceDocument(
        title="WHO Guidelines on Physical Activity and Sedentary Behaviour",
        author_or_org="World Health Organization",
        source_type=KnowledgeSourceType.guideline,
        url_or_identifier="https://www.who.int/publications/i/item/9789240015128",
        license_status=OPEN_ACCESS_SOURCE,
        published_at=dt.date(2020, 11, 25),
        version="who-physical-activity-sedentary-behaviour-2020",
        trust_level=TrustLevel.authoritative,
        source_metadata=_metadata(
            publisher="who.int",
            accessed_at=dt.date(2026, 7, 5),
            canonical_url="https://www.who.int/publications/i/item/9789240015128",
        ),
        content=(
            "WHO activity guidelines provide global public-health context for aerobic "
            "activity, strengthening activity, and reducing sedentary time. Baseline "
            "uses this only as general external knowledge, separate from personal "
            "readiness evidence."
        ),
    ),
)
