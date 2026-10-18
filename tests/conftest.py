import pytest
import zstandard


@pytest.fixture(scope="session")
def dictionary() -> bytes:
    samples = [f"def function_{index}(value):\n    return value + {index}\n".encode() * 8
               for index in range(100)]
    return zstandard.train_dictionary(1024, samples, threads=0).as_bytes()
