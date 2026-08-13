FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py evm_simulator.py x402_verifier.py ./

EXPOSE 8000

# Prefer --workers 1; scale via replicas rather than multi-worker processes.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
