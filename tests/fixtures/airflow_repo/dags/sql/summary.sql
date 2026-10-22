-- rendered by Airflow before it is used
SELECT count(*) FROM events
WHERE event_date = '{{ execution_date.strftime("%Y-%m-%d") }}'
  AND loaded_at < '{{ tomorrow_ds }}';
