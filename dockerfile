FROM python:3.11-slim

WORKDIR /app

# Instalar dependências do sistema para o SQLite e fuso horário
RUN apt-get update && apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Criar a pasta instance para o SQLite, se não existir
RUN mkdir -p instance

# Porta que o Flask usa (ajuste se necessário)
EXPOSE 5000

# Comando para rodar com Gunicorn (recomendado para produção)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "2", "app:app"]
