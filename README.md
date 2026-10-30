# Job-Radar

Sucht jeden Morgen gegen 5 bis 6 Uhr neue Stellen in Commercial Excellence, Sales Operations und Revenue Operations im Raum München und legt die neuen Treffer als GitHub-Issue an. GitHub schickt dir dazu automatisch eine E-Mail.

## Einrichtung (ca. 10 Minuten)

1. Auf github.com ein neues Repository anlegen, Sichtbarkeit **Private** (sonst sieht jeder deine Jobsuche).
2. Den Inhalt dieses Ordners hochladen, inklusive des versteckten Ordners `.github`. Am einfachsten per Terminal:
   ```
   cd job-radar
   git init && git add . && git commit -m "Job-Radar"
   git branch -M main
   git remote add origin https://github.com/<dein-user>/job-radar.git
   git push -u origin main
   ```
3. Im Repo unter **Settings > Actions > General > Workflow permissions** "Read and write permissions" aktivieren.
4. Unter **Actions > Job-Radar > Run workflow** einmal manuell starten und prüfen, ob ein Issue entsteht.
5. Oben rechts im Repo **Watch > All Activity** wählen, damit die Issues per Mail kommen.

## Anpassen

Alles steht in `config.yaml`: Suchbegriffe, Umkreis, Mindestgehalt, Wunscharbeitgeber, ausgeschlossene Arbeitgeber und Ausschlussbegriffe. Weitere Unternehmen mit Greenhouse-, Lever- oder Personio-Karriereseite trägst du unter `ats_boards` ein; den Token findest du in der URL der Karriereseite.

## Gut zu wissen

- Quellen sind die offizielle Jobbörse der Bundesagentur für Arbeit und öffentliche Schnittstellen von Greenhouse, Lever und Personio. Indeed, StepStone und LinkedIn werden bewusst nicht abgefragt, deren Nutzungsbedingungen verbieten automatisiertes Auslesen.
- Der Gehaltsfilter greift nur bei Anzeigen mit Gehaltsangabe. Die meisten Konzernanzeigen nennen keins, sie erscheinen mit "nicht angegeben".
- Jede Stelle wird nur einmal gemeldet (`data/seen.json`).
- GitHub startet geplante Läufe manchmal mit Verzögerung. Durch den täglichen Commit bleibt der Zeitplan aktiv; GitHub pausiert ihn sonst nach 60 Tagen ohne Aktivität.
