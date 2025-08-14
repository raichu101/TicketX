# Tiny Python image
FROM python:3.11-slim

# Create app dir
WORKDIR /app
COPY . /app

# Create uploads dir with write perms
RUN mkdir -p uploads && chmod -R 777 uploads

# No deps beyond stdlib
EXPOSE 8000
CMD ["python","ticketx.py","--web"]
