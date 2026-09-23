"""MagCopy - instant region screenshots and Discord-sized GIFs."""
import os

# Before anything imports numpy, which this package does - so this has to be the first code in it.
#
# numpy ships OpenBLAS, which starts a worker thread per CPU core the moment numpy is imported and
# commits a buffer for each. On a 20-core machine that was 19 threads and 611 MB of committed
# memory, for matrix maths MagCopy never does: numpy here only reshapes pixel buffers. One thread
# costs nothing and takes the idle footprint from ~676 MB to ~65 MB. setdefault, so anyone who
# has a reason to set these themselves still can.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

from .settings import APP_NAME, APP_VERSION  # noqa: E402

__all__ = ["APP_NAME", "APP_VERSION"]
