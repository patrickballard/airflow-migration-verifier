import warnings; warnings.simplefilter("ignore")
import pandas as pd
df = pd.DataFrame({"a": [1.0, None, 3.0]})
print(df.applymap(str).iloc[0, 0], df.fillna(method="ffill")["a"].tolist(), len(pd.date_range("2024-01-01", periods=2, freq="H")))
