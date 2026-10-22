# Copy-on-Write is the only mode in pandas 3: chained assignment silently stops writing.
import warnings; warnings.simplefilter("ignore")
import pandas as pd
df = pd.DataFrame({"a": [1, 2, 3]})
df["a"][0] = 99
col = df["a"]; col[1] = 77
print("df.a after chained writes ->", df["a"].tolist())
