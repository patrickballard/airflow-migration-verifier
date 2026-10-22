#!/usr/bin/env bash
# Rendered by Airflow as a Jinja template before it runs.
echo "report for {{ execution_date.strftime('%Y-%m-%d') }}"
echo "tomorrow is {{ tomorrow_ds_nodash }}"
