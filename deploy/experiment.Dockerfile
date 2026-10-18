FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /opt/repoweave/requirements.txt
COPY repoweave_engine-0.1.0-py3-none-any.whl /opt/repoweave/repoweave_engine-0.1.0-py3-none-any.whl
COPY smoke_engine.py /opt/repoweave/smoke_engine.py
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r /opt/repoweave/requirements.txt \
    && pip install --no-cache-dir --no-deps /opt/repoweave/repoweave_engine-0.1.0-py3-none-any.whl \
    && useradd --create-home --uid 1000 --shell /bin/bash repoweave
USER repoweave
WORKDIR /home/repoweave
RUN python /opt/repoweave/smoke_engine.py \
    && python -c "import importlib.util; assert all(importlib.util.find_spec(m) is None for m in ('daytona', 'droid_sdk', 'langgraph', 'langsmith', 'dotenv'))"
ENTRYPOINT ["sleep", "infinity"]
