import numpy as np

x = np.array([1.5, np.NaN], dtype=np.float_)
print(np.product([2, 3]), np.alltrue(x > 0), np.cumproduct([1, 2]), np.in1d([1], [1]))
# NEP 50: these silently change type/value in 2.x
print(type(np.float32(3) + 3.0).__name__, int(np.uint8(200) + 100))
