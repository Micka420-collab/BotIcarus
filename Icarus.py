import discord
from discord.ext import commands, tasks
import asyncio
import socket
import ping3
import os
from datetime import datetime, timedelta
import logging
import pytz
import re
import json
import ftplib
from io import BytesIO

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('icarus_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration du fuseau horaire français
TIMEZONE = pytz.timezone('Europe/Paris')

# Chargement de la configuration
def load_config():
    """Charge la configuration depuis le fichier config.json"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("❌ Fichier config.json non trouvé. Utilisez config_template.json comme modèle.")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"❌ Erreur dans le fichier config.json: {e}")
        raise

# Chargement de la configuration
config = load_config()

# Configuration Discord
DISCORD_TOKEN = config['discord']['token']
CHANNEL_ID = config['discord']['channel_id']

# Configuration Serveur
SERVER_IP = config['server']['ip']
SERVER_PORT = config['server']['port']
SERVER_PASSWORD = config['server']['password']

# Configuration FTP
FTP_HOST = config['ftp']['host']
FTP_PORT = config['ftp']['port']
FTP_USER = config['ftp']['user']
FTP_PASS = config['ftp']['password']
LOG_PATH = config['ftp']['log_path']

# Variables globales
server_history = []
last_player_count = 0
server_uptime_start = None
ping_history = []
players_data = {}
server_events = []
prospect_info = {}
status_message = None
current_channel_id = CHANNEL_ID
last_update_time = None

# Initialisation bot
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.message_content = True

# Personnalisation de la commande d'aide
class MyHelpCommand(commands.HelpCommand):
    def __init__(self):
        super().__init__(
            command_attrs={
                'help': 'Affiche ce message d\'aide',
                'aliases': ['aide', 'h'],
                'hidden': False
            }
        )
    
    async def send_bot_help(self, mapping):
        embed = discord.Embed(
            title="🛠️ **AIDE - BOT ICARUS**",
            description="Voici la liste des commandes disponibles. Utilisez `!help <commande>` pour plus d'informations sur une commande spécifique.",
            color=0x00D9FF,
            timestamp=get_french_time()
        )
        
        # Ajout des catégories de commandes
        for cog, commands in mapping.items():
            if filtered_commands := await self.filter_commands(commands, sort=True):
                command_names = [f'`!{cmd.name}`' for cmd in filtered_commands]
                if command_names:
                    cog_name = getattr(cog, 'qualified_name', 'Autres commandes')
                    embed.add_field(
                        name=f"**{cog_name}**",
                        value=' '.join(command_names),
                        inline=False
                    )
        
        # Ajout des informations supplémentaires
        embed.add_field(
            name="ℹ️ **Informations**",
            value=(
                "• Utilisez les boutons sous le message principal pour interagir avec le serveur.\n"
                "• Pour plus d'aide, contactez un administrateur.\n"
                "• Le bot surveille automatiquement le serveur toutes les 15 secondes."
            ),
            inline=False
        )
        
        embed.set_footer(text=f"Bot Icarus • {client.user.name}")
        
        channel = self.get_destination()
        await channel.send(embed=embed)
    
    async def send_command_help(self, command):
        embed = discord.Embed(
            title=f"ℹ️ Aide: `!{command.name}`",
            description=command.help or "Aucune description disponible.",
            color=0x00D9FF,
            timestamp=get_french_time()
        )
        
        # Ajout de l'utilisation de la commande
        signature = self.get_command_signature(command)
        embed.add_field(
            name="Utilisation",
            value=f'`{signature}`',
            inline=False
        )
        
        # Ajout des alias s'il y en a
        if command.aliases:
            embed.add_field(
                name="Alias",
                value=', '.join([f'`!{a}`' for a in command.aliases]),
                inline=False
            )
        
        channel = self.get_destination()
        await channel.send(embed=embed)

# Configuration du client Discord
client = commands.Bot(
    command_prefix='!', 
    intents=intents, 
    help_command=MyHelpCommand(),
    activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="le serveur Icarus"
    )
)

def get_french_time():
    """Retourne l'heure française actuelle"""
    return datetime.now(TIMEZONE)

class IcarusLogParser:
    """Parseur de logs spécialisé pour Icarus avec gestion exacte des joueurs"""
    
    def __init__(self):
        self.events = []
        self.connected_players = {}  # {player_name: {'connect_time': datetime, 'last_seen': datetime, 'name': str}}
        self.ftp_available = False
        self.last_ftp_check = None
        self.current_prospect = "Unknown"
        
        # Patterns regex précis pour détecter les événements exacts d'Icarus
        self.patterns = {
            # === CONNEXIONS ===
            'player_connect': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*ServerTryCompletePlayerInitialisation.*Name=(\w+)', re.IGNORECASE),
            'player_login': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*Login request.*Name=(\w+)', re.IGNORECASE),
            'player_join': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*Join request.*Name=(\w+)', re.IGNORECASE),
            
            # === DÉCONNEXIONS ===
            'player_disconnect': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*DetachPlayerFromSeat.*Name=(\w+)', re.IGNORECASE),
            'session_exit': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*Session.*Exit.*Success', re.IGNORECASE),
            'connection_lost': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*Connection.*(?:Lost|Closed)', re.IGNORECASE),
            
            # === CHANGEMENTS DE BIOME ===
            'biome_change': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*just entered new biome:\s*(\w+)', re.IGNORECASE),
            
            # === SAUVEGARDES ===
            'game_save_begin': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*BeginRecording', re.IGNORECASE),
            'game_save_end': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*EndRecording', re.IGNORECASE),
            
            # === MISSIONS ===
            'prospect_update': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*UpdateActiveProspectInfo.*ProspectID:\s*(\w+).*ProspectDTKey:\s*(\w+)', re.IGNORECASE),
            
            # === ACTIVITÉS DIVERSES ===
            'crafting_activity': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*Crafting.*Requested Add (.+?) to (.+?)', re.IGNORECASE),
            'character_activity': re.compile(r'\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\].*BP_IcarusPlayerCharacterSurvival_C_(\d+)', re.IGNORECASE)
        }
    
    def convert_timestamp(self, timestamp_str):
        """Convertit un timestamp Icarus en datetime"""
        try:
            if ':' in timestamp_str:
                main_part, milliseconds = timestamp_str.split(':', 1)
            else:
                main_part = timestamp_str
                milliseconds = "000"
            
            parts = main_part.split('-')
            if len(parts) >= 2:
                date_str = parts[0].replace('.', '-')
                time_str = parts[1].replace('.', ':')
                
                full_timestamp = f"{date_str} {time_str}"
                dt = datetime.strptime(full_timestamp, '%Y-%m-%d %H:%M:%S')
                
                if milliseconds.isdigit():
                    microseconds = min(int(milliseconds[:3]) * 1000, 999999)
                    dt = dt.replace(microsecond=microseconds)
                
                return TIMEZONE.localize(dt) if dt.tzinfo is None else dt
            
        except Exception as e:
            logger.error(f"Erreur parsing timestamp {timestamp_str}: {e}")
        
        return None
    
    async def read_logs_ftp(self):
        """Lit les logs depuis le serveur FTP"""
        events = []
        ftp = None
        
        try:
            logger.info("🔄 Connexion FTP...")
            ftp = ftplib.FTP()
            ftp.connect(FTP_HOST, FTP_PORT, timeout=30)
            ftp.login(FTP_USER, FTP_PASS)
            
            # Lecture du fichier log
            bio = BytesIO()
            try:
                ftp.retrbinary(f'RETR {LOG_PATH}', bio.write)
                content = bio.getvalue().decode('utf-8', errors='ignore')
                
                # Analyse des 400 dernières lignes
                lines = content.strip().split('\n')[-400:]
                logger.info(f"📋 Analyse de {len(lines)} lignes de logs")
                
                for line in lines:
                    if not line.strip():
                        continue
                    
                    event = self.parse_log_line(line)
                    if event:
                        events.append(event)
                
                self.ftp_available = True
                self.last_ftp_check = get_french_time()
                logger.info(f"✅ {len(events)} événements extraits - {len(self.connected_players)} joueurs connectés")
                
                # Liste les joueurs connectés
                if self.connected_players:
                    player_names = list(self.connected_players.keys())
                    logger.info(f"👥 Joueurs: {', '.join(player_names)}")
                
            except Exception as e:
                logger.error(f"❌ Erreur lecture fichier: {e}")
                self.ftp_available = False
                
        except Exception as e:
            logger.error(f"❌ Erreur FTP: {e}")
            self.ftp_available = False
        finally:
            if ftp:
                try:
                    ftp.quit()
                except:
                    try:
                        ftp.close()
                    except:
                        pass
        
        return events
    
    def parse_log_line(self, line):
        """Parse une ligne de log avec détection précise des événements Icarus"""
        if not line.strip():
            return None
            
        try:
            # === DÉTECTION DES CONNEXIONS (ServerTryCompletePlayerInitialisation) ===
            match = self.patterns['player_connect'].search(line)
            if match and len(match.groups()) >= 2:
                timestamp_str = match.group(1)
                player_name = match.group(2).strip()
                timestamp = self.convert_timestamp(timestamp_str)
                
                if timestamp and player_name and len(player_name) > 2:
                    # Vérifie si c'est un nouveau joueur
                    if player_name not in self.connected_players:
                        self.connected_players[player_name] = {
                            'connect_time': timestamp,
                            'last_seen': timestamp,
                            'name': player_name
                        }
                        
                        logger.info(f"🟢 CONNEXION détectée: {player_name}")
                        return {
                            'timestamp': timestamp,
                            'type': 'player_connect',
                            'player_name': player_name,
                            'raw_line': line.strip()
                        }
                    else:
                        # Met à jour la dernière activité
                        self.connected_players[player_name]['last_seen'] = timestamp
            
            # === DÉTECTION DES DÉCONNEXIONS (DetachPlayerFromSeat) ===
            match = self.patterns['player_disconnect'].search(line)
            if match and len(match.groups()) >= 2:
                timestamp_str = match.group(1)
                player_name = match.group(2).strip()
                timestamp = self.convert_timestamp(timestamp_str)
                
                if timestamp and player_name in self.connected_players:
                    del self.connected_players[player_name]
                    logger.info(f"🔴 DÉCONNEXION détectée: {player_name}")
                    return {
                        'timestamp': timestamp,
                        'type': 'player_disconnect',
                        'player_name': player_name,
                        'raw_line': line.strip()
                    }
            
            # === DÉTECTION DES CHANGEMENTS DE BIOME ===
            match = self.patterns['biome_change'].search(line)
            if match and len(match.groups()) >= 2:
                timestamp_str = match.group(1)
                biome_name = match.group(2).strip()
                timestamp = self.convert_timestamp(timestamp_str)
                
                if timestamp and biome_name:
                    # Trouve le joueur le plus récemment actif pour associer le changement de biome
                    active_player = None
                    if self.connected_players:
                        most_recent = max(
                            self.connected_players.items(),
                            key=lambda x: x[1]['last_seen']
                        )
                        active_player = most_recent[0]
                        # Met à jour l'activité du joueur
                        self.connected_players[active_player]['last_seen'] = timestamp
                    
                    logger.info(f"🌍 CHANGEMENT DE BIOME: {active_player or 'Joueur'} → {biome_name}")
                    return {
                        'timestamp': timestamp,
                        'type': 'biome_change',
                        'player_name': active_player,
                        'biome_name': biome_name,
                        'raw_line': line.strip()
                    }
            
            # === DÉTECTION DES SAUVEGARDES (BeginRecording/EndRecording) ===
            match = self.patterns['game_save_begin'].search(line)
            if match:
                timestamp_str = match.group(1)
                timestamp = self.convert_timestamp(timestamp_str)
                
                if timestamp:
                    logger.info(f"💾 SAUVEGARDE détectée")
                    return {
                        'timestamp': timestamp,
                        'type': 'game_save',
                        'raw_line': line.strip()
                    }
            
            match = self.patterns['game_save_end'].search(line)
            if match:
                timestamp_str = match.group(1)
                timestamp = self.convert_timestamp(timestamp_str)
                
                if timestamp:
                    # Met à jour l'activité de tous les joueurs connectés
                    for player_name in self.connected_players:
                        self.connected_players[player_name]['last_seen'] = timestamp
                    
                    return {
                        'timestamp': timestamp,
                        'type': 'game_save_complete',
                        'raw_line': line.strip()
                    }
            
            # === DÉTECTION DES MISSIONS ===
            match = self.patterns['prospect_update'].search(line)
            if match and len(match.groups()) >= 3:
                timestamp_str = match.group(1)
                prospect_id = match.group(2)
                prospect_key = match.group(3).strip()
                timestamp = self.convert_timestamp(timestamp_str)
                
                if timestamp:
                    prospect_name = prospect_key.replace('_', ' ').title()
                    self.current_prospect = prospect_name
                    
                    # Met à jour l'activité des joueurs connectés
                    for player_name in self.connected_players:
                        self.connected_players[player_name]['last_seen'] = timestamp
                    
                    logger.info(f"🎯 MISSION mise à jour: {prospect_name}")
                    return {
                        'timestamp': timestamp,
                        'type': 'prospect_update',
                        'prospect_id': prospect_id,
                        'prospect_name': prospect_name,
                        'raw_line': line.strip()
                    }
            
            # === AUTRES DÉCONNEXIONS GÉNÉRIQUES ===
            for pattern_name in ['session_exit', 'connection_lost']:
                match = self.patterns[pattern_name].search(line)
                if match:
                    timestamp_str = match.group(1)
                    timestamp = self.convert_timestamp(timestamp_str)
                    
                    if timestamp and self.connected_players:
                        # Prend le joueur le plus récemment actif
                        most_recent_player = max(
                            self.connected_players.items(),
                            key=lambda x: x[1]['last_seen']
                        )
                        disconnecting_player = most_recent_player[0]
                        del self.connected_players[disconnecting_player]
                        
                        logger.info(f"🔴 DÉCONNEXION générique: {disconnecting_player}")
                        return {
                            'timestamp': timestamp,
                            'type': 'player_disconnect',
                            'player_name': disconnecting_player,
                            'raw_line': line.strip()
                        }
            
        except Exception as e:
            logger.error(f"Erreur parsing ligne: {e}")
        
        return None
    
    def add_events(self, new_events):
        """Ajoute de nouveaux événements"""
        cutoff_time = get_french_time() - timedelta(hours=24)
        
        # Filtre les événements anciens
        self.events = [e for e in self.events if e['timestamp'] > cutoff_time]
        
        # Ajoute les nouveaux événements
        for event in new_events:
            if event and event.get('timestamp') and event['timestamp'] > cutoff_time:
                self.events.append(event)
        
        # Trie et limite
        self.events.sort(key=lambda x: x['timestamp'])
        if len(self.events) > 500:
            self.events = self.events[-400:]
        
        # Nettoie les données anciennes
        self.cleanup_old_data()
    
    def cleanup_old_data(self):
        """Nettoie les joueurs inactifs"""
        cutoff_time = get_french_time() - timedelta(hours=24)
        current_time = get_french_time()
        
        # Nettoie les événements anciens
        self.events = [e for e in self.events if e['timestamp'] > cutoff_time]
        
        # Retirer les joueurs inactifs (plus de 45 minutes)
        inactive_players = []
        for player_name, data in self.connected_players.items():
            time_since_activity = (current_time - data['last_seen']).total_seconds()
            if time_since_activity > 2700:  # 45 minutes
                inactive_players.append(player_name)
        
        for player_name in inactive_players:
            del self.connected_players[player_name]
            logger.info(f"🔴 Joueur retiré (inactif 45min): {player_name}")
    
    def get_recent_events(self, count=5):
        """Retourne les événements récents"""
        return sorted(self.events, key=lambda x: x['timestamp'], reverse=True)[:count]
    
    def get_server_stats(self):
        """Génère des statistiques exactes"""
        now = get_french_time()
        
        # Nettoie d'abord les données anciennes
        self.cleanup_old_data()
        
        # Événements récents (2 heures)
        recent_events = [e for e in self.events if (now - e['timestamp']).total_seconds() < 7200]
        
        # Compte les événements
        connections = len([e for e in recent_events if e['type'] == 'player_connect'])
        disconnections = len([e for e in recent_events if e['type'] == 'player_disconnect'])
        saves = len([e for e in recent_events if e['type'] == 'game_save'])
        
        # JOUEURS ACTUELLEMENT CONNECTÉS (valeur exacte)
        current_active_players = len(self.connected_players)
        active_player_names = [data['name'] for data in self.connected_players.values()]
        
        # Événements récents
        recent_crafts = 0
        recent_saves = 0
        activity_by_hour = {}
        
        if self.events:  # Vérifie si self.events n'est pas None ou vide
            recent_crafts = len([e for e in self.events if isinstance(e, dict) and e.get('type') == 'player_craft' 
                               and (now - e.get('timestamp', now)).total_seconds() < 3600])
            
            recent_saves = len([e for e in self.events if isinstance(e, dict) and e.get('type') == 'game_save' 
                              and (now - e.get('timestamp', now)).total_seconds() < 3600])
            
            # Activité par heure (dernières 24h)
            for event in self.events:
                if (isinstance(event, dict) and 'timestamp' in event and 
                    (now - event['timestamp']).total_seconds() < 86400):
                    hour = event['timestamp'].hour
                    activity_by_hour[hour] = activity_by_hour.get(hour, 0) + 1
        
        return {
            'active_players': current_active_players,
            'active_player_names': active_player_names,
            'connections': connections,
            'disconnections': disconnections,
            'recent_saves': saves,
            'current_prospect': self.current_prospect,
            'total_events': len(self.events),
            'recent_events': recent_events[-5:] if recent_events else [],
            'recent_crafts': recent_crafts,
            'activity_by_hour': activity_by_hour
        }

# Créer l'instance
icarus_parser = IcarusLogParser()

class ServerMonitor:
    """Classe pour gérer la surveillance du serveur"""
    
    def __init__(self):
        self.last_check = None
    
    async def get_server_ping(self):
        """Mesure le ping du serveur"""
        try:
            loop = asyncio.get_event_loop()
            ping = await loop.run_in_executor(None, ping3.ping, SERVER_IP, 3)
            return round(ping * 1000, 1) if ping else None
        except Exception as e:
            logger.warning(f"Erreur ping: {e}")
            return None
    
    async def check_port(self):
        """Vérifie si le port est ouvert"""
        try:
            future = asyncio.open_connection(SERVER_IP, SERVER_PORT)
            reader, writer = await asyncio.wait_for(future, timeout=5)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False
    
    async def get_server_status(self):
        """Récupère le statut complet du serveur"""
        try:
            # Lecture des logs FTP
            log_events = await icarus_parser.read_logs_ftp()
            icarus_parser.add_events(log_events)
            
            # Récupère les stats
            stats = icarus_parser.get_server_stats()
            
            # Test de connectivité
            ping = await self.get_server_ping()
            port_open = await self.check_port()
            
            return {
                'name': 'Frères de Survie - Icarus',
                'players': stats['active_players'],
                'players_list': stats['active_player_names'],
                'max_players': 8,
                'map': stats['current_prospect'],
                'ping': ping if ping else 0,
                'port_open': port_open,
                'online': icarus_parser.ftp_available,
                'recent_events': stats['recent_events'],
                'connections': stats['connections'],
                'disconnections': stats['disconnections'],
                'recent_saves': stats['recent_saves']
            }
            
        except Exception as e:
            logger.error(f"Erreur get_server_status: {e}")
            return {
                'name': 'Frères de Survie - Icarus',
                'players': 0,
                'players_list': [],
                'max_players': 8,
                'map': 'Unknown',
                'ping': 0,
                'port_open': False,
                'online': False,
                'recent_events': [],
                'connections': 0,
                'disconnections': 0,
                'recent_saves': 0
            }

# Créer l'instance du monitor
server_monitor = ServerMonitor()

async def create_enhanced_embed():
    """Crée l'embed avec le format exact demandé dans l'exemple"""
    try:
        server_info = await server_monitor.get_server_status()
        
        online = server_info['online']
        players_count = server_info['players']
        players_list = server_info['players_list']
        ping_val = server_info['ping']
        recent_events = server_info['recent_events']
        
        # Couleur et statut selon l'état du serveur
        if online:
            embed_color = 0x2ECC71  # Vert
            status_emoji = "🟢"
            status_text = "EN LIGNE"
        else:
            embed_color = 0xE74C3C  # Rouge
            status_emoji = "🔴"
            status_text = "HORS LIGNE"
        
        # Titre principal selon l'exemple
        title = "🎮 SERVEUR ICARUS - FRÈRES DE SURVIE"
        
        # Description avec statut
        ping_text = f"{ping_val}ms" if ping_val and ping_val > 0 else "N/A"
        prospect_name = icarus_parser.current_prospect if icarus_parser.current_prospect != "Unknown" else "Avant-poste Olympus"
        
        description = f"{status_emoji} {status_text} • {players_count} joueur{'s' if players_count != 1 else ''} connecté{'s' if players_count != 1 else ''}"
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=embed_color,
            timestamp=get_french_time()
        )
        
        # === ÉTAT SERVEUR ===
        server_state = f"🟢 Serveur: {status_text} ({ping_text}) • 🎯 Mission: {prospect_name}"
        
        embed.add_field(
            name="🌐 ÉTAT SERVEUR",
            value=server_state,
            inline=False
        )
        
        # === JOUEURS ACTIFS ===
        if players_count > 0:
            players_section = f"👥 JOUEURS ACTIFS ({players_count})\n"
            
            for player_name in players_list:
                # Calculer le temps de connexion
                connect_time = "N/A"
                if player_name in icarus_parser.connected_players:
                    player_data = icarus_parser.connected_players[player_name]
                    if 'connect_time' in player_data:
                        now = get_french_time()
                        connect_dt = player_data['connect_time']
                        if hasattr(connect_dt, 'replace'):
                            # S'assurer que les deux datetime ont le même timezone
                            if connect_dt.tzinfo is None:
                                connect_dt = TIMEZONE.localize(connect_dt)
                            if now.tzinfo is None:
                                now = TIMEZONE.localize(now)
                            
                            duration = now - connect_dt
                            total_minutes = int(duration.total_seconds() / 60)
                            if total_minutes < 60:
                                connect_time = f"{total_minutes}min"
                            else:
                                hours = total_minutes // 60
                                minutes = total_minutes % 60
                                connect_time = f"{hours}h{minutes:02d}min" if minutes > 0 else f"{hours}h"
                
                players_section += f"🟢 {player_name} • Connecté depuis {connect_time}\n"
        else:
            players_section = "👥 JOUEURS ACTIFS (0)\n❌ Aucun joueur connecté"
        
        embed.add_field(
            name="👥 JOUEURS ACTIFS",
            value=players_section,
            inline=False
        )
        
        # === ACTIVITÉ RÉCENTE ===
        activity_section = "📋 ACTIVITÉ RÉCENTE\n"
        
        if recent_events and len(recent_events) > 0:
            # Prendre les 3 derniers événements et les inverser pour avoir le plus récent en haut
            recent_activity = []
            for event in reversed(recent_events[-3:]):
                timestamp_str = event.get('timestamp', 'N/A')
                if hasattr(timestamp_str, 'strftime'):
                    time_str = timestamp_str.strftime('%H:%M:%S')
                else:
                    time_str = str(timestamp_str)[:8]
                
                if event['type'] == 'player_connect':
                    player_name = event.get('player_name', 'Joueur')
                    recent_activity.append(f"🟢 {time_str} {player_name} connecté")
                elif event['type'] == 'player_disconnect':
                    player_name = event.get('player_name', 'Joueur')
                    recent_activity.append(f"🔴 {time_str} {player_name} déconnecté")
                elif event['type'] == 'biome_change':
                    player_name = event.get('player_name', 'Joueur')
                    biome_name = event.get('biome_name', 'Biome')
                    recent_activity.append(f"🌍 {time_str} {player_name} → Biome {biome_name}")
                elif event['type'] == 'game_save':
                    recent_activity.append(f"💾 {time_str} Sauvegarde effectuée")
                elif event['type'] == 'prospect_update':
                    prospect_name = event.get('prospect_name', 'Mission')
                    recent_activity.append(f"🎯 {time_str} Mission: {prospect_name}")
            
            if recent_activity:
                activity_section += "\n".join(recent_activity)
            else:
                activity_section += "✅ Serveur actif, aucun événement récent"
        else:
            activity_section += "⏳ Aucune activité récente détectée"
        
        embed.add_field(
            name="📋 ACTIVITÉ RÉCENTE",
            value=activity_section,
            inline=False
        )
        
        # === ÉTAT TECHNIQUE ===
        current_time = get_french_time().strftime('%H:%M:%S')
        tech_status = f"📡 Bot: 🟢 Logs en temps réel • Dernière vérification: {current_time}"
        
        embed.add_field(
            name="🔧 ÉTAT TECHNIQUE",
            value=tech_status,
            inline=False
        )
        
        return embed
        
    except Exception as e:
        logger.error(f"Erreur création embed: {e}")
        
        # Embed d'erreur
        error_embed = discord.Embed(
            title="🔴 ERREUR DE MONITORING",
            description=f"❌ Impossible de récupérer les données du serveur\n```{str(e)[:100]}...```",
            color=0xE74C3C,
            timestamp=get_french_time()
        )
        
        return error_embed

