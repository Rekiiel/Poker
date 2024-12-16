from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
from collections import Counter
import time
import os

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
        if not self.ordre_joueurs or self.index_actuel >= len(self.ordre_joueurs):
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
        while tentatives < len(self.ordre_joueurs):
            if self.ordre_joueurs[self.index_actuel] in self.joueurs and self.joueurs[self.ordre_joueurs[self.index_actuel]]['en_jeu']:
                return self.ordre_joueurs[self.index_actuel]
            self.index_actuel = (self.index_actuel + 1) % len(self.ordre_joueurs)
            tentatives += 1
            
        return None
    
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
        self.deconnexions_temporaires = {}  # Pour suivre les déconnexions temporaires
        self.joueurs_prets = set()  # Pour suivre les joueurs prêts
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
        self.joueurs_prets.clear()  # Réinitialiser les joueurs prêts
        
        # Vérifier et éliminer les joueurs sans jetons
        joueurs_elimines = []
        for username, joueur in self.joueurs.items():
            if joueur['jetons'] <= 0:
                joueur['en_jeu'] = False
                joueurs_elimines.append(username)
                socketio.emit('notification', {
                    'message': f'{username} n\'a plus de jetons et ne peut pas participer à cette manche',
                    'type': 'error'
                }, room=self.room_id)
        
        # S'il ne reste qu'un joueur avec des jetons, terminer la partie
        joueurs_avec_jetons = [u for u, j in self.joueurs.items() if j['jetons'] > 0]
        if len(joueurs_avec_jetons) <= 1:
            if joueurs_avec_jetons:
                socketio.emit('notification', {
                    'message': f'{joueurs_avec_jetons[0]} a gagné la partie !',
                    'type': 'success'
                }, room=self.room_id)
            return
        
        # Distribution des cartes
        joueurs_liste = list(self.joueurs.keys())
        for username in joueurs_liste:
            if self.joueurs[username]['jetons'] > 0:  # Ne distribuer qu'aux joueurs avec des jetons
                self.joueurs[username]['cartes'] = [self.deck.pop().to_dict() for _ in range(2)]
                self.joueurs[username]['en_jeu'] = True  # S'assurer que tous les joueurs sont en jeu
                self.mises_tour[username] = 0
                self.evaluer_et_envoyer_combinaison(username)
        
        # Mise des blinds
        petite_blind_index = (self.dealer_index + 1) % len(joueurs_liste)
        grande_blind_index = (self.dealer_index + 2) % len(joueurs_liste)
        
        # S'assurer que les joueurs des blinds ont assez de jetons
        while self.joueurs[joueurs_liste[petite_blind_index]]['jetons'] <= 0:
            petite_blind_index = (petite_blind_index + 1) % len(joueurs_liste)
        while self.joueurs[joueurs_liste[grande_blind_index]]['jetons'] <= 0:
            grande_blind_index = (grande_blind_index + 1) % len(joueurs_liste)
        
        # Petite blind
        petite_blind_joueur = joueurs_liste[petite_blind_index]
        montant_petite_blind = min(self.petite_blind, self.joueurs[petite_blind_joueur]['jetons'])
        self.joueurs[petite_blind_joueur]['jetons'] -= montant_petite_blind
        self.pot += montant_petite_blind
        self.mises_tour[petite_blind_joueur] = montant_petite_blind
        
        # Grande blind
        grande_blind_joueur = joueurs_liste[grande_blind_index]
        montant_grande_blind = min(self.grande_blind, self.joueurs[grande_blind_joueur]['jetons'])
        self.joueurs[grande_blind_joueur]['jetons'] -= montant_grande_blind
        self.pot += montant_grande_blind
        self.mises_tour[grande_blind_joueur] = montant_grande_blind
        self.mise_actuelle = montant_grande_blind
        
        # Initialiser la gestion des tours
        self.gestion_tour = GestionTour(self.joueurs)
        # Positionner sur le joueur après la grande blind
        self.gestion_tour.index_actuel = (grande_blind_index + 1) % len(joueurs_liste)
        self.gestion_tour.dernier_miseur = grande_blind_joueur
        
        # Envoi des cartes aux joueurs
        for username in joueurs_liste:
            if self.joueurs[username]['jetons'] > 0:  # Ne distribuer qu'aux joueurs avec des jetons
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
        # Commencer par le premier joueur à gauche du dealer qui est encore en jeu
        self.gestion_tour.index_actuel = (self.dealer_index + 1) % len(self.joueurs)
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
            # Le gain total est simplement le pot actuel, pas besoin d'ajouter les mises du tour
            # car elles sont déjà incluses dans le pot quand elles sont faites
            gain_total = self.pot
            
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
        
        # V��rification de la quinte flush royale et quinte flush
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

        # Déterminer les positions des blinds
        joueurs_liste = list(self.joueurs.keys())
        if len(joueurs_liste) >= 2:
            dealer_index = self.dealer_index % len(joueurs_liste)
            big_blind_index = (self.dealer_index + 2) % len(joueurs_liste)
            dealer = joueurs_liste[dealer_index]
            big_blind = joueurs_liste[big_blind_index]
        else:
            dealer = None
            big_blind = None

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
                    'mise_tour': self.mises_tour.get(u, 0),
                    'is_dealer': u == dealer,
                    'is_big_blind': u == big_blind
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

    def gerer_deconnexion_temporaire(self, username):
        # Supprimer immédiatement le joueur
        if username in self.joueurs:
            # Si c'était le tour du joueur déconnecté
            if self.gestion_tour and self.gestion_tour.joueur_actuel() == username:
                # Coucher automatiquement le joueur
                self.joueurs[username]['en_jeu'] = False
                socketio.emit('notification', {
                    'message': f'{username} a été déconnecté et ses cartes ont été couchées',
                    'type': 'error'
                }, room=self.room_id)
                
                # Vérifier s'il ne reste qu'un joueur
                joueurs_actifs = [j for j in self.joueurs if self.joueurs[j]['en_jeu']]
                if len(joueurs_actifs) == 1:
                    self.fin_manche(joueurs_actifs[0])
                else:
                    # Passer au joueur suivant
                    self.gestion_tour.passer_au_suivant()
                    self.update_game_state()
            
            # Supprimer le joueur et continuer la partie
            self.retirer_joueur(username)
            socketio.emit('notification', {
                'message': f'{username} a quitté la table',
                'type': 'error'
            }, room=self.room_id)
            
            # Si la table est vide après la déconnexion, la supprimer
            if not self.joueurs:
                if self.room_id in games:
                    del games[self.room_id]
                    socketio.emit('update_tables', {
                        'tables': {
                            room_id: {
                                'joueurs': {
                                    u: {'jetons': j['jetons']} 
                                    for u, j in game.joueurs.items()
                                }
                            } for room_id, game in games.items()
                        }
                    }, broadcast=True)

    def retirer_joueur(self, username):
        if username in self.joueurs:
            # Nettoyer les mises du joueur
            if username in self.mises_tour:
                self.pot += self.mises_tour[username]  # Ajouter la mise au pot
                del self.mises_tour[username]
            
            # Retirer le joueur des joueurs prêts
            if username in self.joueurs_prets:
                self.joueurs_prets.remove(username)
            
            # Supprimer le joueur
            del self.joueurs[username]
            if username in self.deconnexions_temporaires:
                del self.deconnexions_temporaires[username]
            
            # Mettre à jour la gestion du tour si nécessaire
            if self.gestion_tour:
                self.gestion_tour.ordre_joueurs = list(self.joueurs.keys())
                if len(self.joueurs) < 2:
                    if len(self.joueurs) == 1:
                        dernier_joueur = next(iter(self.joueurs.keys()))
                        # Réinitialiser l'index avant de finir la manche
                        self.gestion_tour.index_actuel = 0
                        self.fin_manche(dernier_joueur)
                    else:
                        self.phase = 'attente'
                else:
                    # Si c'était le tour du joueur déconnecté
                    if self.gestion_tour.joueur_actuel() == username:
                        # Réinitialiser l'index si nécessaire
                        if self.gestion_tour.index_actuel >= len(self.joueurs):
                            self.gestion_tour.index_actuel = 0
                        self.gestion_tour.passer_au_suivant()
                    self.update_game_state()

    def verifier_tous_prets(self):
        """Vérifie si tous les joueurs sont prêts"""
        return len(self.joueurs_prets) >= 2 and len(self.joueurs_prets) == len(self.joueurs)

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
    
    if len(games[room].joueurs) >= 7:
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
    
    # Vérifier si le joueur a encore des jetons
    if game.joueurs[username]['jetons'] <= 0:
        game.joueurs[username]['en_jeu'] = False
        socketio.emit('notification', {
            'message': f'{username} n\'a plus de jetons et est éliminé de la partie',
            'type': 'error'
        }, room=room)
        # Vérifier s'il ne reste qu'un joueur
        joueurs_actifs = [j for j in game.joueurs if game.joueurs[j]['en_jeu']]
        if len(joueurs_actifs) == 1:
            game.fin_manche(joueurs_actifs[0])
            return
        game.gestion_tour.passer_au_suivant()
        game.update_game_state()
        return
    
    mise_necessaire = game.mise_actuelle - game.mises_tour[username]
    
    if action == 'check':
        if mise_necessaire > 0:
            emit('erreur', {'message': 'Vous ne pouvez pas checker, vous devez suivre ou vous coucher'}, room=username)
            return
        # Notification de check
        socketio.emit('notification', {
            'message': f'{username} checke',
            'type': 'action'
        }, room=room)
    elif action == 'mise':
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
            # Coucher le joueur s'il est encore en jeu
            if game.phase != 'attente' and game.joueurs[username]['en_jeu']:
                game.joueurs[username]['en_jeu'] = False
                if game.gestion_tour and game.gestion_tour.joueur_actuel() == username:
                    game.gestion_tour.passer_au_suivant()
            
            # Retirer le joueur de la partie
            game.retirer_joueur(username)
            
            # Quitter les rooms
            leave_room(room)
            leave_room(username)
            
            # Notifier les autres joueurs
            emit('joueur_parti', {'username': username}, room=room)
            
            # Si la partie est vide, la supprimer
            if not game.joueurs:
                del games[room]
            
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
            
            # Mettre à jour l'état du jeu pour les joueurs restants
            if game.joueurs:
                game.update_game_state()

