"""Inter-page navigation helpers for the Streamlit admin.

Why a dedicated module: `streamlit_app.main` imports every page module to
build its PAGES dict, so a page module cannot import back from
`admin.streamlit_app` without creating an import cycle. Anything pages need
to coordinate with the main app (e.g. the navigation sentinel key) lives here.
"""

from __future__ import annotations

# Session-state key used by a page to request a switch to another page.
# `streamlit_app.main` pops this key BEFORE instantiating the sidebar radio,
# avoiding Streamlit's 'cannot modify session_state[<widget_key>] after the
# widget is instantiated' error.
PENDING_PAGE_KEY = "_pending_page"


def resolve_initial_page_index(
    pages_list: list[str],
    pending: str | None,
) -> int:
    """Return the radio index that matches a pending-navigation request.

    Pure function — extracted so it can be unit-tested without spinning up a
    Streamlit runtime. Falls back to 0 (first page) when the request is
    absent or points at an unknown page.
    """
    if pending is None:
        return 0
    if pending not in pages_list:
        return 0
    return pages_list.index(pending)