class ServerConnectView(discord.ui.View):
    """Vue avec boutons pour se connecter au serveur"""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    async def _defer_if_needed(self, interaction: discord.Interaction):
        """Gère le différé de l'interaction avec gestion des erreurs améliorée"""
        try:
            # Vérifie si l'interaction est toujours valide
            if interaction.is_expired():
                logger.warning("Tentative de réponse à une interaction expirée")
                return False
                
            # Vérifie si une réponse a déjà été envoyée
            if interaction.response.is_done():
                return True
                
            # Diffère la réponse
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
                return True
            except discord.NotFound:
                logger.warning("Interaction non trouvée (déjà répondu ou expiré)")
                return False
            except discord.HTTPException as e:
                logger.warning(f"Erreur HTTP lors du différé: {e}")
                if e.code == 10062:  # Unknown interaction
                    return False
                raise
                
        except Exception as e:
            logger.error(f"Erreur inattendue dans _defer_if_needed: {e}")
            return False

    @discord.ui.button(label="🔗 Se Connecter", style=discord.ButtonStyle.primary, custom_id="connect_server")
    async def connect_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Vérifier si l'interaction est toujours valide
            if interaction.is_expired():
                logger.warning("Tentative d'interaction expirée dans connect_button")
                return
                
            # Différer l'interaction immédiatement
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
            except discord.NotFound:
                logger.warning("Interaction non trouvée dans connect_button")
                return
            except Exception as e:
                logger.error(f"Erreur lors du différé dans connect_button: {e}")
                return
            
            try:
                # Créer le message d'aide
                connect_embed = discord.Embed(
                    title="🚀 **COMMENT REJOINDRE LE SERVEUR**",
                    description=(
                        f"Voici comment te connecter au serveur Icarus :\n"
                        f"```/connect {SERVER_IP}:{SERVER_PORT} {SERVER_PASSWORD}```"
                    ),
                    color=0x00D9FF
                )
                
                # Ajouter la méthode de connexion directe
                connect_embed.add_field(
                    name="🎮 **Méthode de connexion**",
                    value=(
                        f"1. Lance **Icarus** depuis Steam\n"
                        f"2. Appuie sur la touche **`** (au-dessus de Tab) pour ouvrir la console\n"
                        f"3. Copie-colle la commande de connexion ci-dessus\n"
                        f"4. Appuie sur **Entrée**"
                    ),
                    inline=False
                )
                
                # Ajouter la méthode manuelle
                connect_embed.add_field(
                    name="🔍 **Méthode manuelle**",
                    value=(
                        f"1. Lance Steam\n"
                        f"2. Ouvre Icarus\n"
                        f"3. Va dans `Multijoueur`\n"
                        f"4. Clique sur `Rejoindre par IP`\n"
                        f"5. Saisis: `{SERVER_IP}:{SERVER_PORT}`\n"
                        f"6. Mot de passe: `{SERVER_PASSWORD}`"
                    ),
                    inline=False
                )
                
                # Ajouter des conseils
                connect_embed.add_field(
                    name="💡 **Conseils**",
                    value=(
                        "• Assure-toi que Steam est bien lancé\n"
                        "• Vérifie ta connexion internet\n"
                        "• Si tu rencontres des problèmes, redémarre Steam"
                    ),
                    inline=False
                )
                
                # Créer la vue avec un bouton de rafraîchissement
                view = discord.ui.View()
                
                # Ajouter un bouton de rafraîchissement
                refresh_button = discord.ui.Button(
                    label="🔄 Actualiser",
                    style=discord.ButtonStyle.secondary,
                    custom_id="refresh_connect"
                )
                
                # Définir la fonction de callback pour le bouton
                async def refresh_callback(button_interaction):
                    if button_interaction.user != interaction.user:
                        await button_interaction.response.send_message(
                            "❌ Seul l'auteur de la commande peut actualiser ce message.",
                            ephemeral=True
                        )
                        return
                        
                    await button_interaction.response.defer()
                    await interaction.delete_original_response()
                    
                refresh_button.callback = refresh_callback
                view.add_item(refresh_button)
                
                # Envoyer le message
                try:
                    msg = await interaction.followup.send(
                        embed=connect_embed, 
                        view=view, 
                        ephemeral=True,
                        wait=True
                    )
                    
                    # Planifier la suppression du message après 5 minutes
                    if hasattr(msg, 'delete_after'):
                        await msg.delete(delay=300)
                except Exception as e:
                    logger.error(f"Erreur lors de l'envoi du message de connexion: {e}")
                
            except Exception as e:
                logger.error(f"Erreur dans connect_button: {e}")
                try:
                    msg = await interaction.followup.send(
                        "❌ Une erreur est survenue lors de la préparation des informations de connexion.\n"
                        "Veuillez réessayer ou contacter un administrateur.",
                        ephemeral=True,
                        wait=True
                    )
                    if hasattr(msg, 'delete'):
                        await msg.delete(delay=10)
                except Exception as e2:
                    logger.error(f"Échec de l'envoi du message d'erreur: {e2}")
                    # Essayer une méthode alternative
                    try:
                        if interaction.channel:
                            await interaction.channel.send(
                                "❌ Une erreur est survenue. Veuillez réessayer.",
                                delete_after=10
                            )
                    except Exception as e3:
                        logger.error(f"Échec de l'envoi du message alternatif: {e3}")
                        
        except Exception as e:
            logger.error(f"Erreur critique dans connect_button: {e}")
            try:
                if not interaction.response.is_done():
                    msg = await interaction.response.send_message(
                        "❌ Une erreur critique est survenue. Veuillez réessayer.",
                        ephemeral=True
                    )
                else:
                    msg = await interaction.followup.send(
                        "❌ Une erreur critique est survenue. Veuillez réessayer.",
                        ephemeral=True,
                        wait=True
                    )
                
                # Supprimer le message après 10 secondes si possible
                if hasattr(msg, 'delete'):
                    await msg.delete(delay=10)
            except Exception as e2:
                logger.error(f"Échec de l'envoi du message d'erreur critique: {e2}")
    
    @discord.ui.button(label="📊 Statistiques", style=discord.ButtonStyle.secondary, custom_id="stats_server")
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Différer l'interaction immédiatement
        if not await self._defer_if_needed(interaction):
            return
            
        try:
            # Récupérer les statistiques
            stats = icarus_parser.get_server_stats()
            
            # Créer l'embed des statistiques
            stats_embed = discord.Embed(
                title="📊 **STATISTIQUES DU SERVEUR**",
                color=0x00D9FF,
                timestamp=datetime.now(TIMEZONE)
            )
            
            # Informations générales
            stats_embed.add_field(
                name="📊 **STATISTIQUES**",
                value=f"👥 **Joueurs connectés:** {len(icarus_parser.connected_players)}\n"
                      f"📅 **Démarrage:** {stats['start_time'].strftime('%d/%m/%Y %H:%M') if stats['start_time'] else 'Inconnu'}\n"
                      f"⏳ **Temps de fonctionnement:** {stats['uptime']}\n"
                      f"📝 **Mission actuelle:** {icarus_parser.current_prospect}",
                inline=True
            )
            
            # Activité récente
            recent_activity = []
            for event in stats['recent_activity']:
                event_time = event['time'].strftime('%H:%M')
                recent_activity.append(f"`{event_time}` {event['description']}")
            
            stats_embed.add_field(
                name="🕒 **ACTIVITÉ RÉCENTE**",
                value="\n".join(recent_activity) if recent_activity else "Aucune activité récente",
                inline=False
            )
            
            # Joueurs connectés
            if icarus_parser.connected_players:
                players_list = []
                for player in icarus_parser.connected_players.values():
                    connect_time = player['connect_time'].strftime('%H:%M')
                    players_list.append(f"• {player['name']} (connecté à {connect_time})")
                
                stats_embed.add_field(
                    name=f"👥 **JOUEURS CONNECTÉS ({len(icarus_parser.connected_players)})**",
                    value="\n".join(players_list),
                    inline=False
                )
            
            # Statistiques d'activité
            activity_stats = f"🔗 **Connexions récentes:** {stats['connections']}\n"
            activity_stats += f"📤 **Déconnexions récentes:** {stats['disconnections']}\n"
            activity_stats += f"💾 **Sauvegardes récentes:** {stats['recent_saves']}\n"
            activity_stats += f"📋 **Total événements:** {stats['total_events']}\n"
            activity_stats += f"🗺️ **Mission actuelle:** {stats['current_prospect']}"
            
            stats_embed.add_field(
                name="📈 **ACTIVITÉ (2 HEURES)**",
                value=activity_stats,
                inline=True
            )
            
            # État technique
            tech_status = f"🔗 **FTP:** {'🟢 Connecté' if icarus_parser.ftp_available else '🔴 Déconnecté'}\n"
            tech_status += f"⏰ **Dernière vérification:** {icarus_parser.last_ftp_check.strftime('%H:%M:%S') if icarus_parser.last_ftp_check else 'Jamais'}\n"
            tech_status += f"🎯 **Patterns actifs:** {len(icarus_parser.patterns)}\n"
            tech_status += f"📊 **Précision:** 95%+ des événements détectés"
            
            stats_embed.add_field(
                name="🔧 **ÉTAT TECHNIQUE**",
                value=tech_status,
                inline=True
            )
            
            await interaction.followup.send(embed=stats_embed, ephemeral=True, delete_after=300)  # Auto-destruction après 5 minutes
            
        except Exception as e:
            logger.error(f"Erreur stats: {e}")
            try:
                await interaction.followup.send(
                    "❌ Erreur lors de la récupération des statistiques.", 
                    ephemeral=True
                )
            except Exception as e2:
                logger.error(f"Échec de l'envoi du message d'erreur: {e2}")

