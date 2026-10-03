"""Find a known board embedded on a company's own careers page (BE-050).

Pages like ``acme.com/careers`` often load a Greenhouse, Lever or Ashby
widget (script or iframe) or just link to the board. Each script ``src``,
iframe ``src`` and link ``href`` in the fetched HTML goes through
``match_board``, so only known board hosts count. Pure parsing, no network.

Widgets win over links: a page may link to another company's board, but it
only embeds its own. If either kind names more than one board, it's
ambiguous and gives ``None``.
"""

from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .ats import match_board
from .sources import BoardSource


def match_embedded_board(html: str, page_url: str) -> BoardSource | None:
    """The one board ``html`` embeds or links to, else ``None``."""
    soup = BeautifulSoup(html, "html.parser")
    widgets = _boards(soup, page_url, ("script", "src"), ("iframe", "src"))
    if widgets:
        return _only(widgets)
    return _only(_boards(soup, page_url, ("a", "href")))


def _boards(
    soup: BeautifulSoup, page_url: str, *pairs: tuple[str, str]
) -> list[BoardSource]:
    found: list[BoardSource] = []
    for name, attr in pairs:
        for tag in soup.find_all(name):
            value = tag.get(attr) if isinstance(tag, Tag) else None
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                url = urljoin(page_url, value.strip())
            except ValueError:
                continue
            board = match_board(url)
            if board is not None:
                found.append(board)
    return found


def _only(boards: list[BoardSource]) -> BoardSource | None:
    distinct = {b.model_dump_json(): b for b in boards}
    if len(distinct) != 1:
        return None
    return next(iter(distinct.values()))
