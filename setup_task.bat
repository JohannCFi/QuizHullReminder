@echo off
echo Configuration de la tache planifiee Hull Quiz Reminder...
echo.

schtasks /create /tn "HullQuizReminder" /tr "python \"%~dp0notify.py\"" /sc daily /st 08:00 /f

if %errorlevel% == 0 (
    echo.
    echo Tache creee avec succes !
    echo La notification Telegram sera envoyee chaque jour a 08:00.
    echo.
    echo Pour modifier l'heure, utilisez le Planificateur de taches Windows
    echo ou relancez ce script apres l'avoir modifie.
) else (
    echo.
    echo Erreur lors de la creation de la tache.
    echo Essayez de relancer ce script en tant qu'administrateur.
)

echo.
pause
