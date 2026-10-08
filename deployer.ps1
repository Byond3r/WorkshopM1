# Copie les fichiers de pi/ sur le Raspberry et redémarre les services.
# À lancer depuis la racine du projet, PC connecté au Wi-Fi Ultron :
#   powershell -ExecutionPolicy Bypass -File deployer.ps1

$PI = "user@10.42.0.1"
$DOSSIER_PI = "/home/user/sentinel/"
$FICHIERS = @(
    "pi/serveur.py", "pi/dashboard.html", "pi/capture_reseau.py", "pi/capteurs_pi.py",
    "pi/sentinel.service", "pi/sentinel-reseau.service", "pi/installer.sh", "pi/durcir.sh",
    "pi/generer_certificat.sh"
)

Write-Host "Copie des fichiers vers $PI ..."
scp $FICHIERS "${PI}:${DOSSIER_PI}"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Copie impossible : le PC est-il bien connecte a Ultron ? (ping 10.42.0.1)"
    exit 1
}

Write-Host "Redemarrage des services ..."
# sentinel-reseau n'est redemarre que s'il a deja ete installe (sinon on l'ignore)
# -t : ouvre un vrai terminal, pour que sudo puisse demander le mot de passe
ssh -t $PI "sudo systemctl restart sentinel; systemctl is-enabled --quiet sentinel-reseau && sudo systemctl restart sentinel-reseau; systemctl is-active sentinel sentinel-reseau"
