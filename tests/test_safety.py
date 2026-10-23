import pytest

from core.safety import find_emergency
from tests.fakes import CONFIG

PHRASES = CONFIG.escalation.emergency_keywords


@pytest.mark.parametrize(
    "text",
    [
        "I think I smell gas near my furnace!",
        "There's a strong gas smell in the basement",
        "smelling gas since this morning",
        "I can smell natural gas in the kitchen",
        "It smells like gas in here",
        "the house smells strongly of gas",
        "there is a gas odor by the water heater",
        "I think we have a gas leak",
        "the furnace is leaking gas",
        "it smells like rotten eggs near the furnace",
        "our carbon monoxide detector keeps beeping",
        "the CO detector is going off",
        "CO alarm went off twice tonight",
        "monoxide alarm sounding",
        "I smell smoke when the heat comes on",
        "smoke coming out of the vent",
        "the furnace is smoking",
        "the smoke alarm is going off",
        "the unit caught on fire",
        "I saw flames in the burner",
        "sparks from the outdoor unit",
        "there's a burning smell from the vents",
        "it smells like burning plastic",
        "SMELL GAS!!!",
    ],
)
def test_emergency_wordings_are_caught(text):
    assert find_emergency(text, PHRASES) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Do you service gas furnaces?",
        "My gas bill has been really high this winter",
        "Can you install a gas water heater?",
        "Do you service the Smokehouse restaurant?",
        "My smoke detector chirps, I think the battery is low",
        "Could you come fix my thermostat?",
        "Can you come out Friday? The heat pump is noisy",
        "I need a quote for a new gas fireplace insert",
        "What are your hours on Saturday?",
        "My CO2 sensor reading is high",
        "The thermostat is in eco mode",
        "We had a fire drill at work so I missed your call",
    ],
)
def test_ordinary_emails_are_not_flagged(text):
    assert find_emergency(text, PHRASES) is None


def test_the_matched_phrase_is_reported():
    assert find_emergency("I think I smell gas near my furnace", PHRASES) == "smell gas"


def test_words_too_far_apart_do_not_match():
    # "smell" and "gas" with three words between them
    assert find_emergency("it does not smell bad, but the gas bill is high", ["smell gas"]) is None


def test_words_in_the_wrong_order_do_not_match_that_phrase():
    assert find_emergency("gas smell", ["smell gas"]) is None


def test_no_phrases_means_no_emergencies():
    assert find_emergency("I smell gas", []) is None
