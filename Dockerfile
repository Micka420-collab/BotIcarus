# Utiliser Python 3.11 slim comme image de base
FROM python:3.11-slim

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers de requirements
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code source
COPY . .

# Exposer le port (optionnel pour les bots Discord)
EXPOSE 8080

# Variables d'environnement pour Cloud Run
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Commande pour démarrer le bot
CMD ["python", "Icarus.py"]