@socketio.on('demander_combinaison')
def handle_demander_combinaison(data):
    room = data['room']
    username = data['username']
    
    if room in games:
        games[room].evaluer_et_envoyer_combinaison(username)

@socketio.on('connect')
def handle_connect(auth):
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
    
    # Vérifier si le joueur était temporairement déconnecté
    for game in games.values():
        for username in game.deconnexions_temporaires:
            if username in game.joueurs:
                del game.deconnexions_temporaires[username]
                socketio.emit('notification', {
                    'message': f'{username} s\'est reconnecté',
                    'type': 'success'
                }, room=game.room_id)
                break

@socketio.on('disconnect')
def handle_disconnect():
    session_id = request.sid
    for room_id, game in list(games.items()):
        for username in list(game.joueurs.keys()):
            if username in request.namespace.rooms:  # Vérifier si le joueur est dans la room
                game.gerer_deconnexion_temporaire(username)
                break

@socketio.on('ping_client')
def handle_ping():
    emit('pong_server')

@socketio.on('joueur_pret')
def handle_joueur_pret(data):
    room = data['room']
    username = data['username']
    
    if room not in games:
        return
        
    game = games[room]
    game.joueurs_prets.add(username)
    
    # Notifier tous les joueurs du changement d'état
    socketio.emit('update_joueurs_prets', {
        'joueurs_prets': list(game.joueurs_prets)
    }, room=room)
    
    # Si tous les joueurs sont prêts, démarrer la partie
    if game.verifier_tous_prets():
        game.distribuer_cartes()
        socketio.emit('partie_demarree', room=room)
        game.update_game_state()

@socketio.on('joueur_pas_pret')
def handle_joueur_pas_pret(data):
    room = data['room']
    username = data['username']
    
    if room not in games:
        return
        
    game = games[room]
    if username in game.joueurs_prets:
        game.joueurs_prets.remove(username)
    
    # Notifier tous les joueurs du changement d'état
    socketio.emit('update_joueurs_prets', {
        'joueurs_prets': list(game.joueurs_prets)
    }, room=room)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000) 