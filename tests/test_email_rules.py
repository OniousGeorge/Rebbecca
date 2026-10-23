import base64
import email

import pytest

from adapters import email_rules as rules

OWN = "agent@gmail.com"


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def payload(headers=None, plain=None, html=None):
    """A Gmail 'full' message payload."""
    all_headers = {"From": "Dana Whitfield <dana@example.com>", "Subject": "AC problem"}
    all_headers.update(headers or {})
    header_list = []
    for name, value in all_headers.items():
        header_list.append({"name": name, "value": value})

    parts = []
    if plain is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(plain)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    return {"mimeType": "multipart/alternative", "headers": header_list, "parts": parts}


# --- body extraction ---------------------------------------------------------
def test_plain_text_is_preferred_over_html():
    p = payload(plain="plain version", html="<p>html version</p>")
    assert rules.extract_body(p) == "plain version"


def test_html_only_email_is_turned_into_text():
    p = payload(html="<div>Hi,<br>my furnace is out.</div><p>12 Oak St</p><style>p{color:red}</style>")
    text = rules.extract_body(p)
    assert "my furnace is out." in text
    assert "12 Oak St" in text
    assert "color:red" not in text
    assert "<" not in text


def test_nested_multipart_is_searched():
    inner = {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/plain", "body": {"data": b64("deep")}}]}
    p = {"mimeType": "multipart/mixed", "headers": [], "parts": [inner, {"mimeType": "application/pdf", "body": {}}]}
    assert rules.extract_body(p) == "deep"


def test_single_part_message_body():
    p = {"mimeType": "text/plain", "headers": [], "body": {"data": b64("just text")}}
    assert rules.extract_body(p) == "just text"


def test_message_with_no_text_gives_an_empty_body():
    assert rules.extract_body({"mimeType": "multipart/mixed", "headers": [], "parts": []}) == ""


# --- quoted reply stripping --------------------------------------------------
def test_gmail_style_quote_is_removed():
    text = (
        "Thanks, it's 12 Oak St.\n\n"
        "On Tue, Oct 21, 2026 at 3:04 PM Comfort Zone <agent@gmail.com> wrote:\n"
        "> Thanks for contacting us!\n> What is your address?"
    )
    assert rules.strip_quoted_reply(text) == "Thanks, it's 12 Oak St."


def test_quote_header_wrapped_over_two_lines_is_removed():
    text = "The second one\n\nOn Tue, Oct 21, 2026 at 3:04 PM Comfort Zone <agent@gmail.com>\nwrote:\n> options..."
    assert rules.strip_quoted_reply(text) == "The second one"


def test_outlook_original_message_block_is_removed():
    text = "Yes please\n\n-----Original Message-----\nFrom: Comfort Zone\nSent: Tuesday\nSubject: Re: AC"
    assert rules.strip_quoted_reply(text) == "Yes please"


def test_outlook_header_block_without_separator_is_removed():
    text = "Second one\n\nFrom: Comfort Zone <agent@gmail.com>\nSent: Tuesday, October 21, 2026 3:04 PM\nTo: Dana"
    assert rules.strip_quoted_reply(text) == "Second one"


def test_a_message_without_quotes_is_unchanged():
    text = "My furnace is out.\n\nI am at 12 Oak St.\nFrom what I can tell it will not start."
    assert rules.strip_quoted_reply(text) == text


def test_a_message_that_is_only_quotes_becomes_empty():
    assert rules.strip_quoted_reply("> old text\n> more old text") == ""


def test_windows_line_endings_are_handled():
    text = "Hello\r\n\r\nOn Tue, Oct 21, 2026 at 3:04 PM X <a@b.com> wrote:\r\n> hi"
    assert rules.strip_quoted_reply(text) == "Hello"


# --- sender and loop guard ---------------------------------------------------
def test_sender_is_split_into_name_and_lower_case_address():
    p = payload({"From": "Dana Whitfield <Dana@Example.com>"})
    assert rules.sender_of(p) == ("Dana Whitfield", "dana@example.com")


def test_a_normal_customer_email_is_answered():
    assert rules.skip_reason(payload(plain="hi"), OWN) is None


@pytest.mark.parametrize(
    "headers, reason",
    [
        ({"From": "Agent <AGENT@gmail.com>"}, "own_address"),
        ({"From": "no-reply@shop.com"}, "no_reply_sender"),
        ({"From": "noreply@shop.com"}, "no_reply_sender"),
        ({"From": "MAILER-DAEMON@googlemail.com"}, "no_reply_sender"),
        ({"Auto-Submitted": "auto-replied"}, "auto_submitted"),
        ({"Auto-Submitted": "auto-generated"}, "auto_submitted"),
        ({"Precedence": "bulk"}, "bulk_precedence"),
        ({"Precedence": "Junk"}, "bulk_precedence"),
        ({"List-Unsubscribe": "<mailto:x@y.com>"}, "mailing_list"),
        ({"List-Id": "<news.example.com>"}, "mailing_list"),
        ({"X-Auto-Response-Suppress": "All"}, "auto_response"),
    ],
)
def test_loop_guard_skips_automatic_mail(headers, reason):
    assert rules.skip_reason(payload(headers, plain="x"), OWN) == reason


def test_auto_submitted_no_means_a_human_wrote_it():
    assert rules.skip_reason(payload({"Auto-Submitted": "no"}, plain="x"), OWN) is None


def test_allowlist_blocks_everyone_else():
    allowed = ["Dana@Example.com"]
    assert rules.skip_reason(payload(plain="x"), OWN, allowed) is None
    stranger = payload({"From": "stranger@spam.com"}, plain="x")
    assert rules.skip_reason(stranger, OWN, allowed) == "sender_not_allowed"


def test_an_empty_allowlist_allows_everyone():
    assert rules.skip_reason(payload({"From": "stranger@spam.com"}, plain="x"), OWN, []) is None


# --- building replies --------------------------------------------------------
@pytest.mark.parametrize(
    "subject, expected",
    [("AC problem", "Re: AC problem"), ("Re: AC problem", "Re: AC problem"), ("RE: AC problem", "RE: AC problem")],
)
def test_reply_subject_never_doubles_re(subject, expected):
    assert rules.reply_subject(subject) == expected


def test_reply_has_the_headers_that_keep_it_in_the_thread():
    message = rules.build_reply(
        OWN, "dana@example.com", "AC problem", "Hello", message_id_header="<m2@mail>", references="<m1@mail>"
    )
    assert message["To"] == "dana@example.com"
    assert message["Subject"] == "Re: AC problem"
    assert message["In-Reply-To"] == "<m2@mail>"
    assert message["References"] == "<m1@mail> <m2@mail>"


def test_reply_to_an_email_without_a_message_id_has_no_threading_headers():
    message = rules.build_reply(OWN, "dana@example.com", "Hi", "Hello")
    assert message["In-Reply-To"] is None
    assert message["References"] is None


def test_encoded_message_can_be_decoded_back():
    message = rules.build_reply(OWN, "dana@example.com", "Hi", "Hello there")
    raw = rules.encode_for_gmail(message)
    decoded = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert decoded["Subject"] == "Re: Hi"
    assert decoded.get_payload().strip() == "Hello there"


# --- Reply-To ------------------------------------------------------------------
def test_reply_to_on_the_senders_own_domain_is_used():
    p = payload({"From": "Dana <dana@acme.com>", "Reply-To": "Office <Office@Acme.com>"}, plain="x")
    assert rules.reply_address(p) == "office@acme.com"


def test_reply_to_on_another_domain_is_ignored_so_the_inbox_cannot_relay_spam():
    p = payload({"From": "dana@acme.com", "Reply-To": "victim@elsewhere.com"}, plain="x")
    assert rules.reply_address(p) == "dana@acme.com"


def test_without_reply_to_the_sender_gets_the_reply():
    assert rules.reply_address(payload(plain="x")) == "dana@example.com"
