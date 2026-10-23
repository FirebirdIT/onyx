"""Total ordering over the `Tier` enum.

Lives in CE so any module can compare tiers without depending on EE code.
"""

from onyx.configs.constants import IS_STOCK_BRAND
from onyx.server.gateway.configs import LLM_GATEWAY_MIN_TIER
from onyx.server.settings.models import Tier
from onyx.utils.variable_functionality import global_version

TIER_RANK: dict[Tier, int] = {
    Tier.COMMUNITY: 0,
    Tier.BUSINESS: 1,
    Tier.ENTERPRISE: 2,
}


def tier_at_least(current: Tier, required: Tier) -> bool:
    return TIER_RANK[current] >= TIER_RANK[required]


def get_current_tier() -> Tier:
    """The deployment's tier; Community when EE code is not loaded."""
    if not global_version.is_ee_version():
        return Tier.COMMUNITY
    from ee.onyx.utils.tier import get_tier

    return get_tier()


def is_llm_gateway_available(current: Tier) -> bool:
    """Whether this deployment serves the LLM gateway, and so Craft.

    True when the tier reaches LLM_GATEWAY_MIN_TIER. A Deep X build without EE
    code serves the MIT gateway, so it is also true there. The tier itself does
    not change: features gated on the tier stay locked.
    """
    if tier_at_least(current, LLM_GATEWAY_MIN_TIER):
        return True
    return not IS_STOCK_BRAND and not global_version.is_ee_version()
