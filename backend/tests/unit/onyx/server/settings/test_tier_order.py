import pytest

from onyx.server.settings import tier_order
from onyx.server.settings.models import Tier


def test_current_tier_is_community_without_ee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tier_order.global_version, "is_ee_version", lambda: False)
    assert tier_order.get_current_tier() is Tier.COMMUNITY


def test_current_tier_uses_ee_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    from ee.onyx.utils import tier as ee_tier

    monkeypatch.setattr(tier_order.global_version, "is_ee_version", lambda: True)
    monkeypatch.setattr(ee_tier, "get_tier", lambda: Tier.ENTERPRISE)
    assert tier_order.get_current_tier() is Tier.ENTERPRISE


@pytest.mark.parametrize(
    ("tier", "stock_brand", "ee", "expected"),
    [
        # Stock Onyx: only the tier decides.
        (Tier.COMMUNITY, True, False, False),
        (Tier.COMMUNITY, True, True, False),
        (Tier.BUSINESS, True, True, True),
        (Tier.ENTERPRISE, True, True, True),
        # Deep X without EE code serves the MIT gateway.
        (Tier.COMMUNITY, False, False, True),
        # Deep X with EE code loaded follows the tier, like stock Onyx.
        (Tier.COMMUNITY, False, True, False),
        (Tier.BUSINESS, False, True, True),
    ],
)
def test_llm_gateway_availability(
    monkeypatch: pytest.MonkeyPatch,
    tier: Tier,
    stock_brand: bool,
    ee: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(tier_order, "IS_STOCK_BRAND", stock_brand)
    monkeypatch.setattr(tier_order.global_version, "is_ee_version", lambda: ee)
    assert tier_order.is_llm_gateway_available(tier) is expected