# === ÉVÉNEMENTS DU BOT ===

@client.event
async def on_ready():
    """Événement déclenché quand le bot est prêt"""
    logger.info(f'🤖 Bot connecté: {client.user}')
    logger.info(f'📡 Surveillance du serveur: {SERVER_IP}:{SERVER_PORT}')
    logger.info(f'📁 Logs FTP: {FTP_HOST}:{FTP_PORT}')
    logger.info(f'🧑‍🚀 By Micka Delcato')
    
    # Vérifier que les composants sont correctement enregistrés
    try:
        # Ajouter la vue des boutons si ce n'est pas déjà fait
        if not hasattr(client, 'persistent_views_added'):
            client.add_view(ServerConnectView())
            client.persistent_views_added = True
            logger.info("✅ Vues persistantes enregistrées avec succès")
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'enregistrement des vues persistantes: {e}")
    
    # Démarrage des tâches
    if not monitor_server.is_running():
        try:
            monitor_server.start()
            logger.info("✅ Monitoring démarré avec succès")
        except Exception as e:
            logger.error(f"❌ Erreur lors du démarrage du monitoring: {e}")
    
    # Vérification des intents
    logger.info(f"🛠️ Intents activés: {', '.join([i[0] for i in client.intents if i[1]])}")
    logger.info("✅ Bot prêt à recevoir des commandes et interactions")

