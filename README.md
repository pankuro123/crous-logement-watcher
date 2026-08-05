# Détecteur de logements CROUS

Ce programme surveille une recherche publique sur Trouver un logement CROUS.
Il mémorise les offres déjà présentes, puis envoie une notification Telegram
lorsqu'un nouveau logement apparaît. Il ne se connecte pas au compte CROUS et
ne réserve jamais automatiquement un logement.

Quand aucun logement n'est visible, il envoie aussi un message Telegram de
confirmation à chaque vérification (toutes les douze heures avec GitHub Actions).

## Lancer une vérification

```powershell
python .\crous_watcher.py --once
```

## Lancer la surveillance

```powershell
python .\crous_watcher.py --interval 300
```

`300` correspond à une vérification toutes les cinq minutes. Le programme
refuse un intervalle inférieur à 60 secondes afin de ne pas surcharger le site.

## Modifier la zone surveillée

Ouvre la recherche CROUS, ajuste la carte ou les filtres, copie l'URL de la
page de résultats, puis lance par exemple :

```powershell
python .\crous_watcher.py --url "COLLE_ICI_L_URL" --interval 300
```

## Notifications Telegram

Installe Telegram sur le téléphone, crée un bot via `@BotFather`, puis appuie
sur **Démarrer** et envoie un message à ce bot.
Dans le fichier `.env`, renseigne :

```text
TELEGRAM_BOT_TOKEN=ton_token_telegram
```

Ne partage jamais ce token et ne l'ajoute pas à Git.

Pour vérifier la notification :

```powershell
python .\crous_watcher.py --test-notification
```

## Exécution gratuite sur GitHub

Le dépôt contient une tâche GitHub Actions qui exécute une vérification toutes
les douze heures. Dans les réglages du dépôt GitHub, ajoute le secret suivant :

```text
TELEGRAM_BOT_TOKEN
```

avec le token de ton bot comme valeur. Le fichier d'état `data/crous_seen.json`
est automatiquement enregistré dans le dépôt seulement lorsqu'une offre change.
