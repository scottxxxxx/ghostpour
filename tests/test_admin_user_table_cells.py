"""The user table's header and every row template must agree on cell count.

Scott, 2026-09-13: "there are also sorts for our users where the columns fall
out of alignment, which tells me something is not right". He was right and
sorting was not the cause, it was the exposure: the anonymous-device row was
three cells short of the header, so its "last active" rendered under Transl
and everything after shifted left. Sorting interleaves anonymous rows with
signed-in ones, which is when the shift becomes visible.

It had happened before. The anonymous template was patched to ten leading
cells with a comment asserting the count, then Docs, Photos and Transl were
added to the header and the template was not touched. A hand-maintained count
in a comment goes stale exactly when the header grows, so the count lives here
instead, derived from the page, and any drift between the header and either
row template fails this file.

Counted from the page text, not evaluated: the header is static HTML and both
row templates are template literals whose `<td` tags are literal in the
source. A `<td` inside an interpolated value would be counted too, which is
the safe direction (it would fail loudly on a real change rather than pass on
a wrong one).
"""
import re

HTML = "app/static/admin.html"


def _src():
    return open(HTML).read()


def _header_cells(src):
    thead = re.search(r'<table id="user-table"><thead><tr>(.*?)</tr></thead>', src, re.S)
    assert thead, "the user table header was not found"
    return len(re.findall(r"<th\b", thead.group(1)))


def _render_user_rows(src):
    start = src.index("function renderUserRows(")
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("unbalanced braces extracting renderUserRows")


def _row_templates(body):
    """The two `return \\`<tr ...>...</tr>\\`` templates inside renderUserRows,
    in source order: anonymous first, signed-in second.

    Walked by string search, not by a backtick-excluding regex: the signed-in
    template nests a template literal (the tier <select> options), so a
    `[^`]*?` class stops at the inner backtick and never reaches `</tr>`. The
    first version of this file did exactly that and failed on the FIXED page
    the same way it failed on the broken one, which is a test that cannot
    tell the two apart."""
    rows, pos = [], 0
    while True:
        start = body.find("return `<tr", pos)
        if start == -1:
            break
        end = body.index("</tr>`", start)
        rows.append(body[start + len("return `"):end + len("</tr>")])
        pos = end
    assert len(rows) == 2, f"expected the anonymous and signed-in row templates, found {len(rows)}"
    return rows


def _cells(tpl):
    return len(re.findall(r"<td\b", tpl))


def test_the_signed_in_row_has_exactly_one_cell_per_header():
    src = _src()
    anon, signed = _row_templates(_render_user_rows(src))
    assert _cells(signed) == _header_cells(src)


def test_the_anonymous_row_has_exactly_one_cell_per_header():
    """The one that was wrong twice. Three short on 2026-09-13."""
    src = _src()
    anon, signed = _row_templates(_render_user_rows(src))
    assert _cells(anon) == _header_cells(src)


def test_the_empty_state_spans_every_column():
    src = _src()
    body = _render_user_rows(src)
    m = re.search(r'<td colspan="(\d+)"[^>]*>No users yet', body)
    assert m, "the empty-state row was not found"
    assert int(m.group(1)) == _header_cells(src)


def test_the_anonymous_row_puts_last_active_under_the_last_active_header():
    """Count alone is not enough: a row can have the right number of cells
    with the data in the wrong columns. Pin the one cell that was seen
    under the wrong header, by position."""
    src = _src()
    thead = re.search(r'<table id="user-table"><thead><tr>(.*?)</tr></thead>', src, re.S).group(1)
    headers = re.findall(r"<th\b[^>]*>(.*?)</th>", thead, re.S)
    last_active_col = next(i for i, h in enumerate(headers) if "Last Active" in h)
    anon, _ = _row_templates(_render_user_rows(src))
    cells = re.findall(r"<td\b[^>]*>(.*?)</td>", anon, re.S)
    assert "timeAgo(u.last_request)" in cells[last_active_col], (
        f"last active is not in column {last_active_col}; the anonymous row is misaligned")
