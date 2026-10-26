from airflow.operators.bash_operator import BashOperator      # 1.x path, deprecated shim in 2.x
from airflow.operators.python_operator import PythonOperator  # same
print("old operator paths ok:", BashOperator.__name__, PythonOperator.__name__)
