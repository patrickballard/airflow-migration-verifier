FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY rules ./rules
RUN pip install --no-cache-dir .

# rules are read from /app/rules by default; the package is installed, so run it from anywhere
ENTRYPOINT ["python", "-m", "api_change_agent"]
