from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
from collections import Counter

app = Flask(__name__)
app.config['SECRET_KEY'] = 'votre_clé_secrète_ici'
socketio = SocketIO(app, ping_timeout=5, ping_interval=2)

games = {}

class Carte:
    VALEURS = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
    
    def __init__(self, valeur, couleur):
        self.valeur = valeur
        self.couleur = couleur
        
    def to_dict(self):
        return {'valeur': self.valeur, 'couleur': self.couleur}
    
    def get_valeur_numerique(self):
        return self.VALEURS[self.valeur]

class GestionTour:
    def __init__(self, joueurs):
        self.joueurs = joueurs
        self.ordre_joueurs = list(joueurs.keys())
        self.index_actuel = 0
        self.dernier_miseur = None
        self.tour_termine = False
        self.premier_joueur_tour = None  # Pour suivre le premier joueur du tour
        
    def joueur_actuel(self):
        """Retourne le joueur actuel"""
        if not self.ordre_joueurs:
            return None
        return self.ordre_joueurs[self.index_actuel]
    
    def passer_au_suivant(self):
        """Passe au joueur suivant qui est encore en jeu"""
        if not self.ordre_joueurs:
            return None
            
        # Sauvegarder le premier joueur du tour si pas encore défini
        if self.premier_joueur_tour is None:
            self.premier_joueur_tour = self.joueur_actuel()
            
        self.index_actuel = (self.index_actuel + 1) % len(self.ordre_joueurs)
        
        # Chercher le prochain joueur actif
        tentatives = 0
        while not self.joueurs[self.ordre_joueurs[self.index_actuel]]['en_jeu']:
            self.index_actuel = (self.index_actuel + 1) % len(self.ordre_joueurs)
            tentatives += 1
            if tentatives >= len(self.ordre_joueurs):
                return None
            
        return self.ordre_joueurs[self.index_actuel]
    
    def verifier_tour_complet(self, mises_tour, mise_actuelle):
        """Vérifie si le tour est complet (tous les joueurs ont misé le même montant)"""
        joueurs_actifs = [j for j in self.ordre_joueurs if self.joueurs[j]['en_jeu']]
        
        # S'il ne reste qu'un joueur
        if len(joueurs_actifs) <= 1:
            self.tour_termine = True
            return True
            
        # Vérifier si tous les joueurs actifs ont misé le même montant
        for joueur in joueurs_actifs:
            if mises_tour[joueur] != mise_actuelle:
                return False
        
        # Si tous les joueurs ont misé le même montant, on vérifie si on a fait un tour complet
        if self.premier_joueur_tour is None:
            # Premier tour du betting round
            self.premier_joueur_tour = self.joueur_actuel()
            return False
        else:
            # On vérifie si on est revenu au premier joueur du tour
            return self.joueur_actuel() == self.premier_joueur_tour
            
        return False