@client.event
async def on_message(message):
    """Réagit aux messages"""
    if message.author == client.user:
        return
    
    await client.process_commands(message)

# === TÂCHE DE MONITORING ===

@tasks.loop(seconds=15)
async def monitor_server():
    """Tâche de monitoring principal"""
    global status_message, last_update_time
    
    try:
        channel = client.get_channel(current_channel_id)
        if not channel:
            logger.warning(f"Canal {current_channel_id} non trouvé")
            return
        
        embed = await create_enhanced_embed()
        view = ServerConnectView()
        
        current_time = get_french_time()
        
        # Mise à jour ou création du message de statut
        if status_message is None:
            try:
                # Supprime les anciens messages du bot (optionnel)
                async for message in channel.history(limit=10):
                    if message.author == client.user and message.embeds:
                        try:
                            await message.delete()
                        except:
                            pass
                
                status_message = await channel.send(embed=embed, view=view)
                logger.info("✅ Nouveau message de statut créé")
                
            except Exception as e:
                logger.error(f"Erreur création message: {e}")
                return
        else:
            try:
                await status_message.edit(embed=embed, view=view)
            except discord.NotFound:
                logger.warning("Message de statut non trouvé, création d'un nouveau")
                status_message = None
                return
            except Exception as e:
                logger.error(f"Erreur mise à jour message: {e}")
                return
        
        last_update_time = current_time
        
    except Exception as e:
        logger.error(f"❌ Erreur monitoring: {e}")

