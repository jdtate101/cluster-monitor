FROM python:3.12-slim

LABEL maintainer="James Tate"
LABEL description="Kubernetes Pod Failure Monitor — alerts via Gmail when pods fail for >10 min"

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY monitor.py clusters.py alerter.py state.py ./

# Non-root user for safety
RUN useradd -r -u 1000 -s /bin/false monitor
USER monitor

CMD ["python", "-u", "monitor.py"]
