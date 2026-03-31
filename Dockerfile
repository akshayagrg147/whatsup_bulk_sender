# WhatsApp Marketing Suite - runnable image (no source needed on target)
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py config.py bulk_sender.py auto_reply.py scheduler.py analytics.py database.py excel_parser.py ./
COPY templates/ templates/
COPY static/ static/

ENV FLASK_PORT=5001
EXPOSE 5001

CMD ["python", "main.py"]
