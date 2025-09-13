"""Utility helpers for reasoning effort mapping across converters."""
from typing import Optional
from .base_converter import ConversionError


def determine_reasoning_effort(
    budget: Optional[int],
    low_env_var: str,
    high_env_var: str,
    logger,
    *,
    allow_negative: bool = False,
    budget_label: str = "Budget",
) -> str:
    """Determine OpenAI reasoning_effort level from a token budget.

    Args:
        budget: Provided token budget value.
        low_env_var: Environment variable for the low threshold.
        high_env_var: Environment variable for the high threshold.
        logger: Logger instance for output.
        allow_negative: Whether to treat -1 as an absent budget.
        budget_label: Descriptive label for log messages.

    Returns:
        "low", "medium" or "high" based on configured thresholds.

    Raises:
        ConversionError: If required environment variables are missing or invalid.
    """
    import os

    if budget is None or (allow_negative and budget == -1):
        reason = "dynamic thinking (-1)" if budget == -1 else "no budget provided"
        logger.info(
            f"No valid {budget_label.lower()} ({reason}), defaulting to reasoning_effort='high'"
        )
        return "high"

    low_threshold_str = os.environ.get(low_env_var)
    high_threshold_str = os.environ.get(high_env_var)

    if low_threshold_str is None:
        raise ConversionError(
            f"{low_env_var} environment variable is required for intelligent reasoning_effort determination"
        )
    if high_threshold_str is None:
        raise ConversionError(
            f"{high_env_var} environment variable is required for intelligent reasoning_effort determination"
        )

    try:
        low_threshold = int(low_threshold_str)
        high_threshold = int(high_threshold_str)
        logger.debug(
            f"Threshold configuration: low <= {low_threshold}, medium <= {high_threshold}, high > {high_threshold}"
        )
        if budget <= low_threshold:
            effort = "low"
        elif budget <= high_threshold:
            effort = "medium"
        else:
            effort = "high"
        logger.info(
            f"🎯 {budget_label} {budget} -> reasoning_effort '{effort}' (thresholds: low<={low_threshold}, high<={high_threshold})"
        )
        return effort
    except ValueError as e:
        raise ConversionError(
            f"Invalid threshold values in environment variables: {e}. {low_env_var} and {high_env_var} must be integers."
        )