class Partie:
    def __init__(self, room_id):
        self.room_id = room_id
        self.joueurs = {}
        self.deck = []
        self.pot = 0
        self.cartes_communes = []
        self.mise_actuelle = 0
        self.phase = 'attente'  # attente, preflop, flop, turn, river
        self.petite_blind = 10
        self.grande_blind = 20
        self.dealer_index = 0
        self.mises_tour = {}
        self.gestion_tour = None
        self.initialiser_deck()

    def initialiser_deck(self):
        valeurs = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        couleurs = ['♠', '♥', '♦', '♣']
        self.deck = [Carte(v, c) for v in valeurs for c in couleurs]
        random.shuffle(self.deck)

    def distribuer_cartes(self):
        self.phase = 'preflop'
        self.pot = 0
        self.cartes_communes = []
        self.mise_actuelle = self.grande_blind
        
        # Distribution des cartes
        joueurs_liste = list(self.joueurs.keys())
        for username in joueurs_liste:
            self.joueurs[username]['cartes'] = [self.deck.pop().to_dict() for _ in range(2)]
            self.joueurs[username]['en_jeu'] = True
            self.mises_tour[username] = 0
            self.evaluer_et_envoyer_combinaison(username)
        
        # Mise des blinds
        petite_blind_index = (self.dealer_index + 1) % len(joueurs_liste)
        grande_blind_index = (self.dealer_index + 2) % len(joueurs_liste)
        
        # Petite blind
        petite_blind_joueur = joueurs_liste[petite_blind_index]
        self.joueurs[petite_blind_joueur]['jetons'] -= self.petite_blind
        self.pot += self.petite_blind
        self.mises_tour[petite_blind_joueur] = self.petite_blind
        
        # Grande blind
        grande_blind_joueur = joueurs_liste[grande_blind_index]
        self.joueurs[grande_blind_joueur]['jetons'] -= self.grande_blind
        self.pot += self.grande_blind
        self.mises_tour[grande_blind_joueur] = self.grande_blind
        
        # Initialiser la gestion des tours
        self.gestion_tour = GestionTour(self.joueurs)
        # Positionner sur le joueur après la grande blind
        self.gestion_tour.index_actuel = (grande_blind_index + 1) % len(joueurs_liste)
        self.gestion_tour.dernier_miseur = grande_blind_joueur
        
        # Envoi des cartes aux joueurs
        for username in joueurs_liste:
            socketio.emit('recevoir_cartes', {
                'cartes': self.joueurs[username]['cartes']
            }, room=username)
        
        self.update_game_state()

    def next_phase(self):
        joueurs_liste = [u for u, j in self.joueurs.items() if j['en_jeu']]
        if len(joueurs_liste) <= 1:
            self.fin_manche(joueurs_liste[0] if joueurs_liste else None)
            return

        if self.phase == 'preflop':
            self.phase = 'flop'
            self.cartes_communes.extend([self.deck.pop().to_dict() for _ in range(3)])
            socketio.emit('notification', {
                'message': 'Distribution du Flop',
                'type': 'phase'
            }, room=self.room_id)
        elif self.phase == 'flop':
            self.phase = 'turn'
            self.cartes_communes.append(self.deck.pop().to_dict())
            socketio.emit('notification', {
                'message': 'Distribution du Turn',
                'type': 'phase'
            }, room=self.room_id)
        elif self.phase == 'turn':
            self.phase = 'river'
            self.cartes_communes.append(self.deck.pop().to_dict())
            socketio.emit('notification', {
                'message': 'Distribution de la River',
                'type': 'phase'
            }, room=self.room_id)
        elif self.phase == 'river':
            if all(not j['en_jeu'] or self.mises_tour[u] == self.mise_actuelle 
                  for u, j in self.joueurs.items()):
                self.evaluer_mains()
            return
        
        # Réinitialiser les mises pour la nouvelle phase
        self.mise_actuelle = 0
        self.mises_tour = {username: 0 for username in self.joueurs}
        
        # Réinitialiser la gestion des tours pour la nouvelle phase
        self.gestion_tour = GestionTour(self.joueurs)
        # Commencer par le premier joueur après le dealer qui est encore en jeu
        self.gestion_tour.index_actuel = self.dealer_index
        joueur_suivant = self.gestion_tour.passer_au_suivant()
        
        if not joueur_suivant:
            self.fin_manche(None)
            return
        
        # Évaluer les nouvelles combinaisons pour tous les joueurs
        for username in self.joueurs:
            self.evaluer_et_envoyer_combinaison(username)
        
        self.update_game_state()

    def get_nom_combinaison(self, valeur):
        combinaisons = {
            10: 'Quinte Flush Royale',
            9: 'Quinte Flush',
            8: 'Carré',
            7: 'Full House',
            6: 'Couleur',
            5: 'Quinte',
            4: 'Brelan',
            3: 'Deux Paires',
            2: 'Paire',
            1: 'Carte Haute'
        }
        return combinaisons.get(valeur, 'Inconnu')

    def fin_manche(self, gagnant):
        if gagnant:
            # Calculer le gain total (pot + mises du tour en cours)
            gain_total = self.pot
            for mise in self.mises_tour.values():
                gain_total += mise
                
            self.joueurs[gagnant]['jetons'] += gain_total
            socketio.emit('fin_manche', {
                'gagnant': gagnant,
                'gain': gain_total,
                'mains': {u: {'valeur': v, 'cartes': self.joueurs[u]['cartes']} for u, v in self.joueurs.items() if u == gagnant or self.joueurs[u]['en_jeu']}
            }, room=self.room_id)
        
        # Préparation de la prochaine manche
        self.dealer_index = (self.dealer_index + 1) % len(self.joueurs)
        self.phase = 'attente'
        self.pot = 0
        self.cartes_communes = []
        self.mise_actuelle = 0
        self.mises_tour = {}
        self.initialiser_deck()
        
        # Démarrer automatiquement la prochaine manche après un court délai
        socketio.emit('nouvelle_manche', {'delai': 3}, room=self.room_id)  # 3 secondes de délai
        
        self.update_game_state()

    def evaluer_mains(self):
        mains = {}
        mains_valeurs = {}  # Pour stocker les valeurs numériques des mains
        for username, joueur in self.joueurs.items():
            if not joueur['en_jeu']:
                continue
            
            cartes_joueur = [Carte(**c) for c in joueur['cartes']]
            toutes_cartes = cartes_joueur + [Carte(**c) for c in self.cartes_communes]
            valeur_main, cartes_combinaison = self.evaluer_main(toutes_cartes)
            mains[username] = {
                'valeur': valeur_main,
                'cartes': [c.to_dict() for c in cartes_combinaison],
                'main_complete': [c.to_dict() for c in cartes_joueur]
            }
            mains_valeurs[username] = valeur_main
        
        if mains:
            # Trouver le gagnant basé sur la valeur de la main
            gagnant = max(mains_valeurs.items(), key=lambda x: x[1])[0]
            
            # Calculer le gain total (pot + mises du tour en cours)
            gain_total = self.pot
            for mise in self.mises_tour.values():
                gain_total += mise
                
            self.joueurs[gagnant]['jetons'] += gain_total
            
            # Préparer les données pour l'émission
            resultats_mains = {
                username: {
                    'valeur': info['valeur'],
                    'combinaison': self.get_nom_combinaison(info['valeur']),
                    'cartes': info['main_complete'],
                    'cartes_gagnantes': info['cartes']
                }
                for username, info in mains.items()
            }
            
            socketio.emit('fin_manche', {
                'gagnant': gagnant,
                'gain': gain_total,
                'mains': resultats_mains
            }, room=self.room_id)
            
            # Préparation de la prochaine manche
            self.dealer_index = (self.dealer_index + 1) % len(self.joueurs)
            self.phase = 'attente'
            self.pot = 0
            self.cartes_communes = []
            self.mise_actuelle = 0
            self.mises_tour = {}
            self.initialiser_deck()
            
            # Démarrer automatiquement la prochaine manche après un court délai
            socketio.emit('nouvelle_manche', {'delai': 3}, room=self.room_id)
            
            self.update_game_state()

    def evaluer_main(self, cartes):
        if not cartes:  # Si la liste des cartes est vide
            return 0, []  # Retourner une valeur par défaut
            
        valeurs = [c.get_valeur_numerique() for c in cartes]
        couleurs = [c.couleur for c in cartes]
        cartes_combinaison = []  # Pour stocker les cartes qui forment la combinaison
        
        # Vérification de la quinte flush royale et quinte flush
        for couleur in set(couleurs):
            cartes_couleur = [c for c in cartes if c.couleur == couleur]
            if len(cartes_couleur) >= 5:
                valeurs_couleur = sorted([c.get_valeur_numerique() for c in cartes_couleur], reverse=True)
                for i in range(len(valeurs_couleur) - 4):
                    if valeurs_couleur[i:i+5] == [14, 13, 12, 11, 10]:
                        cartes_combinaison = [c for c in cartes_couleur if c.get_valeur_numerique() in [10, 11, 12, 13, 14]]
                        return 10, cartes_combinaison  # Quinte flush royale
                    if valeurs_couleur[i] - valeurs_couleur[i+4] == 4:
                        val_debut = valeurs_couleur[i]
                        cartes_combinaison = [c for c in cartes_couleur if val_debut >= c.get_valeur_numerique() >= val_debut-4]
                        return 9, cartes_combinaison  # Quinte flush
        
        # Carré
        compteur = Counter(valeurs)
        for valeur, count in compteur.items():
            if count == 4:
                cartes_combinaison = [c for c in cartes if c.get_valeur_numerique() == valeur]
                return 8, cartes_combinaison
        
        # Full house
        if 3 in compteur.values() and 2 in compteur.values():
            brelan_valeur = [v for v, c in compteur.items() if c == 3][0]
            paire_valeur = [v for v, c in compteur.items() if c == 2][0]
            cartes_combinaison = [c for c in cartes if c.get_valeur_numerique() in [brelan_valeur, paire_valeur]]
            return 7, cartes_combinaison
        
        # Couleur
        for couleur in set(couleurs):
            cartes_couleur = [c for c in cartes if c.couleur == couleur]
            if len(cartes_couleur) >= 5:
                cartes_combinaison = sorted(cartes_couleur, key=lambda c: c.get_valeur_numerique(), reverse=True)[:5]
                return 6, cartes_combinaison
        
        # Quinte
        valeurs_uniques = sorted(set(valeurs), reverse=True)
        for i in range(len(valeurs_uniques) - 4):
            if valeurs_uniques[i] - valeurs_uniques[i+4] == 4:
                val_debut = valeurs_uniques[i]
                cartes_combinaison = [c for c in cartes if val_debut >= c.get_valeur_numerique() >= val_debut-4]
                return 5, cartes_combinaison
        
        # Brelan
        for valeur, count in compteur.items():
            if count == 3:
                cartes_combinaison = [c for c in cartes if c.get_valeur_numerique() == valeur]
                return 4, cartes_combinaison
        
        # Deux paires
        if list(compteur.values()).count(2) >= 2:
            paires = [v for v, c in compteur.items() if c == 2]
            cartes_combinaison = [c for c in cartes if c.get_valeur_numerique() in paires]
            return 3, cartes_combinaison
        
        # Paire
        for valeur, count in compteur.items():
            if count == 2:
                cartes_combinaison = [c for c in cartes if c.get_valeur_numerique() == valeur]
                return 2, cartes_combinaison
        
        # Carte haute
        carte_haute = max(cartes, key=lambda c: c.get_valeur_numerique())
        return 1, [carte_haute]

    def update_game_state(self):
        # S'assurer que mises_tour contient tous les joueurs actuels
        for username in self.joueurs:
            if username not in self.mises_tour:
                self.mises_tour[username] = 0

        socketio.emit('update_game_state', {
            'pot': self.pot,
            'tour_actuel': self.gestion_tour.joueur_actuel() if self.gestion_tour else None,
            'mise_actuelle': self.mise_actuelle,
            'phase': self.phase,
            'cartes_communes': self.cartes_communes,
            'joueurs': {
                u: {
                    'jetons': j['jetons'],
                    'en_jeu': j['en_jeu'],
                    'mise_tour': self.mises_tour.get(u, 0)  # Utiliser get() avec valeur par défaut
                } for u, j in self.joueurs.items()
            }
        }, room=self.room_id)

    def evaluer_et_envoyer_combinaison(self, username):
        joueur = self.joueurs[username]
        if not joueur['en_jeu']:
            return
            
        cartes_joueur = [Carte(**c) for c in joueur['cartes']]
        toutes_cartes = cartes_joueur + [Carte(**c) for c in self.cartes_communes]
        
        if not toutes_cartes:  # S'il n'y a pas de cartes à évaluer
            socketio.emit('combinaison_actuelle', {
                'combinaison': 'En attente',
                'cartes_gagnantes': []
            }, room=username)
            return
            
        valeur_main, cartes_combinaison = self.evaluer_main(toutes_cartes)
        combinaison = self.get_nom_combinaison(valeur_main) if valeur_main > 0 else 'En attente'
        
        # Convertir les cartes de la combinaison en format lisible
        cartes_gagnantes = [{'valeur': c.valeur, 'couleur': c.couleur} for c in cartes_combinaison]
        
        socketio.emit('combinaison_actuelle', {
            'combinaison': combinaison,
            'cartes_gagnantes': cartes_gagnantes
        }, room=username)

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('creer_table')
def handle_create_table(data):
    username = data['username']
    room = data['room']
    
    if room not in games:
        games[room] = Partie(room)
    
    join_room(room)
    join_room(username)
    
    games[room].joueurs[username] = {
        'cartes': [],
        'jetons': 1000,
        'en_jeu': True
    }
    
    # Émettre l'état actuel à tous les joueurs
    emit('table_creee', {
        'username': username,
        'room': room
    })
    
    # Mettre à jour la liste des tables pour tous les clients
    emit('update_tables', {
        'tables': {
            room_id: {
                'joueurs': {
                    u: {'jetons': j['jetons']} 
                    for u, j in game.joueurs.items()
                }
            } for room_id, game in games.items()
        }
    }, broadcast=True)
    
    # Mettre à jour l'état du jeu
    games[room].update_game_state()

