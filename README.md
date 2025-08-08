# 🎮 Bot Discord Icarus - Frères de Survie

Bot Discord pour surveiller et afficher l'état du serveur Icarus en temps réel.

## 📋 Fonctionnalités

### 🔍 Surveillance en temps réel
- **Connexions/Déconnexions** : Détection précise via `ServerTryCompletePlayerInitialisation` et `DetachPlayerFromSeat`
- **Changements de biome** : Suivi des déplacements des joueurs (`just entered new biome`)
- **Sauvegardes automatiques** : Détection des `BeginRecording`/`EndRecording`
- **État du serveur** : Ping, joueurs connectés, statut en ligne

### 📊 Affichage Discord
- **État serveur** : Statut, ping, mission actuelle
- **Joueurs actifs** : Liste avec temps de connexion
- **Activité récente** : Derniers événements avec horodatage
- **État technique** : Statut du bot et dernière vérification

## 🛠️ Configuration

### Prérequis
- Python 3.11+
- Bibliothèques : `discord.py`, `ping3`, `pytz`, `ftplib`

### Installation
```bash
pip install discord.py ping3 pytz
```

### Configuration
1. Modifier les variables dans `Icarus.py` :
   - `DISCORD_TOKEN` : Token de votre bot Discord
   - `CHANNEL_ID` : ID du canal Discord pour les messages
   - `SERVER_IP` et `SERVER_PORT` : Adresse du serveur Icarus
   - `FTP_HOST`, `FTP_USER`, `FTP_PASS` : Accès FTP pour les logs

## 🚀 Utilisation

```bash
python Icarus.py
```

### Commandes Discord
- `!help` : Affiche l'aide
- `!connect` : Informations de connexion au serveur
- `!fdp` : Commande humoristique

## 📝 Format d'affichage

Le bot affiche les informations selon ce format :

```
🎮 SERVEUR ICARUS - FRÈRES DE SURVIE
🟢 EN LIGNE • 1 joueur connecté

🌐 ÉTAT SERVEUR
🟢 Serveur: EN LIGNE (32ms) • 🎯 Mission: Avant-poste Olympus

👥 JOUEURS ACTIFS (1)
🟢 Sarah_Survivor • Connecté depuis 37min

📋 ACTIVITÉ RÉCENTE
🔴 15:15:26 MickGamer42 déconnecté
💾 14:44:15 Sauvegarde effectuée
🟢 14:38:42 Sarah_Survivor connecté

🔧 ÉTAT TECHNIQUE
📡 Bot: 🟢 Logs en temps réel • Dernière vérification: 15:15:30
```

## 🔧 Détection des événements

### Patterns de logs détectés
- **Connexion** : `ServerTryCompletePlayerInitialisation.*Name=([PlayerName])`
- **Déconnexion** : `DetachPlayerFromSeat.*Name=([PlayerName])`
- **Biome** : `just entered new biome: ([BiomeName])`
- **Sauvegarde** : `BeginRecording` / `EndRecording`

## 📁 Structure du projet

```
Icarus/
├── Icarus.py          # Script principal du bot
├── config.json        # Configuration (optionnel)
├── .gitignore         # Fichiers à ignorer par Git
├── README.md          # Documentation
└── icarus_bot.log     # Logs du bot (généré automatiquement)
```

## 🐛 Dépannage

### Erreurs courantes
- **Timeout FTP** : Vérifier la connexion réseau et les paramètres FTP
- **Bot Discord hors ligne** : Vérifier le token Discord
- **Pas de logs** : Vérifier l'accès au serveur FTP et le chemin des logs

## 👥 Contributeurs

- **Micka** - Développeur principal

## 📄 Licence

Ce projet est sous licence MIT.
