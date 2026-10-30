# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
WORKDIR /guard
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY guard.py .
RUN useradd -r -u 10001 guard
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/__guard/healthz',timeout=3).status==200 else 1)"]
CMD ["python", "guard.py"]