@socketio.on('rejoindre_partie')
def on_join(data):
    username = data['username']
    room = data['room']
    
    if room not in games:
        emit('erreur', {'message': 'Cette table n\'existe plus'})
        return
    
    if len(games[room].joueurs) >= 6:
        emit('erreur', {'message': 'La table est pleine'})
        return
    
    join_room(room)
    join_room(username)
    
    games[room].joueurs[username] = {
        'cartes': [],
        'jetons': 1000,
        'en_jeu': True
    }
    
    # Émettre l'état actuel à tous les joueurs
    emit('joueur_rejoint', {
        'username': username,
        'joueurs': {
            u: {'jetons': j['jetons']} 
            for u, j in games[room].joueurs.items()
        }
    }, room=room)
    
    # Mettre à jour l'état du jeu pour tous les joueurs
    games[room].update_game_state()
    
    # Mettre à jour la liste des tables pour tous les clients
    emit('update_tables', {
        'tables': {
            room_id: {
                'joueurs': {
                    u: {'jetons': j['jetons']} 
                    for u, j in game.joueurs.items()
                }
            } for room_id, game in games.items()
        }
    }, broadcast=True)
    
    # Si on a 2 joueurs ou plus, activer le bouton démarrer pour tous
    if len(games[room].joueurs) >= 2:
        emit('activer_demarrage', room=room)
    
    # Cacher le lobby et afficher le jeu pour le joueur qui rejoint
    emit('table_creee', {
        'username': username,
        'room': room
    })

