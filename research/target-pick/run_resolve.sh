#!/usr/bin/env bash
# probe the old version, resolve against the new one; writes results/resolve-<lib>.json
cd "$(dirname "$0")"
go() {
  local lib=$1 old=$2 new=$3 pkg=$4; shift 4
  AIRFLOW_HOME="$PWD/.venvs/airflow-home-2" ".venvs/$old/bin/python" -I probe_old.py "$pkg" "results/probe-$lib.json" "$@" >/dev/null 2>"results/.probe-$lib.err" \
   && AIRFLOW_HOME="$PWD/.venvs/airflow-home-3" ".venvs/$new/bin/python" -I resolve_new.py "results/probe-$lib.json" "results/resolve-$lib.json" 2>"results/.resolve-$lib.err" | sed "s/^/$lib: /" \
   || echo "$lib: FAILED"
}
go pydantic pydantic-1 pydantic-2 pydantic pydantic.main:BaseModel &
go sqlalchemy sqlalchemy-1 sqlalchemy-2 sqlalchemy sqlalchemy.engine:Engine sqlalchemy.engine:Connection sqlalchemy.engine:Row sqlalchemy.orm:Session sqlalchemy.orm:Query sqlalchemy.sql:Select &
go numpy numpy-1 numpy-2 numpy numpy:ndarray &
go pandas pandas-2 pandas-3 pandas pandas:DataFrame pandas:Series pandas:Index pandas.core.groupby.generic:DataFrameGroupBy &
wait
go langchain langchain-0 langchain-1 langchain &
go llamaindex llamaindex-0 llamaindex-1 llama_index &
go airflow airflow-2 airflow-3 airflow airflow.models.dag:DAG airflow.models.baseoperator:BaseOperator &
go marshmallow marshmallow-3 marshmallow-4 marshmallow marshmallow:Schema marshmallow.fields:Field &
wait