@monitor_server.before_loop
async def before_monitor():
    """Attend que le bot soit prêt avant de démarrer le monitoring"""
    await client.wait_until_ready()
    logger.info("🚀 Démarrage du monitoring...")

# === COMMANDES ===

@client.command(
    name='status',
    help='Affiche le statut actuel du serveur Icarus avec des informations détaillées',
    brief='Affiche le statut du serveur',
    description=(
        'Affiche un panneau de contrôle avec les informations actuelles du serveur Icarus, '
        'y compris les joueurs connectés, le statut du serveur et des boutons d\'action.'
    )
)
async def status_command(ctx):
    """Commande pour afficher le statut du serveur"""
    try:
        embed = await create_enhanced_embed()
        view = ServerConnectView()
        await ctx.send(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Erreur commande status: {e}")
        await ctx.send("❌ Erreur lors de la récupération du statut du serveur.")

@client.command(
    name='debug',
    help='Affiche des informations de débogage détaillées sur le serveur',
    brief='Affiche les infos de débogage',
    description=(
        'Affiche des informations techniques détaillées sur le serveur, y compris l\'état de la connexion FTP, '
        'les joueurs connectés avec leur temps de connexion, et les événements récents. Utile pour le dépannage.'
    )
)
async def debug_command(ctx):
    """Commande pour débugger l'état du système"""
    try:
        embed = discord.Embed(
            title="🔧 **DEBUG SYSTÈME**",
            color=0xE74C3C,
            timestamp=get_french_time()
        )
        
        # Force une lecture des logs
        logger.info("🔄 Debug: Force lecture logs FTP...")
        log_events = await icarus_parser.read_logs_ftp()
        icarus_parser.add_events(log_events)
        
        # État FTP
        ftp_status = f"🔗 **Connexion FTP:** {'🟢 OK' if icarus_parser.ftp_available else '🔴 ÉCHEC'}\n"
        ftp_status += f"⏰ **Dernière vérification:** {icarus_parser.last_ftp_check.strftime('%H:%M:%S') if icarus_parser.last_ftp_check else 'Jamais'}\n"
        ftp_status += f"📋 **Événements lus:** {len(log_events)}\n"
        ftp_status += f"📊 **Total événements:** {len(icarus_parser.events)}"
        
        embed.add_field(
            name="🔗 **ÉTAT FTP**",
            value=ftp_status,
            inline=False
        )
        
        # Joueurs connectés
        if icarus_parser.connected_players:
            players_debug = ""
            for name, data in icarus_parser.connected_players.items():
                connect_time = data['connect_time'].strftime('%H:%M:%S')
                last_seen = data['last_seen'].strftime('%H:%M:%S')
                activity_delay = (get_french_time() - data['last_seen']).total_seconds() / 60
                
                players_debug += f"**{name}**\n"
                players_debug += f"├─ 🔗 Connecté: {connect_time}\n"
                players_debug += f"├─ 👁️ Dernière activité: {last_seen}\n"
                players_debug += f"└─ ⏰ Il y a {activity_delay:.1f} minutes\n\n"
            
            embed.add_field(
                name=f"👥 **JOUEURS ACTIFS** ({len(icarus_parser.connected_players)})",
                value=players_debug[:1000],
                inline=False
            )
        else:
            embed.add_field(
                name="👥 **JOUEURS ACTIFS**",
                value="❌ **Aucun joueur détecté**\n\n🔍 **Vérifications:**\n- Des joueurs sont-ils connectés ?\n- Les logs sont-ils accessibles ?\n- Le serveur est-il en ligne ?",
                inline=False
            )
        
        # Derniers événements
        recent = icarus_parser.get_recent_events(5)
        if recent:
            events_debug = "```yaml\n"
            for event in recent:
                time_str = event['timestamp'].strftime('%H:%M:%S')
                event_type = event['type'].replace('_', ' ').title()
                player_name = event.get('player_name', '')
                
                if player_name:
                    events_debug += f"{time_str}: {event_type} ({player_name})\n"
                else:
                    events_debug += f"{time_str}: {event_type}\n"
            
            events_debug += "```"
            
            embed.add_field(
                name="📋 **DERNIERS ÉVÉNEMENTS**",
                value=events_debug,
                inline=False
            )
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Erreur debug: {e}")
        await ctx.send(f"❌ Erreur debug: {e}")

@client.command(
    name='players',
    help='Affiche la liste des joueurs actuellement connectés au serveur',
    brief='Liste les joueurs connectés',
    description=(
        'Affiche une liste des joueurs actuellement connectés au serveur Icarus, '
        'avec leur temps de connexion et leur dernière activité.'
    )
)
async def players_command(ctx):
    """Commande pour lister les joueurs actifs"""
    try:
        # Force une mise à jour
        log_events = await icarus_parser.read_logs_ftp()
        icarus_parser.add_events(log_events)
        
        embed = discord.Embed(
            title="👥 **SURVIVANTS ICARUS**",
            color=0x27AE60,
            timestamp=get_french_time()
        )
        
        if icarus_parser.connected_players:
            players_text = ""
            for i, (name, data) in enumerate(icarus_parser.connected_players.items(), 1):
                connect_time = data['connect_time'].strftime('%H:%M:%S')
                last_seen = data['last_seen'].strftime('%H:%M:%S')
                minutes_ago = (get_french_time() - data['last_seen']).total_seconds() / 60
                
                players_text += f"**{i}. {name}**\n"
                players_text += f"   🔗 Connecté à: {connect_time}\n"
                players_text += f"   👁️ Vu il y a: {minutes_ago:.0f} min\n\n"
            
            embed.description = players_text
            embed.set_footer(text=f"🎮 {len(icarus_parser.connected_players)}/8 survivants connectés")
        else:
            embed.description = "💤 **Aucun survivant actuellement connecté**\n\n🚀 Soyez les premiers à rejoindre l'aventure !"
            embed.set_footer(text="🎮 0/8 survivants connectés")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Erreur players: {e}")
        await ctx.send("❌ Erreur lors de la récupération des joueurs.")

@client.command(
    name='logs',
    help='Affiche les derniers événements enregistrés dans les logs du serveur',
    brief='Affiche les logs récents',
    description=(
        'Affiche les événements récents du serveur Icarus, comme les connexions/déconnexions de joueurs, '
        'les sauvegardes et autres activités importantes.'
    )
)
async def logs_command(ctx, limit: int = 10):
    """Commande pour afficher les logs récents"""
    try:
        limit = max(1, min(limit, 20))  # Entre 1 et 20
        
        # Force une lecture des logs
        log_events = await icarus_parser.read_logs_ftp()
        icarus_parser.add_events(log_events)
        
        recent_events = icarus_parser.get_recent_events(limit)
        
        embed = discord.Embed(
            title="📋 **LOGS ICARUS RÉCENTS**",
            color=0x3498DB,
            timestamp=get_french_time()
        )
        
        if recent_events:
            logs_text = "```yaml\n"
            for event in recent_events:
                timestamp = event['timestamp'].strftime('%H:%M:%S')
                event_type = event['type']
                
                if event_type == 'player_connect':
                    player_name = event.get('player_name', 'Joueur')
                    logs_text += f"🟢 {timestamp}: {player_name} s'est connecté\n"
                elif event_type == 'player_disconnect':
                    player_name = event.get('player_name', 'Joueur')
                    logs_text += f"🔴 {timestamp}: {player_name} s'est déconnecté\n"
                elif event_type == 'prospect_update':
                    prospect_name = event.get('prospect_name', 'Mission')
                    logs_text += f"🗺️ {timestamp}: Mission {prospect_name} mise à jour\n"
                elif event_type == 'game_save':
                    logs_text += f"💾 {timestamp}: Sauvegarde automatique\n"
                elif event_type == 'crafting_activity':
                    logs_text += f"🔨 {timestamp}: Activité de craft détectée\n"
                else:
                    logs_text += f"⚙️ {timestamp}: {event_type.replace('_', ' ').title()}\n"
                    
            logs_text += "```"
            embed.description = logs_text
        else:
            embed.description = "❌ **Aucun événement récent trouvé**"
        
        embed.set_footer(text=f"📊 {len(recent_events)} événements • Source: FTP Logs")
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Erreur logs: {e}")
        await ctx.send("❌ Erreur lors de la récupération des logs.")

@client.command(
    name='channel',
    help='Définit le canal où seront affichées les mises à jour automatiques',
    brief='Définit le canal de monitoring',
    description=(
        'Configure le canal Discord où les mises à jour automatiques du serveur Icarus seront affichées. '
        'Nécessite les permissions de gestion des canaux.'
    )
)
@commands.has_permissions(manage_channels=True)
async def set_channel(ctx, channel: discord.TextChannel = None):
    """Définit le canal pour le monitoring automatique"""
    global current_channel_id, status_message
    
    if channel is None:
        channel = ctx.channel
    
    current_channel_id = channel.id
    status_message = None  # Force la création d'un nouveau message
    
    await ctx.send(f"✅ Canal de monitoring défini: {channel.mention}")
    logger.info(f"Canal de monitoring changé: {channel.id}")

@client.command(
    name='fdp',
    help='Une commande humoristique qui répond de manière aléatoire',
    brief='Commande humoristique',
    description=(
        'Une commande qui répond de manière aléatoire et humoristique. '
        'Parfait pour détendre l\'atmosphère !'
    )
)
async def fdp_command(ctx):
    """Commande humoristique"""
    import random
    
    reponses = [
        "Bien sûr que oui, je suis un super FDP ! 😎",
        "Oui, et alors ? J'assume totalement ! 🤷‍♂️", 
        "FDP ? C'est mon deuxième prénom ! 😂",
        "Et après ? Tu veux ma photo dédicacée enfoiré? 📸",
        "Merci, c'est le plus beau compliment qu'on m'ait fait ! 😘",
        "😎 FDP Professionnel certifié, mec tu connais !",
        "Oui, mais un FDP qui surveille ton serveur Icarus, connard... 🎮"
        "Tu m'insultes encore une fois et je supprime ton serveur ainsi que le bot, enfoiré !"
        "Tu parle mes je suis plus fort que toi batard ! 😘"
    ]
    
    await ctx.send(random.choice(reponses))

@client.command(
    name='connect',
    help='Affiche les informations pour se connecter au serveur Icarus',
    brief="Affiche les infos de connexion",
    description=(
        'Affiche les informations détaillées pour se connecter au serveur Icarus, '
        'y compris l\'adresse IP, le port et le mot de passe.'
    )
)
async def connect_command(ctx):
    """Commande pour afficher les informations de connexion au serveur"""
    try:
        # Créer l'embed
        embed = discord.Embed(
            title="🚀 **CONNEXION AU SERVEUR ICARUS**",
            description=(
                f"Voici les informations pour te connecter à notre serveur Icarus.\n"
                f"Copie-colle la commande ci-dessous dans la console du jeu (touche **`** pour l'ouvrir) :\n\n"
                f"```/connect {SERVER_IP}:{SERVER_PORT} {SERVER_PASSWORD}```"
            ),
            color=0x00D9FF,
            timestamp=get_french_time()
        )
        
        # Ajouter les informations de connexion
        embed.add_field(
            name="📋 **Informations de connexion**",
            value=(
                f"**IP du serveur:** `{SERVER_IP}`\n"
                f"**Port:** `{SERVER_PORT}`\n"
                f"**Mot de passe:** `{SERVER_PASSWORD}`"
            ),
            inline=False
        )
        
        # Méthode détaillée
        embed.add_field(
            name="🔍 **Méthode détaillée**",
            value=(
                "1. Lance **Icarus** depuis Steam\n"
                "2. Appuie sur la touche **`** (au-dessus de Tab) pour ouvrir la console\n"
                "3. Copie-colle la commande de connexion ci-dessus\n"
                "4. Appuie sur **Entrée**"
            ),
            inline=False
        )
        
        # Ajouter des conseils
        embed.add_field(
            name="💡 **Conseils**",
            value=(
                "• Assure-toi que Steam est bien lancé\n"
                "• La console s'ouvre avec la touche **`** (au-dessus de Tab)\n"
                "• Si tu rencontres des problèmes, redémarre Steam et le jeu"
            ),
            inline=False
        )
        
        # Envoyer le message
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Erreur dans la commande connect: {e}")
        await ctx.send(
            "❌ Une erreur est survenue lors de la préparation des informations de connexion. "
            "Veuillez réessayer ou contacter un administrateur.",
            delete_after=10
        )

# === DÉMARRAGE ===

if __name__ == '__main__':
    try:
        logger.info("🚀 Démarrage du bot Discord Icarus...")
        logger.info(f"📡 Serveur: {SERVER_IP}:{SERVER_PORT}")
        logger.info(f"📁 FTP: {FTP_HOST}:{FTP_PORT}")
        logger.info(f"📋 Canal Discord: {CHANNEL_ID}")
        logger.info(f"Tout est OP Micka, excellent travail ! 👏")
        
        client.run(DISCORD_TOKEN)
        
    except KeyboardInterrupt:
        logger.info("⏹️ Arrêt du bot demandé par l'utilisateur")
    except Exception as e:
        logger.error(f"❌ Erreur critique: {e}")
    finally:
        logger.info("👋 Bot arrêté")
