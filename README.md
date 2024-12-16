# Poker Multijoueurs

Un jeu de poker en ligne multijoueurs développé avec Python, Flask et Socket.IO.

## Fonctionnalités

- Création de tables de poker
- Support jusqu'à 6 joueurs par table
- Interface en temps réel
- Système de mises et de blinds
- Évaluation automatique des mains
- Notifications des actions des joueurs
- Historique des mains jouées
- Cartes rouges pour les coeurs (♥) et carreaux (♦)

## Installation

1. Clonez le dépôt :
```bash
git clone https://github.com/Rekiiel/Poker.git
cd Poker
```

2. Installez les dépendances :
```bash
pip install -r requirements.txt
```

3. Lancez le serveur :
```bash
python main.py
```

4. Ouvrez votre navigateur et accédez à `http://localhost:5000`

## Comment jouer

1. Entrez votre nom et créez une table ou rejoignez une table existante
2. Attendez qu'au moins un autre joueur rejoigne la table
3. Cliquez sur "Démarrer la partie"
4. Jouez votre tour quand c'est à vous :
   - Suivre : pour égaler la mise actuelle
   - Miser : pour augmenter la mise
   - Se coucher : pour abandonner la main

## Règles du jeu

- La petite blind est de 10€
- La grande blind est de 20€
- Chaque joueur commence avec 1000€
- Les mains sont évaluées automatiquement
- Les combinaisons possibles (de la plus forte à la plus faible) :
  1. Quinte Flush Royale
  2. Quinte Flush
  3. Carré
  4. Full House
  5. Couleur
  6. Quinte
  7. Brelan
  8. Deux Paires
  9. Paire
  10. Carte Haute

## Technologies utilisées

- Backend :
  - Python 3
  - Flask
  - Flask-SocketIO
  - Eventlet
- Frontend :
  - HTML5
  - CSS3
  - JavaScript
  - Socket.IO client

## Auteur

- Rekiel

## Licence

MIT License 