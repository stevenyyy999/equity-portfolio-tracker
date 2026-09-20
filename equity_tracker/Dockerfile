FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Installs Stripe CLI
RUN apt-get update && apt-get install -y wget && \
    wget https://github.com/stripe/stripe-cli/releases/download/v1.37.2/stripe_1.37.2_linux_x86_64.tar.gz && \
    tar -xzf stripe_1.37.2_linux_x86_64.tar.gz && \
    mv stripe /usr/local/bin/ && \
    rm stripe_1.37.2_linux_x86_64.tar.gz

COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]