@socketio.on('demarrer_partie')
def start_game(data):
    room = data['room']
    if room in games:
        game = games[room]
        if len(game.joueurs) >= 2:
            game.distribuer_cartes()
            emit('partie_demarree', room=room)
            game.update_game_state()  # S'assurer que l'état est mis à jour après le démarrage
        else:
            emit('erreur', {'message': 'Il faut au moins 2 joueurs pour démarrer la partie'}, room=room)

@socketio.on('action_joueur')
def handle_action(data):
    room = data['room']
    username = data['username']
    action = data['action']
    montant = data.get('montant', 0)
    
    if room not in games:
        return
        
    game = games[room]
    if username != game.gestion_tour.joueur_actuel() or not game.joueurs[username]['en_jeu']:
        emit('erreur', {'message': 'Ce n\'est pas votre tour'}, room=username)
        return
    
    mise_necessaire = game.mise_actuelle - game.mises_tour[username]
    
    if action == 'mise':
        if montant < mise_necessaire:
            emit('erreur', {'message': f'Mise insuffisante. Minimum requis: {mise_necessaire}€'}, room=username)
            return
        if montant > game.joueurs[username]['jetons']:
            emit('erreur', {'message': f'Vous n\'avez pas assez de jetons. Vous avez {game.joueurs[username]["jetons"]}€'}, room=username)
            return
        game.pot += montant
        game.joueurs[username]['jetons'] -= montant
        game.mise_actuelle = game.mises_tour[username] + montant
        game.mises_tour[username] += montant
        game.gestion_tour.dernier_miseur = username
        # Notification de mise
        socketio.emit('notification', {
            'message': f'{username} a misé {montant}€',
            'type': 'action'
        }, room=room)
    elif action == 'suivre':
        if mise_necessaire > game.joueurs[username]['jetons']:
            emit('erreur', {'message': 'Vous n\'avez pas assez de jetons'}, room=username)
            return
        game.pot += mise_necessaire
        game.joueurs[username]['jetons'] -= mise_necessaire
        game.mises_tour[username] += mise_necessaire
        # Notification de suivi
        socketio.emit('notification', {
            'message': f'{username} a suivi ({mise_necessaire}€)',
            'type': 'action'
        }, room=room)
    elif action == 'coucher':
        game.joueurs[username]['en_jeu'] = False
        # Notification d'abandon
        socketio.emit('notification', {
            'message': f'{username} s\'est couché',
            'type': 'action'
        }, room=room)
        # Si tous les autres joueurs se sont couchés sauf un
        joueurs_actifs = [j for j in game.joueurs if game.joueurs[j]['en_jeu']]
        if len(joueurs_actifs) == 1:
            game.fin_manche(joueurs_actifs[0])
            return
    
    # Vérifier si le tour est terminé
    if game.gestion_tour.verifier_tour_complet(game.mises_tour, game.mise_actuelle):
        if game.phase == 'river':
            if all(not j['en_jeu'] or game.mises_tour[u] == game.mise_actuelle 
                  for u, j in game.joueurs.items()):
                game.evaluer_mains()
            return
        game.next_phase()
    else:
        # Passer au joueur suivant
        game.gestion_tour.passer_au_suivant()
        game.update_game_state()

