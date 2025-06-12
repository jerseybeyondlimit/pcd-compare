FROM python:3.10-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libgl1 \
        libgomp1 \
        libboost-all-dev \
        libblas3 \
        liblapack3 \
        libopenblas-dev \
        libeigen3-dev \
        libffi-dev \
        libssl-dev \
        wget \
        vim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY handler.py /app/handler.py
COPY bin/ /app/bin/
COPY src/ /app/src/
COPY static/ /app/static/
COPY frontend/ /app/frontend/
# COPY . /app
RUN chmod +x /app/bin/PotreeConverter_linux_x64/PotreeConverter

ENV LD_LIBRARY_PATH="/app/bin/PotreeConverter_linux_x64:${LD_LIBRARY_PATH}"

EXPOSE 9000
CMD ["gunicorn", "-w", "10", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:9000", "handler:app"]
