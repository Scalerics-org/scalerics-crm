FROM python:3.11-slim

WORKDIR /app

# cairo/pango/gdk-pixbuf estaban para WeasyPrint, que no se usa: los PDF de
# presupuestos los genera el browser del usuario desde /api/attachments/<id>/print.
RUN apt-get update && apt-get install -y \
    curl wget gnupg \
    libffi-dev \
    libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Startup script: copies DB to persistent volume on first run, then starts app
COPY start.sh /start.sh
# Normalizar CRLF: un checkout en Windows deja el shebang como "#!/bin/sh\r" y
# el kernel busca un intérprete que no existe ("no such file or directory").
RUN sed -i 's/\r$//' /start.sh && chmod +x /start.sh

ENV DB_PATH=/data/leads.db
ENV PORT=8080

EXPOSE 8080

CMD ["/start.sh"]
