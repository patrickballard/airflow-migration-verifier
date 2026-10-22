import pandas as pd
s = pd.Series(["x", "y"])
print("dtype == object ->", s.dtype == object, "| dtype:", s.dtype)  # checks like this silently flip
