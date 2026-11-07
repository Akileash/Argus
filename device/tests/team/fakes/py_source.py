"""FAKE teammate library (tests only) for the import-and-call pattern: ``read_once()`` returns one reading."""
import itertools

_n = itertools.count()
SETUP_CALLS = []


def setup():
    SETUP_CALLS.append(1)


def read_once():
    n = next(_n)
    if n % 4 == 3:
        raise OSError("i2c glitch")  # a transient error: counted, not fatal
    return {"R": [148.0 + n, 151.2]}  # cm


def stream():
    """Generator style: blocks internally, yields strings."""
    for n in itertools.count():
        yield f"R2={n} R3=0"
