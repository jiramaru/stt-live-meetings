# Transcription Meet (extension Chrome)

Transcrit automatiquement les appels Google Meet et, à la fin de l'appel :

1. crée un **Google Docs** avec la transcription (noms des intervenants, horodatage)
   dans un dossier de votre Drive ;
2. l'envoie par **e-mail** aux invités de l'événement Google Agenda, avec le lien
   vers le document, partagé en lecture seule.

La transcription s'appuie sur les **sous-titres de Meet** : les noms des
intervenants sont exacts, et il n'y a aucun serveur à héberger. Tout passe par le
compte Google de la personne qui a installé l'extension.

## Fonctionnement

| Moment | Ce que fait l'extension |
| --- | --- |
| Arrivée dans l'appel | Active les sous-titres, affiche « Transcription en cours » en haut de Meet |
| Pendant l'appel | Lit les sous-titres au fil de l'eau ; sauvegarde régulièrement (rien n'est perdu si l'onglet se ferme) |
| Fin de l'appel | Retrouve l'événement d'agenda (titre, invités), crée le document, le partage, envoie l'e-mail |

**Un seul envoi par réunion.** Par défaut, seule l'extension de l'**organisateur**
de l'événement envoie l'e-mail : si cinq membres de l'équipe sont dans l'appel, les
invités ne reçoivent pas cinq e-mails. Pour un appel sans événement d'agenda, une
copie est envoyée uniquement à la personne. Réglable dans les options.

## Mise en place (une seule fois, par un administrateur)

### 1. Projet Google Cloud

1. Ouvrir https://console.cloud.google.com et créer un projet (par exemple
   « Transcription Meet »).
2. **API et services > Bibliothèque** : activer **Google Calendar API**,
   **Google Drive API** et **Gmail API**.
3. **Écran de consentement OAuth** : type **Interne** (réservé aux comptes de votre
   organisation Google Workspace, pas de validation Google nécessaire). Ajouter les
   champs d'application :
   - `.../auth/calendar.events.readonly` (lire les événements : titre, invités)
   - `.../auth/drive.file` (uniquement les fichiers créés par l'extension)
   - `.../auth/gmail.send` (envoyer, sans accès en lecture à la boîte mail)
4. **Identifiants > Créer des identifiants > ID client OAuth**, type
   **Extension Chrome**, identifiant de l'extension :

   ```
   iflccfdecbhhhkjolcjejejjpjaonigb
   ```

   Cet identifiant est fixé par la clé publique (`key`) de `manifest.json`.
5. Copier l'ID client obtenu (`xxxx.apps.googleusercontent.com`) dans
   `manifest.json`, champ `oauth2.client_id`, à la place de
   `REMPLACER_PAR_VOTRE_CLIENT_ID...`.

La clé privée correspondante est dans `.extension-key` (non versionnée). Gardez-la :
elle est nécessaire pour publier une version qui garde le même identifiant.

### 2. Installer l'extension

**Pour tester** (sur un poste) : `chrome://extensions`, activer le **Mode
développeur**, **Charger l'extension non empaquetée**, choisir le dossier
`meet-extension`.

**Pour toute l'équipe** : publier l'extension sur le Chrome Web Store en
visibilité **Privée** (réservée à votre domaine), puis l'installer d'office depuis
la console d'administration Google (**Appareils > Chrome > Applications et
extensions**), ou envoyer le lien de la fiche à l'équipe.

### 3. Sur chaque poste

1. Cliquer sur l'icône de l'extension, puis sur l'icône des options, puis
   **Autoriser l'accès**.
2. Dans Meet : **Paramètres > Sous-titres > Langue : Français** (sinon Meet
   transcrit en anglais).

## Bonnes pratiques

- **Prévenez les participants** que la réunion est transcrite : contrairement à
  l'enregistrement natif de Meet, l'extension n'est pas signalée aux autres
  participants.
- La qualité dépend des sous-titres de Google : bonne pour un français clair,
  moins bonne pour les noms propres, les sigles et les interventions simultanées.

## Limites connues

- **Dépend de l'interface de Meet.** Meet n'offre pas d'API pour les sous-titres :
  l'extension les lit dans la page. Si Google modifie l'interface, la lecture peut
  cesser de fonctionner. Tout ce qui dépend de la page est regroupé dans l'objet
  `MEET` de `src/content.js`, pour une correction rapide.
- Il faut que la personne qui a l'extension reste dans l'appel jusqu'à la fin pour
  avoir la transcription complète.
- Les sous-titres de Meet ne couvrent qu'une langue à la fois.

## Développement

```bash
cd meet-extension
node --test test/*.test.js
```

| Fichier | Rôle |
| --- | --- |
| `src/content.js` | Dans Meet : détecte l'appel, active et lit les sous-titres |
| `src/transcript.js` | Logique pure : assemblage des sous-titres, formats, e-mail (testée) |
| `src/background.js` | Sauvegarde pendant l'appel, livraison à la fin |
| `src/google.js` | Appels aux API Agenda, Drive et Gmail |
| `options.html`, `popup.html` | Réglages et historique des transcriptions |
