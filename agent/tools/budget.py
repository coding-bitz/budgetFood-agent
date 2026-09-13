from strands import tool


@tool
def split_budget(
    total: float,
    meals: list[str],
    proportions: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Splits 'total' across the meals listed in 'meals' (e.g. ["lunch", "dinner"]).
    If 'proportions' is not given, uses a default split of 60% for the first
    meal in the list and the rest split evenly across the others. If given,
    validates that it sums to 1.0 (with a 0.01 tolerance) and raises
    ValueError if not.
    Returns a dict {meal: amount}, rounded to 2 decimals, that sums to
    exactly 'total'. The last meal in the list absorbs any rounding residual
    so the sum is always exact.
    """
    if total <= 0:
        raise ValueError("Total budget must be greater than zero.")

    if not meals:
        raise ValueError("Meals list cannot be empty.")

    if len(meals) == 1:
        return {meals[0]: round(total, 2)}

    if proportions is None:
        first_meal_share = 0.60
        remaining_count = len(meals) - 1
        remaining_share = 0.40 / remaining_count
        effective_proportions = {meals[0]: first_meal_share}
        for meal in meals[1:]:
            effective_proportions[meal] = remaining_share
    else:
        total_proportions = sum(proportions.values())
        if abs(total_proportions - 1.0) > 0.01:
            raise ValueError(
                f"Proportions sum ({total_proportions}) must equal 1.0 within 0.01 tolerance."
            )
        for meal in meals:
            if meal not in proportions:
                raise ValueError(f"Meal '{meal}' is missing from proportions mapping.")
        effective_proportions = proportions

    result: dict[str, float] = {}
    accumulated = 0.0

    for meal in meals[:-1]:
        share = effective_proportions[meal]
        amount = round(total * share, 2)
        result[meal] = amount
        accumulated += amount

    # The last meal in the list absorbs any rounding residual so the sum is exact
    last_meal = meals[-1]
    result[last_meal] = round(total - accumulated, 2)

    return result
