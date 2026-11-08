"""Evidence written as models type it: straight quotes for curly ones, day-first dates.

Found by the 2026-11-08 live smoke (tests/fixtures/live_smoke): DeepSeek quoted a merger
agreement with straight quotes where the text has curly ones, and gave an Enron email
date as 20/04/2001; both answers were right and both were rejected."""
from datetime import date

from mailroom_eda.labeling import verify

MERGER = 'each share of common stock, no par value (the “KTYB Common Stock”), of KTYB'


def test_straight_quotes_locate_curly_quotes_with_original_offsets():
    quote = 'no par value (the "KTYB Common Stock"), of KTYB'
    span = verify.locate(MERGER, quote)
    assert span is not None
    assert MERGER[span[0]:span[1]] == 'no par value (the “KTYB Common Stock”), of KTYB'


def test_apostrophes_and_dashes_fold_too():
    doc = "the Company’s shares — all of them – vest"
    span = verify.locate(doc, "the Company's shares - all of them - vest")
    assert span is not None and doc[span[0]:span[1]] == doc


def test_unambiguous_day_first_dates_are_read():
    assert date(2001, 4, 20) in verify.find_dates("Kevin G Moore@ECT\n20/04/2001 12:27")


def test_ambiguous_slash_dates_stay_month_first():
    assert verify.find_dates("04/05/2001") == [date(2001, 4, 5)]


def test_a_space_lost_beside_markdown_bold_still_locates():
    # Converted HTML: ``<b>Ltd.,</b> a`` became ``Ltd.,**a``; models render the space.
    doc = "between **Perfect Moment Asia Ltd.,**a Hong Kong company ( “**Perfect Moment Asia**”), and"
    quote = 'Perfect Moment Asia Ltd., a Hong Kong company ( "Perfect Moment Asia")'
    span = verify.locate(doc, quote)
    assert span is not None
    assert doc[span[0]:span[1]].startswith("Perfect Moment Asia Ltd.")
    assert doc[span[0]:span[1]].endswith("”)")


def test_short_quotes_do_not_match_across_word_boundaries():
    assert verify.locate("a note on the cart", "anoteon") is None
