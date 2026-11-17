FROM python:3.11-slim

WORKDIR /app
COPY . .

ENV HOST=0.0.0.0
ENV PORT=8080
ENV ENABLE_TRENDS_AGENT=1

EXPOSE 8080

CMD ["python", "app.py"]
