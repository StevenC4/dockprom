"""The message rewrite, against a fixture shaped like what Alertmanager actually posts.

The fixture is copied from a real #alerts post (2026-07-23, NH22ExtractorRunError) plus the
button this change adds — so a template change in alertmanager/config.yml that breaks the
rewrite shows up here rather than on a red post that will not go green.
"""

from alert_ack.resolve import GREEN, resolved_message

TITLE = ":rotating_light: NightHawk22 · nh22-extractor — NH22ExtractorRunError"


def firing_message():
    return {
        "type": "message",
        "subtype": "bot_message",
        "text": "",
        "ts": "1784818437.512549",
        "bot_id": "B0BJXFCNASF",
        "attachments": [
            {
                "id": 1,
                "color": "danger",
                "fallback": "NightHawk22 · nh22-extractor — NH22ExtractorRunError",
                "title": TITLE,
                "title_link": "https://alertmanager.local/#/alerts?receiver=nh22-slack",
                "text": "*WARNING* [spotify] nh22 spotify run error (UNKNOWN)",
                "callback_id": "alert:nh22",
                "mrkdwn_in": ["fallback", "pretext", "text"],
                "actions": [
                    {
                        "id": "1",
                        "name": "alert:resolve",
                        "text": "Mark resolved",
                        "type": "button",
                        "value": "nh22",
                        "style": "primary",
                    }
                ],
            }
        ],
    }


def test_the_border_turns_green():
    out = resolved_message(firing_message(), user_id="U0BGX8MV8EA", clicked_at=1784820000)
    assert out["attachments"][0]["color"] == GREEN


def test_the_button_is_removed():
    # Leaving a live button on a resolved post invites a second click that changes nothing
    # visible, which reads as broken.
    out = resolved_message(firing_message(), user_id="U1", clicked_at=1784820000)
    assert "actions" not in out["attachments"][0]
    assert "callback_id" not in out["attachments"][0]


def test_it_says_who_resolved_it():
    out = resolved_message(firing_message(), user_id="U0BGX8MV8EA", clicked_at=1784820000)
    text = out["attachments"][0]["text"]
    assert ":white_check_mark: *Resolved by <@U0BGX8MV8EA>*" in text
    # ...without losing what the alert said.
    assert "*WARNING* [spotify] nh22 spotify run error (UNKNOWN)" in text
    assert out["attachments"][0]["ts"] == 1784820000


def test_an_anonymous_click_still_resolves():
    out = resolved_message(firing_message(), user_id=None, clicked_at=1)
    assert ":white_check_mark: *Resolved*" in out["attachments"][0]["text"]


def test_the_siren_becomes_a_check():
    out = resolved_message(firing_message(), user_id="U1", clicked_at=1)
    assert out["attachments"][0]["title"].startswith(":white_check_mark: NightHawk22")


def test_a_title_without_the_firing_emoji_is_left_alone():
    original = firing_message()
    original["attachments"][0]["title"] = "Something else entirely"
    out = resolved_message(original, user_id="U1", clicked_at=1)
    assert out["attachments"][0]["title"] == "Something else entirely"


def test_the_rest_of_the_attachment_survives():
    out = resolved_message(firing_message(), user_id="U1", clicked_at=1)
    attachment = out["attachments"][0]
    assert attachment["title_link"] == "https://alertmanager.local/#/alerts?receiver=nh22-slack"
    assert attachment["fallback"] == "NightHawk22 · nh22-extractor — NH22ExtractorRunError"
    assert "text" in attachment["mrkdwn_in"]


def test_the_original_is_not_mutated():
    original = firing_message()
    resolved_message(original, user_id="U1", clicked_at=1)
    assert original["attachments"][0]["color"] == "danger"
    assert original["attachments"][0]["actions"]


def test_every_attachment_is_recoloured_but_only_the_first_is_annotated():
    original = firing_message()
    original["attachments"].append({"color": "danger", "text": "second"})
    out = resolved_message(original, user_id="U1", clicked_at=1)
    assert [a["color"] for a in out["attachments"]] == [GREEN, GREEN]
    assert out["attachments"][1]["text"] == "second"


def test_a_message_with_no_attachment_gets_one():
    # No attachment means no border at all, which is the entire point of the ask.
    out = resolved_message({"text": "bare alert"}, user_id="U1", clicked_at=1)
    assert out["attachments"][0]["color"] == GREEN
    assert "bare alert" in out["attachments"][0]["text"]
