from copy import deepcopy

from f1_engineer.analysis.session_answers import braking_observation_message


def test_braking_message_uses_measured_region_and_interval():
    report = {"braking_zones": [{"supported": True, "region_m": [100, 200],
                                "target_minus_reference_m": -5,
                                "difference_interval_m": [-8, -2]}]}
    original = deepcopy(report)
    message = braking_observation_message(report)
    assert "5 m earlier" in message
    assert "100-200 m region" in message
    assert "-8 to -2 m" in message
    assert "not named corners" in message
    assert "not automatically better" in message
    assert report == original


def test_braking_message_omits_unsupported_or_nonfinite_observations():
    report = {"braking_zones": [{"supported": False},
                               {"supported": True, "target_minus_reference_m": float("nan")} ]}
    assert braking_observation_message(report) is None


def test_braking_message_is_bounded_and_retains_zero():
    zone = {"supported": True, "region_m": [100, 200], "target_minus_reference_m": 0,
            "difference_interval_m": [-2, 2]}
    message = braking_observation_message({"braking_zones": [zone] * 10})
    assert message.count("at the same distance") == 3
