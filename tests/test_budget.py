import pytest
from agent.tools.budget import split_budget


def test_split_budget_default_two_meals():
    result = split_budget(total=40.0, meals=["lunch", "dinner"])
    assert result == {"lunch": 24.0, "dinner": 16.0}
    assert sum(result.values()) == 40.0


def test_split_budget_single_meal():
    result = split_budget(total=25.50, meals=["dinner"])
    assert result == {"dinner": 25.50}


def test_split_budget_three_meals_default():
    result = split_budget(total=60.0, meals=["breakfast", "lunch", "dinner"])
    assert result["breakfast"] == 36.0  # 60% of 60
    assert result["lunch"] == 12.0  # 20% of 60
    assert result["dinner"] == 12.0  # 20% of 60
    assert sum(result.values()) == 60.0


def test_split_budget_rounding_residual_exactness():
    # 33.33 split 60/40 produces 19.998 -> 20.00 and remainder 13.33
    result = split_budget(total=33.33, meals=["lunch", "dinner"])
    assert result["lunch"] == 20.00
    assert result["dinner"] == 13.33
    assert sum(result.values()) == 33.33


def test_split_budget_custom_proportions():
    proportions = {"lunch": 0.50, "dinner": 0.50}
    result = split_budget(total=50.0, meals=["lunch", "dinner"], proportions=proportions)
    assert result == {"lunch": 25.0, "dinner": 25.0}
    assert sum(result.values()) == 50.0


def test_split_budget_proportions_sum_tolerance_error():
    invalid_proportions = {"lunch": 0.40, "dinner": 0.40}  # sums to 0.80
    with pytest.raises(ValueError, match="must equal 1.0"):
        split_budget(total=50.0, meals=["lunch", "dinner"], proportions=invalid_proportions)


def test_split_budget_missing_meal_in_proportions():
    proportions = {"lunch": 1.0}
    with pytest.raises(ValueError, match="missing from proportions"):
        split_budget(total=50.0, meals=["lunch", "dinner"], proportions=proportions)


def test_split_budget_negative_or_zero_budget():
    with pytest.raises(ValueError, match="greater than zero"):
        split_budget(total=0.0, meals=["lunch"])
    with pytest.raises(ValueError, match="greater than zero"):
        split_budget(total=-10.0, meals=["lunch"])


def test_split_budget_empty_meals():
    with pytest.raises(ValueError, match="cannot be empty"):
        split_budget(total=20.0, meals=[])
