# Task code reading context["execution_date"] (or a callable taking `execution_date`):
# the key exists in 2.x and is gone in 3.x (use logical_date / data_interval_*).
try:
    from airflow.sdk.definitions.context import KNOWN_CONTEXT_KEYS  # 3.x
except ImportError:
    from airflow.utils.context import KNOWN_CONTEXT_KEYS  # 2.x
assert "execution_date" in KNOWN_CONTEXT_KEYS, "execution_date is no longer a task context key"
print("execution_date is a task context key")
