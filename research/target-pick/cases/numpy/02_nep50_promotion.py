# NEP 50: Python scalars no longer upcast numpy scalars. Same code, different dtype and value.
import warnings; warnings.simplefilter("ignore")
import numpy as np
a = np.float32(3) + 3.0
b = np.uint8(200) + 100
print("float32(3)+3.0 ->", type(a).__name__, "| uint8(200)+100 ->", type(b).__name__, int(b))
