import pytest
from pydantic import ValidationError

from core.config import Business, load_client


def test_demo_config_loads():
    cfg = load_client("hvac_demo")
    assert cfg.business.name == "Comfort Zone Heating & Air"
    assert cfg.business.timezone == "America/New_York"
    assert cfg.business.hours["sat"] == ("09:00", "13:00")
    assert "sun" not in cfg.business.hours


def _business(**overrides):
    data = dict(
        name="x", tone="x", timezone="America/New_York",
        hours={"mon": ("08:00", "17:00")},
        service_area=["a"], services=["b"],
    )
    return Business(**{**data, **overrides})


def test_rejects_unknown_timezone():
    with pytest.raises(ValidationError):
        _business(timezone="Mars/Olympus")


def test_rejects_opening_after_closing():
    with pytest.raises(ValidationError):
        _business(hours={"mon": ("17:00", "08:00")})


def test_rejects_unknown_day():
    with pytest.raises(ValidationError):
        _business(hours={"funday": ("08:00", "17:00")})
