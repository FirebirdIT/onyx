from unittest.mock import patch

from onyx.auth.email_utils import build_html_email
from onyx.configs.constants import ONYX_DISCORD_URL


def test_stock_brand_email_links_the_community() -> None:
    with (
        patch("onyx.auth.email_utils.IS_STOCK_BRAND", True),
        patch("onyx.auth.email_utils.ONYX_DEFAULT_APPLICATION_NAME", "Onyx"),
    ):
        html = build_html_email("Onyx", "Heading", "Body")
    assert ONYX_DISCORD_URL in html


def test_white_label_email_hides_the_community_link() -> None:
    with (
        patch("onyx.auth.email_utils.IS_STOCK_BRAND", False),
        patch("onyx.auth.email_utils.ONYX_DEFAULT_APPLICATION_NAME", "Deep X"),
    ):
        html = build_html_email("Deep X", "Heading", "Body")
    assert ONYX_DISCORD_URL not in html
    assert "Deep X" in html