@socketio.on('quitter_partie')
def on_leave(data):
    username = data['username']
    room = data['room']
    
    if room in games:
        game = games[room]
        if username in game.joueurs:
            # Nettoyer les mises du joueur
            if username in game.mises_tour:
                del game.mises_tour[username]
            
            # Supprimer le joueur
            del game.joueurs[username]
            
            # Mettre à jour la gestion du tour si nécessaire
            if game.gestion_tour:
                game.gestion_tour.ordre_joueurs = list(game.joueurs.keys())
                if len(game.joueurs) < 2:
                    # S'il ne reste qu'un joueur ou moins, terminer la manche
                    if len(game.joueurs) == 1:
                        dernier_joueur = next(iter(game.joueurs.keys()))
                        game.fin_manche(dernier_joueur)
                    else:
                        game.phase = 'attente'
            
            # Quitter les rooms
            leave_room(room)
            leave_room(username)
            
            # Notifier les autres joueurs
            emit('joueur_parti', {'username': username}, room=room)
            
            # Mettre à jour l'état du jeu
            game.update_game_state()
            
            # Si la partie est vide, la supprimer
            if not game.joueurs:
                del games[room]

@socketio.on('demander_combinaison')
def handle_demander_combinaison(data):
    room = data['room']
    username = data['username']
    
    if room in games:
        games[room].evaluer_et_envoyer_combinaison(username)

@socketio.on('connect')
def handle_connect(auth):
    # Quand un client se connecte, on enregistre son sid
    session_id = request.sid
    emit('connected', {'sid': session_id})
    
    # Envoyer la liste des tables disponibles
    emit('update_tables', {
        'tables': {
            room_id: {
                'joueurs': {
                    u: {'jetons': j['jetons']} 
                    for u, j in game.joueurs.items()
                }
            } for room_id, game in games.items()
        }
    })

@socketio.on('disconnect')
def handle_disconnect():
    # Trouver la partie et le joueur qui s'est déconnecté
    session_id = request.sid
    for room_id, game in list(games.items()):
        for username in list(game.joueurs.keys()):
            if username in game.joueurs:
                # Simuler un "quitter_partie" pour ce joueur
                on_leave({'username': username, 'room': room_id})
                emit('joueur_deconnecte', {'username': username}, room=room_id)
                break

@socketio.on('ping_client')
def handle_ping():
    emit('pong_server')

if __name__ == '__main__':
    socketio.run(app, debug=True) 